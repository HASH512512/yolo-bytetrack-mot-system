"""Batch inference scheduler decoupling capture from batched detect + track + rules."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple
import numpy as np

from core.byte_tracker import ByteTracker
from core.detector_adapter import Detection, DetectorAdapter
from core.trajectory_pool import STrack
from pipeline.rules_engine import CampusRoadRulesEngine, Violation
from pipeline.video_stream import FramePacket, VideoStreamReader


@dataclass
class AdaptiveKeyframeConfig:
    """Configuration for adaptive keyframe detection and Kalman-only skipping."""

    enabled: bool = False
    max_skip_stride: int = 1
    accel_trigger_px: float = 18.0
    roi_guard_margin_px: float = 30.0


def _extract_track_velocity(track: STrack) -> Tuple[float, float]:
    """Extract (vx, vy) in px/frame from Kalman state or recent trajectory history."""
    mean = getattr(track, "mean", None)
    if mean is not None and len(mean) >= 6:
        return (float(mean[4]), float(mean[5]))
    history = getattr(track, "history", None)
    if history is not None and len(history) >= 2:
        f1, x1, y1 = history[-2]
        f2, x2, y2 = history[-1]
        dt = max(1, int(f2) - int(f1))
        return ((float(x2) - float(x1)) / dt, (float(y2) - float(y1)) / dt)
    return (0.0, 0.0)


def _point_near_polygon_bbox(
    point: Tuple[float, float],
    polygon: Sequence[Tuple[float, float]],
    margin_px: float,
) -> bool:
    """Return True if point is inside or within margin_px of polygon's axis-aligned bbox."""
    if not polygon:
        return False
    xs = [float(p[0]) for p in polygon]
    ys = [float(p[1]) for p in polygon]
    px, py = float(point[0]), float(point[1])
    return (
        (min(xs) - margin_px) <= px <= (max(xs) + margin_px)
        and (min(ys) - margin_px) <= py <= (max(ys) + margin_px)
    )


class AdaptiveKeyframeController:
    """Decides per-frame whether to run full object detection or Kalman-only prediction."""

    def __init__(self, config: Optional[AdaptiveKeyframeConfig] = None) -> None:
        self.config = config or AdaptiveKeyframeConfig()
        self._last_keyframe_id: Optional[int] = None
        self._last_velocities: Dict[int, Tuple[float, float]] = {}

    def reset(self) -> None:
        self._last_keyframe_id = None
        self._last_velocities.clear()

    def mark_scheduled_keyframe(self, frame_id: int) -> None:
        """Advance tentative keyframe id when planning multi-packet batches."""
        self._last_keyframe_id = int(frame_id)

    def should_detect(
        self,
        frame_id: int,
        active_tracks: Sequence[STrack],
        roi_polygons: Sequence[Sequence[Tuple[float, float]]] = (),
    ) -> Tuple[bool, str]:
        """Return (should_run_detector, reason) for the given frame and track state."""
        cfg = self.config
        if not cfg.enabled or int(cfg.max_skip_stride) <= 1:
            return (True, "always")

        if not active_tracks:
            return (True, "no_active_tracks")

        fid = int(frame_id)
        if self._last_keyframe_id is None:
            return (True, "initial")

        if (fid - self._last_keyframe_id) >= int(cfg.max_skip_stride):
            return (True, "stride_limit")

        accel_thresh = float(cfg.accel_trigger_px)
        margin = float(cfg.roi_guard_margin_px)
        for track in active_tracks:
            tid = int(track.track_id)
            vx, vy = _extract_track_velocity(track)
            if tid in self._last_velocities:
                pvx, pvy = self._last_velocities[tid]
                if math.hypot(vx - pvx, vy - pvy) >= accel_thresh:
                    return (True, "accel_trigger")

            if roi_polygons:
                anchor = track.bottom_center
                for poly in roi_polygons:
                    if _point_near_polygon_bbox(anchor, poly, margin):
                        return (True, "roi_guard")

        return (False, "kalman_skip")

    def record_keyframe(
        self, frame_id: int, active_tracks: Sequence[STrack]
    ) -> None:
        """Record keyframe id and per-track velocities after tracker.update()."""
        self._last_keyframe_id = int(frame_id)
        self._last_velocities = {
            int(t.track_id): _extract_track_velocity(t) for t in active_tracks
        }


def tracker_update_safe(
    tracker: Any,
    detections: Sequence[Detection],
    frame_id: int,
    frame: Optional[np.ndarray],
) -> List[STrack]:
    """Call tracker.update(..., frame=frame) with fallback for mocks lacking frame arg."""
    try:
        return tracker.update(detections, frame_id=frame_id, frame=frame)
    except TypeError as exc:
        msg = str(exc)
        if "frame" in msg or "unexpected keyword" in msg:
            return tracker.update(detections, frame_id=frame_id)
        raise


