"""Multi-threaded video stream reader with bounded RingBuffer & backpressure."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qs, urlparse
import numpy as np


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_NETWORK_PREFIXES = ("rtsp://", "rtsps://", "rtmp://", "http://", "https://")
_NATURAL_TOKEN_RE = re.compile(r"(\d+)")


def parse_screen_source(uri: Any) -> Optional[Dict[str, Any]]:
    """Parse 'screen://0' or 'screen://0?x=0&y=0&w=1280&h=720' into a dict, or None."""
    if not isinstance(uri, str):
        return None
    s = uri.strip()
    if not s.lower().startswith("screen://"):
        return None

    parsed = urlparse(s)
    mon_raw = (parsed.netloc or parsed.path.lstrip("/")).strip()
    if not mon_raw:
        monitor = 0
    elif mon_raw.isdigit():
        monitor = int(mon_raw)
    else:
        return None

    qs = parse_qs(parsed.query, keep_blank_values=False)

    def _opt_int(key: str) -> Optional[int]:
        vals = qs.get(key)
        if not vals:
            return None
        return int(float(vals[0]))

    try:
        x = _opt_int("x")
        y = _opt_int("y")
        w = _opt_int("w")
        h = _opt_int("h")
    except (TypeError, ValueError):
        return None

    if (w is not None and w <= 0) or (h is not None and h <= 0):
        return None

    return {"monitor": monitor, "x": x, "y": y, "w": w, "h": h}


def parse_camera_source(uri: Any) -> Optional[int]:
    """Parse 'camera://0', '0', or non-negative int 0 into camera index int, or None."""
    if isinstance(uri, bool):
        return None
    if isinstance(uri, int):
        return uri if uri >= 0 else None
    if not isinstance(uri, str):
        return None

    s = uri.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s.lower().startswith("camera://"):
        rest = s[9:].split("?", 1)[0].strip().strip("/")
        if rest.isdigit():
            return int(rest)
    return None


def _is_network_stream_source(source: Any) -> bool:
    return isinstance(source, str) and source.strip().lower().startswith(_NETWORK_PREFIXES)


def is_live_video_source(source: Any) -> bool:
    """Return True for webcam, screen:// capture, or rtsp/rtmp/http(s) live streams."""
    return (
        parse_camera_source(source) is not None
        or parse_screen_source(source) is not None
        or _is_network_stream_source(source)
    )


