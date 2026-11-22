"""Real-time video tracking stream engine backed by core ByteTracker and pipeline scheduler."""

from __future__ import annotations

import asyncio
from collections import deque
import math
from pathlib import Path
import queue
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Set, Tuple, Union

import cv2
import numpy as np

from backend.db import db
from backend.schemas import (
    DetectorType,
    PerStageLatency,
    StreamConfig,
    SystemMetrics,
    TaskRead,
    TaskStatus,
    TaskUpdate,
    TrackFrame,
    TrackItem,
    ViolationEvent,
    ViolationType,
)
from core.byte_tracker import ByteTracker, ByteTrackerConfig
from core.detector_adapter import (
    Detection,
    DetectorAdapter,
    MockDetectorAdapter,
    create_detector_adapter,
)
from core.trajectory_pool import TrackState
from pipeline.batch_scheduler import (
    AdaptiveKeyframeConfig,
    BatchInferenceScheduler,
    PipelineResult,
)
from pipeline.rules_engine import CampusRoadRulesEngine
from pipeline.video_stream import VideoStreamReader, detect_source_native_fps

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DEMO_CACHE_LOCK = threading.Lock()
_DEMO_FRAMES_CACHE: Optional[Tuple[List[np.ndarray], Dict[int, List[Detection]]]] = None