def tracker_predict_only_safe(tracker: Any, frame_id: int) -> List[STrack]:
    """Call tracker.predict_only(frame_id=...) with fallback to update([], frame_id=...)."""
    predict_fn = getattr(tracker, "predict_only", None)
    if callable(predict_fn):
        return predict_fn(frame_id=frame_id)
    return tracker.update([], frame_id=frame_id)


@dataclass
class StageLatency:
    """Per-frame latency breakdown in milliseconds."""

    decode_ms: float
    detect_ms: float
    track_ms: float
    rules_ms: float
    total_ms: float


@dataclass
class PipelineResult:
    """Full inference output for a single video frame."""

    frame_id: int
    timestamp: float
    detections: List[Detection]
    tracks: List[STrack]
    violations: List[Violation]
    latency: StageLatency
    is_keyframe: bool = True
    keyframe_reason: str = "always"
    frame: Optional[np.ndarray] = None
    gmc_shift_px: float = 0.0


FrameResult = PipelineResult


def execute_frame_postprocess(
    packet: FramePacket,
    dets: List[Detection],
    detect_ms: float,
    is_keyframe: bool,
    keyframe_reason: str,
    tracker: Any,
    rules_engine: Optional[CampusRoadRulesEngine],
    controller: AdaptiveKeyframeController,
    include_frame: bool,
) -> PipelineResult:
    """Execute track update/predict_only and rules evaluation for one frame."""
    t_trk_0 = time.perf_counter()
    if is_keyframe:
        tracks = tracker_update_safe(
            tracker, dets, frame_id=packet.frame_id, frame=packet.frame
        )
        track_ms = (time.perf_counter() - t_trk_0) * 1000.0
        controller.record_keyframe(packet.frame_id, tracks)
    else:
        tracks = tracker_predict_only_safe(tracker, frame_id=packet.frame_id)
        track_ms = (time.perf_counter() - t_trk_0) * 1000.0

    gmc_shift_px = float(getattr(tracker, "last_gmc_shift_px", 0.0))

    t_rul_0 = time.perf_counter()
    violations = (
        rules_engine.evaluate(
            tracks, frame_id=packet.frame_id, timestamp=packet.timestamp
        )
        if rules_engine is not None
        else []
    )
    rules_ms = (time.perf_counter() - t_rul_0) * 1000.0

    total_ms = packet.decode_ms + detect_ms + track_ms + rules_ms
    latency = StageLatency(
        decode_ms=round(packet.decode_ms, 3),
        detect_ms=round(detect_ms, 3),
        track_ms=round(track_ms, 3),
        rules_ms=round(rules_ms, 3),
        total_ms=round(total_ms, 3),
    )
    return PipelineResult(
        frame_id=packet.frame_id,
        timestamp=packet.timestamp,
        detections=dets,
        tracks=tracks,
        violations=violations,
        latency=latency,
        is_keyframe=is_keyframe,
        keyframe_reason=keyframe_reason,
        frame=packet.frame if include_frame else None,
        gmc_shift_px=gmc_shift_px,
    )


def summarize_stage_latencies(lats: Sequence[StageLatency]) -> Dict[str, float]:
    """Summarize rolling StageLatency percentiles and per-stage averages."""
    if not lats:
        return {
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "decode_ms": 0.0,
            "detect_ms": 0.0,
            "track_ms": 0.0,
            "rules_ms": 0.0,
        }
    totals = np.asarray([x.total_ms for x in lats], dtype=np.float64)
    return {
        "p50_ms": round(float(np.percentile(totals, 50)), 3),
        "p95_ms": round(float(np.percentile(totals, 95)), 3),
        "decode_ms": round(float(np.mean([x.decode_ms for x in lats])), 3),
        "detect_ms": round(float(np.mean([x.detect_ms for x in lats])), 3),
        "track_ms": round(float(np.mean([x.track_ms for x in lats])), 3),
        "rules_ms": round(float(np.mean([x.rules_ms for x in lats])), 3),
    }