def _natural_sort_key(path: Path) -> Tuple[Tuple[int, Union[int, str]], ...]:
    """Build a type-safe natural sort key so e.g. 000002.jpg < 000010.jpg."""
    parts = _NATURAL_TOKEN_RE.split(path.name)
    key: List[Tuple[int, Union[int, str]]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _is_directory_source(source: object) -> bool:
    if not isinstance(source, (str, Path)):
        return False
    if isinstance(source, str) and "://" in source:
        return False
    try:
        return Path(source).is_dir()
    except OSError:
        return False


def _detect_dir_seqinfo_meta(dir_path: Path) -> Tuple[int, int, float]:
    """Read optional MOTChallenge-style ../seqinfo.ini metadata for natural image dirs."""
    ini_path = dir_path.parent / "seqinfo.ini"
    w, h, fps = 0, 0, 0.0
    if ini_path.is_file():
        try:
            for line in ini_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" not in line:
                    continue
                k, v = [part.strip() for part in line.split("=", 1)]
                kl = k.lower()
                if kl == "framerate":
                    fps = float(v)
                elif kl == "imwidth":
                    w = int(v)
                elif kl == "imheight":
                    h = int(v)
        except Exception:
            pass
    return w, h, fps


def detect_source_native_fps(source: Union[int, str, Path], default_fps: float = 30.0) -> float:
    """Detect native FPS of screen/camera/stream, image-sequence directory, or video file."""
    if parse_screen_source(source) is not None:
        return 60.0
    if parse_camera_source(source) is not None:
        return 30.0
    if _is_network_stream_source(source):
        return float(default_fps)
    if _is_directory_source(source):
        _, _, fps = _detect_dir_seqinfo_meta(Path(source))  # type: ignore[arg-type]
        return float(fps) if fps > 0.0 else float(default_fps)
    if isinstance(source, (str, Path)):
        src_str = str(source)
        if "://" in src_str:
            return float(default_fps)
        try:
            import cv2  # lazy import

            cap = cv2.VideoCapture(src_str)
            try:
                if cap.isOpened():
                    cap_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    if cap_fps > 0.0 and not np.isnan(cap_fps):
                        return cap_fps
            finally:
                cap.release()
        except Exception:
            pass
    return float(default_fps)


def _create_screen_grabber(
    screen_cfg: Dict[str, Any],
) -> Tuple[Callable[[], Optional[np.ndarray]], Callable[[], None]]:
    """Create fastest available screen capture backend: dxcam -> mss -> PIL.ImageGrab."""
    monitor = max(0, int(screen_cfg.get("monitor", 0)))
    rx = screen_cfg.get("x")
    ry = screen_cfg.get("y")
    rw = screen_cfg.get("w")
    rh = screen_cfg.get("h")
    has_region = rw is not None and rh is not None and int(rw) > 0 and int(rh) > 0

    # 1. Try dxcam (DXGI GPU Desktop Duplication)
    try:
        import dxcam  # type: ignore

        cam = None
        try:
            cam = dxcam.create(output_idx=monitor, output_color="BGR")
        except Exception:
            if monitor != 0:
                cam = dxcam.create(output_idx=0, output_color="BGR")
        if cam is not None:
            region: Optional[Tuple[int, int, int, int]] = None
            if has_region:
                cam_w = int(getattr(cam, "width", 0) or 0)
                cam_h = int(getattr(cam, "height", 0) or 0)
                left = max(0, int(rx or 0))
                top = max(0, int(ry or 0))
                right = left + int(rw)
                bottom = top + int(rh)
                if cam_w > 0 and cam_h > 0:
                    left = min(left, max(0, cam_w - 1))
                    top = min(top, max(0, cam_h - 1))
                    right = max(left + 1, min(right, cam_w))
                    bottom = max(top + 1, min(bottom, cam_h))
                region = (left, top, right, bottom)

            last_frame: Optional[np.ndarray] = None
            warmup_frame = None
            for _ in range(3):
                warmup_frame = cam.grab(region=region) if region else cam.grab()
                if warmup_frame is not None:
                    break
                time.sleep(0.005)
            if warmup_frame is not None:
                last_frame = np.ascontiguousarray(warmup_frame[:, :, :3])

                def _grab_dxcam() -> Optional[np.ndarray]:
                    nonlocal last_frame
                    fr = cam.grab(region=region) if region else cam.grab()
                    if fr is not None:
                        last_frame = np.ascontiguousarray(fr[:, :, :3])
                    return last_frame

                def _release_dxcam() -> None:
                    try:
                        cam.release()
                    except Exception:
                        pass

                return _grab_dxcam, _release_dxcam
            try:
                cam.release()
            except Exception:
                pass
    except Exception:
        pass

    # 2. Try mss
    try:
        import mss  # type: ignore

        sct = mss.mss()
        monitors = sct.monitors
        if len(monitors) > 1:
            mon_idx = min(monitor, len(monitors) - 2) + 1
        else:
            mon_idx = 0
        base_mon = monitors[mon_idx]
        if has_region:
            left = int(base_mon["left"]) + max(0, int(rx or 0))
            top = int(base_mon["top"]) + max(0, int(ry or 0))
            max_right = int(base_mon["left"]) + int(base_mon["width"])
            max_bottom = int(base_mon["top"]) + int(base_mon["height"])
            left = min(left, max_right - 1)
            top = min(top, max_bottom - 1)
            mss_region = {
                "left": left,
                "top": top,
                "width": max(1, min(int(rw), max_right - left)),
                "height": max(1, min(int(rh), max_bottom - top)),
            }
        else:
            mss_region = base_mon

        def _grab_mss() -> Optional[np.ndarray]:
            shot = sct.grab(mss_region)
            arr = np.asarray(shot, dtype=np.uint8)
            if arr.ndim != 3 or arr.shape[2] < 3:
                return None
            return np.ascontiguousarray(arr[:, :, :3])

        def _release_mss() -> None:
            try:
                sct.close()
            except Exception:
                pass

        return _grab_mss, _release_mss
    except Exception:
        pass

    # 3. Fallback to built-in PIL.ImageGrab
    from PIL import ImageGrab

    bbox: Optional[Tuple[int, int, int, int]] = None
    if has_region:
        left = max(0, int(rx or 0)) if monitor == 0 else int(rx or 0)
        top = max(0, int(ry or 0)) if monitor == 0 else int(ry or 0)
        bbox = (left, top, left + int(rw), top + int(rh))

    def _grab_pil() -> Optional[np.ndarray]:
        if monitor > 0:
            try:
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
            except TypeError:
                img = ImageGrab.grab(bbox=bbox)
        else:
            img = ImageGrab.grab(bbox=bbox)
        arr = np.asarray(img, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None
        return np.ascontiguousarray(arr[:, :, :3][:, :, ::-1])

    return _grab_pil, (lambda: None)


@dataclass
class FramePacket:
    """Single decoded video frame with timing metadata."""

    frame_id: int
    timestamp: float
    frame: np.ndarray
    decode_ms: float = 0.0


class RingBuffer:
    """Thread-safe bounded circular buffer with backpressure frame dropping."""

    def __init__(self, capacity: int = 32, drop_policy: str = "oldest") -> None:
        if capacity <= 0:
            raise ValueError("RingBuffer capacity must be positive.")
        if drop_policy not in ("oldest", "newest", "block"):
            raise ValueError("drop_policy must be 'oldest', 'newest', or 'block'.")
        self._capacity = int(capacity)
        self._drop_policy = drop_policy
        self._buf: Deque[FramePacket] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)
        self._dropped_frames: int = 0
        self._total_pushed: int = 0
        self._closed: bool = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buf)

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def total_pushed(self) -> int:
        with self._lock:
            return self._total_pushed

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()
            self._not_full.notify_all()

    def reset(self) -> None:
        with self._lock:
            self._buf.clear()
            self._dropped_frames = 0
            self._total_pushed = 0
            self._closed = False
            self._not_full.notify_all()

    def put(self, item: FramePacket) -> bool:
        """Push a FramePacket into the ring buffer.

        If the buffer is full, drops either the oldest or newest frame depending
        on drop_policy and increments dropped_frames, or blocks until space is
        available when drop_policy == 'block'.

        Returns:
            True if the item was enqueued, False if dropped (newest policy) or closed.
        """
        with self._lock:
            if self._drop_policy == "block":
                while len(self._buf) >= self._capacity and not self._closed:
                    self._not_full.wait()
            if self._closed:
                return False
            self._total_pushed += 1
            if len(self._buf) >= self._capacity:
                self._dropped_frames += 1
                if self._drop_policy == "oldest":
                    self._buf.popleft()
                    self._buf.append(item)
                    self._not_empty.notify()
                    return True
                return False
            self._buf.append(item)
            self._not_empty.notify()
            return True

    def get(self, timeout: Optional[float] = None) -> Optional[FramePacket]:
        """Pop the oldest FramePacket, waiting up to timeout seconds if empty."""
        with self._lock:
            if not self._buf and not self._closed and (timeout is None or timeout > 0):
                self._not_empty.wait(timeout=timeout)
            if not self._buf:
                return None
            item = self._buf.popleft()
            self._not_full.notify()
            return item

    def get_batch(
        self, max_batch: int = 4, timeout: float = 0.05
    ) -> List[FramePacket]:
        """Pop up to max_batch FramePackets with brief coalesce for GPU batching."""
        batch: List[FramePacket] = []
        with self._lock:
            if not self._buf and not self._closed and timeout > 0:
                self._not_empty.wait(timeout=timeout)
            # Brief opportunistic coalesce so high-FPS producers fill micro-batches cleanly
            if 0 < len(self._buf) < max_batch and not self._closed and timeout > 0:
                coalesce = min(float(timeout), 0.002)
                if coalesce > 0:
                    self._not_empty.wait(timeout=coalesce)
            while self._buf and len(batch) < max_batch:
                batch.append(self._buf.popleft())
            if batch:
                self._not_full.notify_all()
        return batch