def _build_demo_campus_background(width: int = 1280, height: int = 720) -> np.ndarray:
    """Render a deterministic 1280x720 campus road background for offline mock demos."""
    canvas = np.full((height, width, 3), (38, 44, 52), dtype=np.uint8)

    # Top lawn / courtyard area
    cv2.rectangle(canvas, (0, 0), (width, 220), (42, 68, 48), thickness=-1)
    # Restricted courtyard zone outline matching default seeded zone
    cv2.rectangle(canvas, (480, 60), (820, 210), (54, 88, 60), thickness=-1)
    cv2.rectangle(canvas, (480, 60), (820, 210), (70, 140, 95), thickness=2)

    # Upper sidewalk
    cv2.rectangle(canvas, (0, 220), (width, 260), (84, 88, 94), thickness=-1)

    # Two-lane campus asphalt road (y=260..460)
    cv2.rectangle(canvas, (0, 260), (width, 460), (50, 54, 60), thickness=-1)
    cv2.line(canvas, (0, 260), (width, 260), (210, 214, 220), thickness=2)
    cv2.line(canvas, (0, 460), (width, 460), (210, 214, 220), thickness=2)

    # Dashed yellow lane divider at y=360
    for x in range(20, width, 64):
        cv2.line(canvas, (x, 360), (min(width, x + 36), 360), (60, 200, 235), thickness=2)

    # Crosswalk stripes near x=130
    for y in range(272, 452, 22):
        cv2.rectangle(canvas, (115, y), (165, y + 10), (185, 190, 196), thickness=-1)

    # Subtle lane direction arrows
    for ax in (320, 680, 1020):
        cv2.arrowedLine(canvas, (ax - 35, 310), (ax + 35, 310), (120, 130, 140), 2, tipLength=0.25)
        cv2.arrowedLine(canvas, (ax + 35, 410), (ax - 35, 410), (120, 130, 140), 2, tipLength=0.25)

    # Bottom pedestrian plaza
    cv2.rectangle(canvas, (0, 460), (width, height), (66, 72, 80), thickness=-1)
    cv2.rectangle(canvas, (480, 470), (740, 620), (76, 82, 92), thickness=-1)

    # Prominent offline demo simulation banner
    cv2.rectangle(canvas, (16, 14), (585, 50), (24, 34, 48), thickness=-1)
    cv2.rectangle(canvas, (16, 14), (585, 50), (45, 175, 245), thickness=2)
    cv2.putText(
        canvas,
        "[OFFLINE DEMO SIMULATION - MOCK DETECTOR]",
        (28, 39),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.68,
        (90, 215, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def _get_demo_scenario_assets(
    num_frames: int = 180,
) -> Tuple[List[np.ndarray], Dict[int, List[Detection]]]:
    """Build or return cached 1280x720 looping demo frames and matching detections."""
    global _DEMO_FRAMES_CACHE
    with _DEMO_CACHE_LOCK:
        if _DEMO_FRAMES_CACHE is not None and len(_DEMO_FRAMES_CACHE[0]) == num_frames:
            return _DEMO_FRAMES_CACHE

        mock_scenario = MockDetectorAdapter.create_synthetic_campus_scenario(
            num_frames=num_frames,
            frame_size=(1280, 720),
        )
        scripted: Dict[int, List[Detection]] = {
            k: list(v) for k, v in mock_scenario._scripted.items()
        }
        base_bg = _build_demo_campus_background(1280, 720)
        frames: List[np.ndarray] = []

        color_by_cls = {
            0: (140, 225, 110),  # person
            1: (230, 200, 90),   # bicycle
            2: (235, 165, 65),   # car
            3: (75, 145, 245),   # motorcycle
        }

        for f_idx in range(num_frames):
            frame = base_bg.copy()
            for det in scripted.get(f_idx, []):
                x1, y1, x2, y2 = [int(round(v)) for v in det.bbox]
                color = color_by_cls.get(det.cls_id, (200, 200, 200))
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness=-1)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (245, 245, 245), thickness=2)
                if det.cls_id == 2:
                    # Sedan windshield accent
                    wx1 = x1 + max(6, (x2 - x1) // 4)
                    wx2 = x2 - max(6, (x2 - x1) // 5)
                    wy1 = y1 + 6
                    wy2 = y2 - 6
                    if wx2 > wx1 and wy2 > wy1:
                        cv2.rectangle(frame, (wx1, wy1), (wx2, wy2), (45, 65, 90), thickness=-1)
            cv2.putText(
                frame,
                f"SYNTH FRAME {f_idx + 1:03d}/{num_frames:03d}",
                (1015, 38),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (185, 200, 215),
                1,
                cv2.LINE_AA,
            )
            frames.append(frame)

        _DEMO_FRAMES_CACHE = (frames, scripted)
        return _DEMO_FRAMES_CACHE


def _resolve_local_path(raw_path: str) -> Path:
    """Resolve an absolute or project-relative local filesystem path."""
    p = Path(raw_path)
    if not p.is_absolute() and not p.exists():
        candidate = PROJECT_ROOT / p
        if candidate.exists():
            return candidate
    return p


class TaskStreamRunner:
    """Manages real detection/tracking/rules pipeline execution and broadcasting for one task."""

    def __init__(self, task: TaskRead, config: StreamConfig) -> None:
        self.task = task
        self.config = config
        self.task_id = task.id
        self.subscribers: Set[asyncio.Queue] = set()
        self.running = False
        self.is_demo_simulation = False
        self.frame_id = 0
        self.metrics = SystemMetrics()
        self.latest_frame: Optional[TrackFrame] = None
        self.latest_jpeg: Optional[bytes] = None

        self._jpeg_seq: int = 0
        self._jpeg_lock = threading.Lock()
        self._raw_frame_lock = threading.Lock()
        self._latest_raw_frame: Optional[np.ndarray] = None
        self._jpeg_event = threading.Event()
        self._jpeg_thread: Optional[threading.Thread] = None

        self._db_queue: queue.Queue = queue.Queue(maxsize=256)
        self._db_thread: Optional[threading.Thread] = None
        self._first_persist_done: bool = False

        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self.reader: Optional[VideoStreamReader] = None
        self.scheduler: Optional[BatchInferenceScheduler] = None
        self.detector: Optional[DetectorAdapter] = None
        self.tracker: Optional[ByteTracker] = None
        self.rules_engine: Optional[CampusRoadRulesEngine] = None

        self._latency_history: Deque[float] = deque(maxlen=100)
        self._track_pos_history: Dict[int, Deque[Tuple[int, float, float]]] = {}
        self._track_speed_ema: Dict[int, float] = {}
        self._pending_violations: List[ViolationEvent] = []
        self._last_ws_push: float = 0.0
        self._last_downsample: float = 0.0
        self._last_jpeg_time: float = 0.0
        self._jpeg_interval: float = 1.0 / 30.0
        self._ws_interval: float = 1.0 / 30.0
        self._downsample_interval: float = 1.0 / max(1.0, float(config.downsample_fps))

    @property
    def jpeg_seq(self) -> int:
        with self._jpeg_lock:
            return self._jpeg_seq

    @property
    def is_eof_done(self) -> bool:
        if self.is_demo_simulation or self.reader is None:
            return False
        sched_alive = (
            self.scheduler is not None
            and self.scheduler._thread is not None
            and self.scheduler._thread.is_alive()
        )
        return self.reader.is_eof and not sched_alive

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=30)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _build_pipeline(self) -> None:
        raw_source = (self.task.video_source or self.config.video_source or "").strip()
        is_demo = (
            raw_source.lower() == "demo"
            or self.config.detector_type == DetectorType.MOCK
        )
        self.is_demo_simulation = is_demo

        tracker_cfg = ByteTrackerConfig(
            use_nsa=self.config.use_nsa,
            use_diou=self.config.use_diou,
            use_recovery_pool=self.config.use_recovery_pool,
            use_class_aware=self.config.use_class_aware,
            use_vdc=self.config.use_vdc,
            use_oru=self.config.use_oru,
            use_lazy_appearance=self.config.use_lazy_appearance,
        )
        self.tracker = ByteTracker(tracker_cfg)

        zones = db.list_zones(self.task_id)
        self.rules_engine = CampusRoadRulesEngine.from_zone_dicts(
            [z.model_dump(mode="json") for z in zones]
        )

        stride = self.config.max_skip_stride if self.config.adaptive_skip else 1
        adaptive_cfg = AdaptiveKeyframeConfig(
            enabled=bool(self.config.adaptive_skip),
            max_skip_stride=max(1, int(stride)),
        )
        playback_mode = str(getattr(self.config, "playback_mode", "native") or "native").strip().lower()

        if is_demo:
            frames, scripted = _get_demo_scenario_assets(num_frames=180)
            idx_by_id = {id(fr): idx for idx, fr in enumerate(frames)}
            num_frames = len(frames)
            self.detector = MockDetectorAdapter(
                generator=lambda call_idx, frame: list(
                    scripted.get(idx_by_id.get(id(frame), call_idx % num_frames), [])
                ),
                class_names=MockDetectorAdapter.DEFAULT_NAMES,
            )
            demo_fps = float(self.config.fps) if self.config.fps and float(self.config.fps) > 0.0 else 25.0
            self.reader = VideoStreamReader(
                source=frames,
                buffer_capacity=64,
                drop_policy="oldest",
                target_fps=demo_fps,
                loop_synthetic=True,
            )
            batch_size = max(1, int(getattr(self.config, "batch_size", 4)))
            batch_timeout = 0.004
        else:
            if not raw_source:
                raise FileNotFoundError("Video source cannot be empty.")
            if raw_source.isdigit():
                resolved_source: Union[int, str] = int(raw_source)
            elif raw_source.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
                resolved_source = raw_source
            else:
                src_path = _resolve_local_path(raw_source)
                if not src_path.exists():
                    raise FileNotFoundError(f"Video source not found: {raw_source}")
                resolved_source = str(src_path)

            if not self.config.model_path:
                raise FileNotFoundError(
                    f"model_path is required for detector_type='{self.config.detector_type.value}' "
                    "(automatic downloading is disabled)."
                )
            weight_path = _resolve_local_path(self.config.model_path)
            if not weight_path.exists():
                raise FileNotFoundError(f"Model weights not found: {self.config.model_path}")

            self.detector = create_detector_adapter(
                self.config.detector_type.value,
                model_path=str(weight_path),
                conf_thresh=self.config.conf_threshold,
                iou_thresh=self.config.iou_threshold,
                device=getattr(self.config, "device", "cuda:0"),
                half=bool(getattr(self.config, "half_precision", True)),
                imgsz=self.config.imgsz,
                lazy_load=False,
            )
            is_seq_dir = (
                isinstance(resolved_source, str)
                and "://" not in resolved_source
                and Path(resolved_source).is_dir()
            )
            is_live_stream = isinstance(resolved_source, int) or (
                isinstance(resolved_source, str)
                and resolved_source.lower().startswith(("rtsp://", "rtsps://", "http://", "https://"))
            )
            if playback_mode == "native" and not is_live_stream:
                target_fps: Optional[float] = detect_source_native_fps(resolved_source, default_fps=30.0)
                batch_size = 1
                batch_timeout = 0.002
            else:
                target_fps = (
                    float(self.config.fps)
                    if self.config.fps and float(self.config.fps) > 0.0
                    else None
                )
                batch_size = max(1, int(getattr(self.config, "batch_size", 8)))
                batch_timeout = 0.004

            self.reader = VideoStreamReader(
                source=resolved_source,
                buffer_capacity=64,
                drop_policy="oldest" if is_live_stream else "block",
                target_fps=target_fps,
                loop_synthetic=is_seq_dir,
            )

        enable_roi = bool(getattr(self.config, "enable_roi_rules", False))
        self.scheduler = BatchInferenceScheduler(
            detector=self.detector,
            tracker=self.tracker,
            rules_engine=(self.rules_engine if enable_roi else None),
            stream_reader=self.reader,
            batch_size=batch_size,
            batch_timeout=batch_timeout,
            adaptive_config=adaptive_cfg,
            include_frame_in_result=True,
        )

    async def start(self) -> None:
        if self.running:
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._latency_history.clear()
        self._track_pos_history.clear()
        self._track_speed_ema.clear()
        self._pending_violations.clear()
        self._last_ws_push = 0.0
        self._last_downsample = 0.0
        self._last_jpeg_time = 0.0
        self._first_persist_done = False
        self.frame_id = 0
        self._downsample_interval = 1.0 / max(1.0, float(self.config.downsample_fps))
        with self._raw_frame_lock:
            self._latest_raw_frame = None
        self._jpeg_event.clear()
        while not self._db_queue.empty():
            try:
                self._db_queue.get_nowait()
            except queue.Empty:
                break

        try:
            self._build_pipeline()
            assert self.reader is not None and self.scheduler is not None
            self._jpeg_thread = threading.Thread(
                target=self._jpeg_worker_loop, daemon=True
            )
            self._db_thread = threading.Thread(
                target=self._db_worker_loop, daemon=True
            )
            self._jpeg_thread.start()
            self._db_thread.start()
            self.reader.start()
            self.running = True
            self.scheduler.start(on_result=self._on_pipeline_result)
        except Exception:
            self.running = False
            self._stop_event.set()
            await asyncio.to_thread(self._stop_pipeline_sync)
            db.update_task(self.task_id, TaskUpdate(status=TaskStatus.ERROR))
            raise

    def _stop_pipeline_sync(self) -> None:
        if self.scheduler is not None:
            self.scheduler._stop_event.set()
        if self.reader is not None:
            self.reader.stop(timeout=2.0)
        if self.scheduler is not None:
            self.scheduler.stop(timeout=2.0)
        self._jpeg_event.set()
        if self._jpeg_thread is not None and self._jpeg_thread.is_alive():
            self._jpeg_thread.join(timeout=1.5)
        self._jpeg_thread = None

        try:
            self._db_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._db_thread is not None and self._db_thread.is_alive():
            self._db_thread.join(timeout=2.0)
        self._db_thread = None
        self.scheduler = None
        self.reader = None

    async def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        await asyncio.to_thread(self._stop_pipeline_sync)
        self.latest_frame = None
        with self._jpeg_lock:
            self.latest_jpeg = None
        with self._raw_frame_lock:
            self._latest_raw_frame = None
        self.metrics = SystemMetrics()
        self._notify_stop_subscribers()

    def _notify_stop_subscribers(self) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                    q.put_nowait(None)
                except Exception:
                    pass
            except Exception:
                pass

    def _broadcast_frame_sync(self, frame: TrackFrame) -> None:
        dead_queues: List[asyncio.Queue] = []
        for q in list(self.subscribers):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                    q.put_nowait(frame)
                except Exception:
                    dead_queues.append(q)
            except Exception:
                dead_queues.append(q)
        for dead in dead_queues:
            self.subscribers.discard(dead)

    def _schedule_broadcast(self, frame: TrackFrame) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._broadcast_frame_sync, frame)
            except RuntimeError:
                pass

    def _compute_motion(
        self, track: Any, source_fps: float
    ) -> Tuple[float, List[float]]:
        tid = int(track.track_id)
        tracklet_len = int(getattr(track, "tracklet_len", 0) or 0)
        cx, cy = track.center
        vx_pf = 0.0
        vy_pf = 0.0
        used_kalman = False

        mean = getattr(track, "mean", None)
        if mean is not None and len(mean) >= 6:
            vx_pf = float(mean[4])
            vy_pf = float(mean[5])
            used_kalman = True
        else:
            buf = self._track_pos_history.get(tid)
            if buf and len(buf) >= 2:
                f0, x0, y0 = buf[0]
                f1, x1, y1 = buf[-1]
                df = max(int(f1 - f0), 1)
                vx_pf = (x1 - x0) / float(df)
                vy_pf = (y1 - y0) / float(df)

        vx_px = vx_pf * source_fps
        vy_px = vy_pf * source_fps

        if tracklet_len < 3 and not used_kalman:
            return 0.0, [round(vx_px, 1), round(vy_px, 1)]
        if tracklet_len < 2:
            return 0.0, [round(vx_px, 1), round(vy_px, 1)]

        if self.rules_engine is not None:
            p0 = self.rules_engine.homography.pixel_to_meter((cx, cy))
            p1 = self.rules_engine.homography.pixel_to_meter((cx + vx_pf, cy + vy_pf))
            dist_m = float(math.hypot(float(p1[0] - p0[0]), float(p1[1] - p0[1])))
            speed_mps = dist_m * source_fps
        else:
            speed_mps = float(math.hypot(vx_px, vy_px) * 0.05)

        speed_kmh = speed_mps * 3.6
        cls_lower = str(getattr(track, "cls_name", "") or "").lower()
        if cls_lower in ("person", "pedestrian"):
            max_kmh = 18.0
        elif cls_lower in ("bicycle", "cyclist"):
            max_kmh = 35.0
        else:
            max_kmh = 80.0

        speed_kmh = min(max(speed_kmh, 0.0), max_kmh)
        prev_ema = self._track_speed_ema.get(tid)
        if prev_ema is None:
            smoothed = speed_kmh if tracklet_len >= 3 else 0.0
        else:
            smoothed = 0.25 * speed_kmh + 0.75 * prev_ema
        self._track_speed_ema[tid] = smoothed
        return round(smoothed, 1), [round(vx_px, 1), round(vy_px, 1)]

    def _jpeg_worker_loop(self) -> None:
        """Encode preview JPEG off the inference thread at ~15 Hz."""
        while not self._stop_event.is_set():
            self._jpeg_event.wait(timeout=0.1)
            self._jpeg_event.clear()
            if self._stop_event.is_set():
                break

            now_perf = time.perf_counter()
            if self._last_jpeg_time != 0.0 and (now_perf - self._last_jpeg_time) < self._jpeg_interval:
                continue

            with self._raw_frame_lock:
                frame = self._latest_raw_frame
                self._latest_raw_frame = None
            if frame is None:
                continue

            self._last_jpeg_time = now_perf
            h, w = frame.shape[:2]
            if w > 1280:
                scale = 1280.0 / float(w)
                img = cv2.resize(
                    frame,
                    (1280, max(1, int(round(h * scale)))),
                    interpolation=cv2.INTER_LINEAR,
                )
            else:
                img = frame
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
            if ok:
                with self._jpeg_lock:
                    self.latest_jpeg = buf.tobytes()
                    self._jpeg_seq += 1

    def _db_worker_loop(self) -> None:
        """Drain trajectory batch inserts off the inference callback thread."""
        while True:
            try:
                item = self._db_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if item is None:
                break
            try:
                frame_id, now_ts, track_items = item
                if track_items:
                    db.insert_trajectories_batch(self.task_id, frame_id, now_ts, track_items)
            except Exception:
                pass

    def _submit_raw_jpeg_frame(self, frame: np.ndarray) -> None:
        with self._raw_frame_lock:
            self._latest_raw_frame = frame
        self._jpeg_event.set()

    def _on_pipeline_result(self, result: PipelineResult) -> None:
        if self._stop_event.is_set() or not self.running:
            return

        self.frame_id = int(result.frame_id)
        now_perf = time.perf_counter()
        now_ts = time.time()

        if result.frame is not None:
            frame_h, frame_w = int(result.frame.shape[0]), int(result.frame.shape[1])
            self._submit_raw_jpeg_frame(result.frame)
        else:
            frame_w = int(getattr(self.reader, "frame_width", 0) or 1280)
            frame_h = int(getattr(self.reader, "frame_height", 0) or 720)

        # Rolling latency and real scheduler telemetry
        total_ms = float(result.latency.total_ms)
        self._latency_history.append(total_ms)
        sorted_lat = sorted(self._latency_history)
        p95_idx = min(int(0.95 * len(sorted_lat)), len(sorted_lat) - 1)
        p95_lat = sorted_lat[p95_idx] if sorted_lat else total_ms
        avg_lat = sum(self._latency_history) / max(1, len(self._latency_history))

        sched_metrics = self.scheduler.get_metrics() if self.scheduler is not None else {}
        fps = float(sched_metrics.get("fps", 0.0))
        if fps <= 0.0:
            fps = 1000.0 / max(avg_lat, 1.0)
        drop_count = int(
            sched_metrics.get(
                "dropped_frames",
                self.reader.buffer.dropped_frames if self.reader is not None else 0,
            )
        )
        detect_skip_ratio = float(sched_metrics.get("detect_skip_ratio", 0.0))

        current_metrics = SystemMetrics(
            fps=round(fps, 1),
            p95_latency_ms=round(p95_lat, 2),
            avg_latency_ms=round(avg_lat, 2),
            drop_count=drop_count,
            queue_size=len(self.subscribers),
            detect_skip_ratio=round(detect_skip_ratio, 4),
            is_demo_simulation=self.is_demo_simulation,
            active_streams=1,
            stage_latency=PerStageLatency(
                decode_ms=round(float(result.latency.decode_ms), 2),
                detect_ms=round(float(result.latency.detect_ms), 2),
                track_ms=round(float(result.latency.track_ms), 2),
                rules_ms=round(float(result.latency.rules_ms), 2),
                total_ms=round(total_ms, 2),
            ),
        )
        self.metrics = current_metrics

        # Persist violations on every frame so nothing is dropped between WS downsample ticks
        for vio in result.violations:
            try:
                zone_id = int(vio.zone_or_lane_id)
            except (TypeError, ValueError):
                zone_id = None
            details = dict(vio.details or {})
            details.setdefault("cls_name", vio.cls_name)
            details.setdefault("zone_or_lane_id", vio.zone_or_lane_id)
            v_event = ViolationEvent(
                task_id=self.task_id,
                track_id=int(vio.track_id),
                violation_type=ViolationType(vio.rule_type),
                timestamp=now_ts,
                frame_id=int(vio.frame_id),
                bbox=[round(float(x), 1) for x in vio.bbox],
                zone_id=zone_id,
                acknowledged=False,
                details=details,
            )
            saved = db.create_violation(v_event)
            self._pending_violations.append(saved)

        # Update motion buffer on every frame (indexed by video frame_id, not wall clock)
        source_fps = max(1.0, float(getattr(self.reader, "source_fps", 0.0) or 30.0))
        active_ids: Set[int] = set()
        for t in result.tracks:
            tid = int(t.track_id)
            active_ids.add(tid)
            cx, cy = t.center
            pos_buf = self._track_pos_history.get(tid)
            if pos_buf is None:
                pos_buf = deque(maxlen=10)
                self._track_pos_history[tid] = pos_buf
            pos_buf.append((int(result.frame_id), float(cx), float(cy)))
        if len(self._track_pos_history) > 256:
            for old_id in list(self._track_pos_history.keys()):
                if old_id not in active_ids:
                    self._track_pos_history.pop(old_id, None)
                    self._track_speed_ema.pop(old_id, None)

        ws_due = self._last_ws_push == 0.0 or (now_perf - self._last_ws_push) >= self._ws_interval
        db_due = (
            self._last_downsample == 0.0
            or (now_perf - self._last_downsample) >= self._downsample_interval
        )

        if ws_due or db_due:
            track_items: List[TrackItem] = []
            for t in result.tracks:
                tid = int(t.track_id)
                speed_kmh, dir_vec = self._compute_motion(t, source_fps)
                trail = [
                    [round(float(hx), 1), round(float(hy), 1)]
                    for _, hx, hy in list(t.history)[-25:]
                ]
                track_items.append(
                    TrackItem(
                        track_id=tid,
                        bbox=[round(float(v), 1) for v in t.xyxy],
                        class_id=int(t.cls_id),
                        class_name=str(t.cls_name),
                        conf=round(float(t.score), 3),
                        speed=speed_kmh,
                        direction=dir_vec,
                        state="tracked" if t.state == TrackState.TRACKED else "lost",
                        trail=trail,
                    )
                )

            if db_due:
                self._last_downsample = now_perf
                if track_items:
                    if not self._first_persist_done:
                        self._first_persist_done = True
                        db.insert_trajectories_batch(
                            self.task_id, int(result.frame_id), now_ts, track_items
                        )
                    else:
                        try:
                            self._db_queue.put_nowait(
                                (int(result.frame_id), now_ts, track_items)
                            )
                        except queue.Full:
                            pass

            if ws_due:
                self._last_ws_push = now_perf
                active_violations = list(self._pending_violations)
                self._pending_violations.clear()

                frame_msg = TrackFrame(
                    task_id=self.task_id,
                    frame_id=int(result.frame_id),
                    timestamp=now_ts,
                    frame_width=frame_w,
                    frame_height=frame_h,
                    is_keyframe=bool(getattr(result, "is_keyframe", True)),
                    is_demo_simulation=self.is_demo_simulation,
                    tracks=track_items,
                    active_violations=active_violations,
                    metrics=current_metrics,
                )
                self.latest_frame = frame_msg
                self._schedule_broadcast(frame_msg)