class BatchInferenceScheduler:
    """Pulls frame batches from VideoStreamReader and executes detector -> tracker -> rules."""

    def __init__(
        self,
        detector: DetectorAdapter,
        tracker: ByteTracker,
        rules_engine: Optional[CampusRoadRulesEngine] = None,
        stream_reader: Optional[VideoStreamReader] = None,
        batch_size: int = 4,
        batch_timeout: float = 0.03,
        stats_window: int = 200,
        adaptive_config: Optional[AdaptiveKeyframeConfig] = None,
        include_frame_in_result: bool = False,
    ) -> None:
        self.detector = detector
        self.tracker = tracker
        self.rules_engine = rules_engine
        self.stream_reader = stream_reader
        self.batch_size = max(1, int(batch_size))
        self.batch_timeout = float(batch_timeout)
        self.adaptive_config = adaptive_config or AdaptiveKeyframeConfig()
        self.adaptive_controller = AdaptiveKeyframeController(self.adaptive_config)
        self.include_frame_in_result = bool(include_frame_in_result)

        self._latencies: Deque[StageLatency] = deque(maxlen=stats_window)
        self._processed_frames: int = 0
        self._keyframes: int = 0
        self._skipped_detect_frames: int = 0
        self._active_tracks: List[STrack] = []
        self._start_wall: float = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def process_batch(self, packets: List[FramePacket]) -> List[PipelineResult]:
        """Execute batched detection, sequential tracking, and rules on a list of FramePackets."""
        if not packets:
            return []

        if self.rules_engine is not None:
            try:
                roi_polygons = self.rules_engine.get_roi_polygons(include_lanes=False)
            except TypeError:
                roi_polygons = self.rules_engine.get_roi_polygons()
        else:
            roi_polygons = ()

        decisions: List[Tuple[bool, str]] = []
        keyframe_indices: List[int] = []
        for idx, packet in enumerate(packets):
            is_kf, reason = self.adaptive_controller.should_detect(
                frame_id=packet.frame_id,
                active_tracks=self._active_tracks,
                roi_polygons=roi_polygons,
            )
            decisions.append((is_kf, reason))
            if is_kf:
                keyframe_indices.append(idx)
                self.adaptive_controller.mark_scheduled_keyframe(packet.frame_id)

        keyframe_dets: Dict[int, List[Detection]] = {}
        per_kf_detect_ms = 0.0
        if keyframe_indices:
            kf_frames = [packets[i].frame for i in keyframe_indices]
            t_det_0 = time.perf_counter()
            batch_dets = self.detector.detect_batch(kf_frames)
            detect_batch_ms = (time.perf_counter() - t_det_0) * 1000.0
            per_kf_detect_ms = detect_batch_ms / len(keyframe_indices)
            for idx, dets in zip(keyframe_indices, batch_dets):
                keyframe_dets[idx] = dets

        results: List[PipelineResult] = []
        for idx, packet in enumerate(packets):
            is_kf, reason = decisions[idx]
            dets = keyframe_dets.get(idx, []) if is_kf else []
            detect_ms = per_kf_detect_ms if is_kf else 0.0
            res = execute_frame_postprocess(
                packet=packet,
                dets=dets,
                detect_ms=detect_ms,
                is_keyframe=is_kf,
                keyframe_reason=reason,
                tracker=self.tracker,
                rules_engine=self.rules_engine,
                controller=self.adaptive_controller,
                include_frame=self.include_frame_in_result,
            )
            self._active_tracks = list(res.tracks)
            with self._lock:
                self._latencies.append(res.latency)
                self._processed_frames += 1
                if is_kf:
                    self._keyframes += 1
                else:
                    self._skipped_detect_frames += 1
            results.append(res)
        return results

    def step(self) -> List[PipelineResult]:
        """Pull one batch from stream_reader and process it synchronously."""
        if self.stream_reader is None:
            raise RuntimeError("stream_reader must be set to call step().")
        packets = self.stream_reader.buffer.get_batch(
            max_batch=self.batch_size, timeout=self.batch_timeout
        )
        return self.process_batch(packets)

    def start(
        self, on_result: Optional[Callable[[PipelineResult], None]] = None
    ) -> "BatchInferenceScheduler":
        """Start background consumer thread reading from stream_reader."""
        if self.stream_reader is None:
            raise RuntimeError("stream_reader must be set before calling start().")
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._start_wall = time.perf_counter()
        self._thread = threading.Thread(
            target=self._run_loop, args=(on_result,), daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run_loop(
        self, on_result: Optional[Callable[[PipelineResult], None]]
    ) -> None:
        assert self.stream_reader is not None
        while not self._stop_event.is_set():
            results = self.step()
            if results:
                if on_result is not None:
                    for res in results:
                        on_result(res)
            elif self.stream_reader.is_eof:
                break

    def get_metrics(self) -> Dict[str, float | int]:
        """Return rolling FPS, P50/P95 total latency, and per-stage averages."""
        with self._lock:
            lats = list(self._latencies)
            processed = self._processed_frames
            keyframes = self._keyframes
            skipped = self._skipped_detect_frames
        elapsed = (
            max(time.perf_counter() - self._start_wall, 1e-6)
            if self._start_wall
            else 1e-6
        )
        skip_ratio = (skipped / processed) if processed > 0 else 0.0
        lat_summary = summarize_stage_latencies(lats)
        if not lats:
            fps = 0.0
        elif self._start_wall:
            fps = processed / elapsed
        else:
            avg_total = float(np.mean([x.total_ms for x in lats]))
            fps = 1000.0 / max(avg_total, 1e-3)

        return {
            "processed_frames": processed,
            "dropped_frames": (
                self.stream_reader.buffer.dropped_frames if self.stream_reader else 0
            ),
            "fps": round(float(fps), 2),
            **lat_summary,
            "keyframes": keyframes,
            "skipped_detect_frames": skipped,
            "detect_skip_ratio": round(float(skip_ratio), 4),
            "gmc_shift_px": float(getattr(self.tracker, "last_gmc_shift_px", 0.0)),
        }