class VideoStreamReader:
    """Multi-threaded video capture pushing frames into a bounded RingBuffer.

    Supports OpenCV video files/RTSP/camera indices, natural-sorted image-sequence
    directories (.jpg/.jpeg/.png) with RAM caching + parallel prefetch, and in-memory
    frame sequences. Passing target_fps=None or <= 0 leaves capture uncapped (300+ FPS ready).
    """

    def __init__(
        self,
        source: Union[int, str, Path, Sequence[np.ndarray]],
        buffer_capacity: int = 64,
        drop_policy: str = "oldest",
        target_fps: Optional[float] = None,
        loop_synthetic: bool = False,
        max_cached_frames: int = 300,
        reconnect_backoff: float = 0.15,
        max_reconnects: int = 6,
    ) -> None:
        self.source = source
        self.buffer = RingBuffer(capacity=buffer_capacity, drop_policy=drop_policy)
        self.target_fps = float(target_fps) if target_fps is not None and float(target_fps) > 0.0 else None
        self.loop_synthetic = loop_synthetic
        self.max_cached_frames = max(0, int(max_cached_frames))
        self._reconnect_backoff = max(0.01, float(reconnect_backoff))
        self._max_reconnects = max(0, int(max_reconnects))

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._eof = False
        self._total_frames = 0
        self._start_time = 0.0

        self._meta_lock = threading.Lock()
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._source_fps: float = float(self.target_fps) if self.target_fps else 0.0
        if isinstance(source, (list, tuple)) and len(source) > 0:
            first = np.asarray(source[0])
            if first.ndim >= 2:
                self._frame_height = int(first.shape[0])
                self._frame_width = int(first.shape[1])
            if self._source_fps <= 0.0:
                self._source_fps = 30.0
        elif parse_screen_source(source) is not None:
            if self._source_fps <= 0.0:
                self._source_fps = 60.0
        elif parse_camera_source(source) is not None or _is_network_stream_source(source):
            if self._source_fps <= 0.0:
                self._source_fps = 30.0
        elif _is_directory_source(source):
            sw, sh, sfps = _detect_dir_seqinfo_meta(Path(source))  # type: ignore[arg-type]
            if sw > 0:
                self._frame_width = sw
            if sh > 0:
                self._frame_height = sh
            if sfps > 0.0 and self._source_fps <= 0.0:
                self._source_fps = sfps
            if self._source_fps <= 0.0:
                self._source_fps = 30.0

    @property
    def frame_width(self) -> int:
        with self._meta_lock:
            return self._frame_width

    @property
    def frame_height(self) -> int:
        with self._meta_lock:
            return self._frame_height

    @property
    def source_fps(self) -> float:
        with self._meta_lock:
            return self._source_fps

    def _update_frame_meta(
        self,
        frame: Optional[np.ndarray] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[float] = None,
    ) -> None:
        with self._meta_lock:
            if frame is not None and frame.ndim >= 2:
                if self._frame_height <= 0:
                    self._frame_height = int(frame.shape[0])
                if self._frame_width <= 0:
                    self._frame_width = int(frame.shape[1])
            if width is not None and width > 0 and self._frame_width <= 0:
                self._frame_width = int(width)
            if height is not None and height > 0 and self._frame_height <= 0:
                self._frame_height = int(height)
            if fps is not None and fps > 0.0 and not np.isnan(fps):
                if self._source_fps <= 0.0:
                    self._source_fps = float(fps)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_eof(self) -> bool:
        return self._eof and self.buffer.size == 0

    @property
    def stats(self) -> Dict[str, float | int]:
        elapsed = max(time.perf_counter() - self._start_time, 1e-6) if self._start_time else 1e-6
        return {
            "total_frames": self._total_frames,
            "dropped_frames": self.buffer.dropped_frames,
            "buffer_size": self.buffer.size,
            "buffer_capacity": self.buffer.capacity,
            "capture_fps": self._total_frames / elapsed if self._start_time else 0.0,
        }

    def start(self) -> "VideoStreamReader":
        if self.is_running:
            return self
        self._stop_event.clear()
        self._eof = False
        self._total_frames = 0
        self._start_time = time.perf_counter()
        self.buffer.reset()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self.buffer.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> "VideoStreamReader":
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    def _wait_interval(self, t0: float, frame_interval: float) -> None:
        if frame_interval > 0.0:
            elapsed = time.perf_counter() - t0
            remaining = frame_interval - elapsed
            if remaining > 0.0:
                self._stop_event.wait(timeout=remaining)

    def _capture_loop(self) -> None:
        frame_interval = (1.0 / self.target_fps) if self.target_fps and self.target_fps > 0 else 0.0

        if isinstance(self.source, (list, tuple)):
            idx = 0
            n = len(self.source)
            while not self._stop_event.is_set() and n > 0:
                t0 = time.perf_counter()
                frame = np.asarray(self.source[idx])
                self._update_frame_meta(frame=frame, fps=self.target_fps or 30.0)
                self._total_frames += 1
                decode_ms = (time.perf_counter() - t0) * 1000.0
                self.buffer.put(
                    FramePacket(
                        frame_id=self._total_frames,
                        timestamp=time.perf_counter(),
                        frame=frame,
                        decode_ms=decode_ms,
                    )
                )
                idx += 1
                if idx >= n:
                    if self.loop_synthetic:
                        idx = 0
                    else:
                        break
                self._wait_interval(t0, frame_interval)
            self._eof = True
            self.buffer.close()
            return

        screen_cfg = parse_screen_source(self.source)
        if screen_cfg is not None:
            grab_fn = None
            release_fn = None
            try:
                grab_fn, release_fn = _create_screen_grabber(screen_cfg)
                consecutive_errors = 0
                while not self._stop_event.is_set():
                    t0 = time.perf_counter()
                    try:
                        frame = grab_fn()
                    except Exception:
                        frame = None
                    decode_ms = (time.perf_counter() - t0) * 1000.0
                    if frame is None:
                        consecutive_errors += 1
                        if consecutive_errors >= 5:
                            break
                        self._stop_event.wait(timeout=0.02)
                        continue
                    consecutive_errors = 0
                    self._update_frame_meta(frame=frame, fps=self.target_fps or 60.0)
                    self._total_frames += 1
                    self.buffer.put(
                        FramePacket(
                            frame_id=self._total_frames,
                            timestamp=time.perf_counter(),
                            frame=frame,
                            decode_ms=decode_ms,
                        )
                    )
                    self._wait_interval(t0, frame_interval)
            except Exception:
                pass
            finally:
                if release_fn is not None:
                    release_fn()
                self._eof = True
                self.buffer.close()
            return

        import cv2  # lazy import so pure-unit environments stay lightweight

        if _is_directory_source(self.source):
            dir_path = Path(self.source)  # type: ignore[arg-type]
            image_paths = sorted(
                (
                    p
                    for p in dir_path.iterdir()
                    if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
                ),
                key=_natural_sort_key,
            )
            idx = 0
            n = len(image_paths)
            read_any = False
            frame_cache: Dict[int, np.ndarray] = {}
            prefetch_ahead = 4
            with ThreadPoolExecutor(max_workers=2) as decode_pool:
                inflight: Dict[int, Future] = {}

                def _schedule_prefetch(start_pos: int) -> None:
                    if n <= 0:
                        return
                    limit = min(prefetch_ahead, n)
                    for step in range(limit):
                        p_idx = (start_pos + step) % n
                        if p_idx not in frame_cache and p_idx not in inflight:
                            if not self.loop_synthetic and (start_pos + step) >= n:
                                break
                            inflight[p_idx] = decode_pool.submit(
                                cv2.imread, str(image_paths[p_idx])
                            )

                while not self._stop_event.is_set() and n > 0:
                    t0 = time.perf_counter()
                    from_cache = idx in frame_cache
                    if from_cache:
                        frame = frame_cache[idx]
                        decode_ms = (time.perf_counter() - t0) * 1000.0
                    else:
                        fut = inflight.pop(idx, None)
                        if fut is not None:
                            frame = fut.result()
                        else:
                            frame = cv2.imread(str(image_paths[idx]))
                        decode_ms = (time.perf_counter() - t0) * 1000.0
                        if frame is not None and len(frame_cache) < self.max_cached_frames:
                            frame_cache[idx] = frame

                    _schedule_prefetch((idx + 1) % n if self.loop_synthetic else idx + 1)

                    if frame is not None:
                        read_any = True
                        self._update_frame_meta(frame=frame, fps=self.target_fps or 30.0)
                        self._total_frames += 1
                        if (
                            from_cache
                            and frame_interval <= 0.0
                            and self.buffer.size >= self.buffer.capacity
                        ):
                            self._stop_event.wait(timeout=0.001)
                        self.buffer.put(
                            FramePacket(
                                frame_id=self._total_frames,
                                timestamp=time.perf_counter(),
                                frame=frame,
                                decode_ms=decode_ms,
                            )
                        )
                    idx += 1
                    if idx >= n:
                        if self.loop_synthetic and read_any:
                            idx = 0
                            read_any = False
                        else:
                            break
                    if frame is not None and frame_interval > 0.0:
                        self._wait_interval(t0, frame_interval)
            self._eof = True
            self.buffer.close()
            return

        cam_idx = parse_camera_source(self.source)
        is_network = _is_network_stream_source(self.source)
        is_live = (cam_idx is not None) or is_network
        src = str(self.source) if isinstance(self.source, Path) else self.source

        def _open_capture():
            if cam_idx is not None:
                if sys.platform.startswith("win") and hasattr(cv2, "CAP_DSHOW"):
                    try:
                        c = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
                        if c.isOpened():
                            return c
                        c.release()
                    except TypeError:
                        pass
                return cv2.VideoCapture(cam_idx)
            return cv2.VideoCapture(src)

        def _sync_cap_meta(c) -> None:
            if c is None or not c.isOpened():
                return
            cap_w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            cap_h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            cap_fps = float(c.get(cv2.CAP_PROP_FPS) or 0.0)
            self._update_frame_meta(
                width=cap_w,
                height=cap_h,
                fps=(
                    cap_fps
                    if cap_fps > 0.0 and not np.isnan(cap_fps)
                    else (self.target_fps or 30.0)
                ),
            )
            if cap_fps > 0.0 and not np.isnan(cap_fps):
                with self._meta_lock:
                    self._source_fps = cap_fps

        cap = _open_capture()
        _sync_cap_meta(cap)
        read_any = False
        transient_drops = 0
        reconnect_attempts = 0
        backoff = self._reconnect_backoff
        try:
            while not self._stop_event.is_set():
                if cap is None or not cap.isOpened():
                    if is_network and reconnect_attempts < self._max_reconnects:
                        self._stop_event.wait(timeout=backoff)
                        if self._stop_event.is_set():
                            break
                        backoff = min(3.0, backoff * 1.5)
                        reconnect_attempts += 1
                        if cap is not None:
                            cap.release()
                        cap = _open_capture()
                        _sync_cap_meta(cap)
                        continue
                    break

                t0 = time.perf_counter()
                ok, frame = cap.read()
                decode_ms = (time.perf_counter() - t0) * 1000.0
                if not ok or frame is None:
                    if is_network:
                        transient_drops += 1
                        if transient_drops < 3:
                            self._stop_event.wait(timeout=0.02)
                            continue
                        transient_drops = 0
                        cap.release()
                        cap = None
                        if reconnect_attempts < self._max_reconnects:
                            self._stop_event.wait(timeout=backoff)
                            if self._stop_event.is_set():
                                break
                            backoff = min(3.0, backoff * 1.5)
                            reconnect_attempts += 1
                            cap = _open_capture()
                            _sync_cap_meta(cap)
                            continue
                        break
                    if cam_idx is not None:
                        transient_drops += 1
                        if transient_drops < 5:
                            self._stop_event.wait(timeout=0.02)
                            continue
                        break
                    if self.loop_synthetic and not is_live and read_any:
                        rewound = cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        if rewound:
                            ok, frame = cap.read()
                        if not rewound or not ok or frame is None:
                            cap.release()
                            cap = _open_capture()
                            if cap is not None and cap.isOpened():
                                ok, frame = cap.read()
                        if not ok or frame is None:
                            break
                        decode_ms = (time.perf_counter() - t0) * 1000.0
                    else:
                        break

                if self._stop_event.is_set():
                    break

                read_any = True
                transient_drops = 0
                reconnect_attempts = 0
                backoff = self._reconnect_backoff
                self._update_frame_meta(frame=frame, fps=self.target_fps or 30.0)
                self._total_frames += 1
                self.buffer.put(
                    FramePacket(
                        frame_id=self._total_frames,
                        timestamp=time.perf_counter(),
                        frame=frame,
                        decode_ms=decode_ms,
                    )
                )
                self._wait_interval(t0, frame_interval)
        finally:
            if cap is not None:
                cap.release()
            self._eof = True
            self.buffer.close()