class StreamEngineManager:
    """Singleton managing active task stream runners."""

    def __init__(self) -> None:
        self.runners: Dict[int, TaskStreamRunner] = {}

    def get_runner(self, task_id: int) -> Optional[TaskStreamRunner]:
        return self.runners.get(task_id)

    async def start_task(self, task_id: int) -> bool:
        task = db.get_task(task_id)
        if not task:
            return False

        existing = self.runners.get(task_id)
        if existing is not None:
            if existing.running:
                return True
            await existing.stop()
            self.runners.pop(task_id, None)

        runner = TaskStreamRunner(task, task.config)
        self.runners[task_id] = runner
        try:
            await runner.start()
        except Exception:
            self.runners.pop(task_id, None)
            raise

        db.update_task(task_id, TaskUpdate(status=TaskStatus.RUNNING))
        return True

    async def stop_task(self, task_id: int) -> bool:
        runner = self.runners.pop(task_id, None)
        if runner:
            await runner.stop()

        task = db.get_task(task_id)
        if task:
            db.update_task(task_id, TaskUpdate(status=TaskStatus.STOPPED))
            return True
        return False

    def get_system_metrics(self) -> SystemMetrics:
        """Aggregate real runtime telemetry across all active stream runners."""
        active = [r for r in self.runners.values() if r.running]
        if not active:
            return SystemMetrics(
                fps=0.0,
                p95_latency_ms=0.0,
                avg_latency_ms=0.0,
                drop_count=0,
                queue_size=0,
                detect_skip_ratio=0.0,
                is_demo_simulation=False,
                active_streams=0,
                stage_latency=PerStageLatency(),
            )

        avg_fps = sum(r.metrics.fps for r in active) / len(active)
        avg_p95 = sum(r.metrics.p95_latency_ms for r in active) / len(active)
        avg_lat = sum(r.metrics.avg_latency_ms for r in active) / len(active)
        total_drops = sum(r.metrics.drop_count for r in active)
        total_q = sum(len(r.subscribers) for r in active)
        avg_skip = sum(r.metrics.detect_skip_ratio for r in active) / len(active)
        any_demo = any(r.is_demo_simulation for r in active)
        first_stage = active[0].metrics.stage_latency

        return SystemMetrics(
            fps=round(avg_fps, 1),
            p95_latency_ms=round(avg_p95, 2),
            avg_latency_ms=round(avg_lat, 2),
            drop_count=total_drops,
            queue_size=total_q,
            detect_skip_ratio=round(avg_skip, 4),
            is_demo_simulation=any_demo,
            active_streams=len(active),
            stage_latency=first_stage,
        )


stream_manager = StreamEngineManager()
