"""Multi-threaded video stream reader with bounded RingBuffer & backpressure."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import re
import threading
import time
from typing import Deque, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_NATURAL_TOKEN_RE = re.compile(r"(\d+)")


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
    """Detect native FPS of an image-sequence directory or OpenCV video file."""
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
    ) -> None:
        self.source = source
        self.buffer = RingBuffer(capacity=buffer_capacity, drop_policy=drop_policy)
        self.target_fps = float(target_fps) if target_fps is not None and float(target_fps) > 0.0 else None
        self.loop_synthetic = loop_synthetic
        self.max_cached_frames = max(0, int(max_cached_frames))

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

        src = str(self.source) if isinstance(self.source, Path) else self.source
        cap = cv2.VideoCapture(src)
        try:
            if cap.isOpened():
                cap_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                cap_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                cap_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                self._update_frame_meta(
                    width=cap_w,
                    height=cap_h,
                    fps=(cap_fps if cap_fps > 0.0 and not np.isnan(cap_fps) else (self.target_fps or 30.0)),
                )
                if cap_fps > 0.0 and not np.isnan(cap_fps):
                    with self._meta_lock:
                        self._source_fps = cap_fps
            while not self._stop_event.is_set() and cap.isOpened():
                t0 = time.perf_counter()
                ok, frame = cap.read()
                decode_ms = (time.perf_counter() - t0) * 1000.0
                if not ok or frame is None:
                    break
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
            cap.release()
            self._eof = True
            self.buffer.close()
