"""Multi-stream round-robin batch coordinator sharing one detector across streams."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.byte_tracker import ByteTracker
from core.detector_adapter import Detection, DetectorAdapter
from core.trajectory_pool import STrack
from pipeline.batch_scheduler import (
    AdaptiveKeyframeConfig,
    AdaptiveKeyframeController,
    PipelineResult,
    StageLatency,
    execute_frame_postprocess,
    summarize_stage_latencies,
)
from pipeline.rules_engine import CampusRoadRulesEngine
from pipeline.video_stream import FramePacket, VideoStreamReader


@dataclass
class StreamSpec:
    """Registration specification for a single managed video stream."""

    stream_id: str
    reader: VideoStreamReader
    tracker: ByteTracker
    rules_engine: Optional[CampusRoadRulesEngine] = None
    adaptive_config: Optional[AdaptiveKeyframeConfig] = None
    on_result: Optional[Callable[[str, PipelineResult], None]] = None


class _StreamRuntime:
    """Internal per-stream mutable scheduling state."""

    def __init__(self, spec: StreamSpec, stats_window: int) -> None:
        self.spec = spec
        self.controller = AdaptiveKeyframeController(
            spec.adaptive_config or AdaptiveKeyframeConfig()
        )
        self.active_tracks: List[STrack] = []
        self.latencies: Deque[StageLatency] = deque(maxlen=stats_window)
        self.processed_frames: int = 0
        self.keyframes: int = 0
        self.skipped_detect_frames: int = 0
        self.start_wall: float = 0.0


class MultiStreamBatchCoordinator:
    """Collects frames round-robin across streams and runs ONE shared detect_batch per step."""

    def __init__(
        self,
        detector: DetectorAdapter,
        max_batch_size: int = 8,
        poll_timeout: float = 0.01,
        include_frame_in_result: bool = False,
        stats_window: int = 200,
    ) -> None:
        self.detector = detector
        self.max_batch_size = max(1, int(max_batch_size))
        self.poll_timeout = max(0.0, float(poll_timeout))
        self.include_frame_in_result = bool(include_frame_in_result)
        self._stats_window = max(1, int(stats_window))

        self._streams: Dict[str, _StreamRuntime] = {}
        self._rr_cursor: int = 0
        self._lock = threading.Lock()
        self._step_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._start_wall: float = 0.0
        self._total_batches: int = 0
        self._total_batch_items: int = 0

    def add_stream(self, spec: StreamSpec) -> None:
        """Register a new stream with the coordinator."""
        with self._lock:
            if spec.stream_id in self._streams:
                raise ValueError(f"Stream '{spec.stream_id}' is already registered.")
            self._streams[spec.stream_id] = _StreamRuntime(spec, self._stats_window)

    def remove_stream(self, stream_id: str) -> bool:
        """Unregister a stream by stream_id."""
        with self._lock:
            removed = self._streams.pop(stream_id, None) is not None
            if self._streams:
                self._rr_cursor %= len(self._streams)
            else:
                self._rr_cursor = 0
            return removed

    def _collect_round_robin(
        self, runtimes: List[_StreamRuntime]
    ) -> List[Tuple[_StreamRuntime, FramePacket]]:
        if not runtimes:
            return []

        n = len(runtimes)
        with self._lock:
            cursor = self._rr_cursor % n
        order = runtimes[cursor:] + runtimes[:cursor]

        collected: List[Tuple[_StreamRuntime, FramePacket]] = []
        last_hit_offset = -1

        while len(collected) < self.max_batch_size:
            progress = False
            for offset, rt in enumerate(order):
                if len(collected) >= self.max_batch_size:
                    break
                pkt = rt.spec.reader.buffer.get(timeout=0.0)
                if pkt is not None:
                    collected.append((rt, pkt))
                    progress = True
                    last_hit_offset = offset
            if not progress:
                break

        with self._lock:
            if self._streams:
                advance = (last_hit_offset + 1) if last_hit_offset >= 0 else 1
                self._rr_cursor = (cursor + advance) % len(self._streams)

        return collected

    def step_once(self) -> Dict[str, List[PipelineResult]]:
        """Run one round-robin cross-stream collection, batched detection, and per-stream tracking/rules."""
        with self._step_lock:
            t_step_0 = time.perf_counter()
            with self._lock:
                runtimes = list(self._streams.values())
            if not runtimes:
                return {}

            collected = self._collect_round_robin(runtimes)
            if not collected and self.poll_timeout > 0.0:
                if all(rt.spec.reader.is_eof for rt in runtimes):
                    return {}
                deadline = time.perf_counter() + self.poll_timeout
                while not collected and not self._stop_event.is_set():
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0.0:
                        break
                    self._stop_event.wait(timeout=min(0.002, remaining))
                    with self._lock:
                        runtimes = list(self._streams.values())
                    if not runtimes:
                        return {}
                    collected = self._collect_round_robin(runtimes)
                    if not collected and all(rt.spec.reader.is_eof for rt in runtimes):
                        break

            if not collected:
                return {}

            with self._lock:
                if self._start_wall == 0.0:
                    self._start_wall = t_step_0
                self._total_batches += 1
                self._total_batch_items += len(collected)
                for rt, _ in collected:
                    if rt.start_wall == 0.0:
                        rt.start_wall = t_step_0

            decisions: List[Tuple[bool, str]] = []
            keyframe_indices: List[int] = []
            for idx, (rt, packet) in enumerate(collected):
                if rt.spec.rules_engine is not None:
                    try:
                        roi_polygons = rt.spec.rules_engine.get_roi_polygons(
                            include_lanes=False
                        )
                    except TypeError:
                        roi_polygons = rt.spec.rules_engine.get_roi_polygons()
                else:
                    roi_polygons = ()
                is_kf, reason = rt.controller.should_detect(
                    frame_id=packet.frame_id,
                    active_tracks=rt.active_tracks,
                    roi_polygons=roi_polygons,
                )
                decisions.append((is_kf, reason))
                if is_kf:
                    keyframe_indices.append(idx)
                    rt.controller.mark_scheduled_keyframe(packet.frame_id)

            keyframe_dets: Dict[int, List[Detection]] = {}
            per_kf_detect_ms = 0.0
            if keyframe_indices:
                kf_frames = [collected[i][1].frame for i in keyframe_indices]
                t_det_0 = time.perf_counter()
                batch_dets = self.detector.detect_batch(kf_frames)
                detect_batch_ms = (time.perf_counter() - t_det_0) * 1000.0
                per_kf_detect_ms = detect_batch_ms / len(keyframe_indices)
                for idx, dets in zip(keyframe_indices, batch_dets):
                    keyframe_dets[idx] = dets

            results_by_stream: Dict[str, List[PipelineResult]] = {}
            for idx, (rt, packet) in enumerate(collected):
                sid = rt.spec.stream_id
                is_kf, reason = decisions[idx]
                dets = keyframe_dets.get(idx, []) if is_kf else []
                detect_ms = per_kf_detect_ms if is_kf else 0.0
                res = execute_frame_postprocess(
                    packet=packet,
                    dets=dets,
                    detect_ms=detect_ms,
                    is_keyframe=is_kf,
                    keyframe_reason=reason,
                    tracker=rt.spec.tracker,
                    rules_engine=rt.spec.rules_engine,
                    controller=rt.controller,
                    include_frame=self.include_frame_in_result,
                )
                rt.active_tracks = list(res.tracks)
                with self._lock:
                    rt.latencies.append(res.latency)
                    rt.processed_frames += 1
                    if is_kf:
                        rt.keyframes += 1
                    else:
                        rt.skipped_detect_frames += 1

                results_by_stream.setdefault(sid, []).append(res)
                if rt.spec.on_result is not None:
                    rt.spec.on_result(sid, res)

            return results_by_stream

    def start(self) -> "MultiStreamBatchCoordinator":
        """Start background scheduling loop across all registered streams."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        with self._lock:
            if self._start_wall == 0.0:
                self._start_wall = time.perf_counter()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        """Signal background scheduling loop to stop and wait for join."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            step_res = self.step_once()
            if step_res:
                continue
            with self._lock:
                runtimes = list(self._streams.values())
            if runtimes and all(rt.spec.reader.is_eof for rt in runtimes):
                break
            if not runtimes:
                self._stop_event.wait(timeout=max(0.005, self.poll_timeout))

    def _build_stream_metrics(
        self, sid: str, rt: _StreamRuntime, now: float
    ) -> Dict[str, Any]:
        lats = list(rt.latencies)
        processed = rt.processed_frames
        keyframes = rt.keyframes
        skipped = rt.skipped_detect_frames
        dropped = rt.spec.reader.buffer.dropped_frames
        elapsed = max(now - rt.start_wall, 1e-6) if rt.start_wall else 1e-6
        skip_ratio = (skipped / processed) if processed > 0 else 0.0
        fps = (processed / elapsed) if (lats and rt.start_wall) else 0.0
        return {
            "stream_id": sid,
            "processed_frames": processed,
            "dropped_frames": dropped,
            "fps": round(float(fps), 2),
            **summarize_stage_latencies(lats),
            "keyframes": keyframes,
            "skipped_detect_frames": skipped,
            "detect_skip_ratio": round(float(skip_ratio), 4),
        }

    def get_metrics(self, stream_id: Optional[str] = None) -> Dict[str, Any]:
        """Return per-stream metrics (when stream_id given) or aggregate + per-stream metrics."""
        now = time.perf_counter()
        with self._lock:
            if stream_id is not None:
                rt = self._streams.get(stream_id)
                if rt is None:
                    raise KeyError(f"Unknown stream_id: {stream_id}")
                return self._build_stream_metrics(stream_id, rt, now)

            stream_items = list(self._streams.items())
            total_batches = self._total_batches
            total_batch_items = self._total_batch_items
            start_wall = self._start_wall

            per_stream: Dict[str, Dict[str, Any]] = {}
            all_lats: List[StageLatency] = []
            total_processed = 0
            total_dropped = 0
            total_keyframes = 0
            total_skipped = 0

            for sid, rt in stream_items:
                m = self._build_stream_metrics(sid, rt, now)
                per_stream[sid] = m
                all_lats.extend(rt.latencies)
                total_processed += rt.processed_frames
                total_dropped += int(m["dropped_frames"])
                total_keyframes += rt.keyframes
                total_skipped += rt.skipped_detect_frames

        elapsed = max(now - start_wall, 1e-6) if start_wall else 1e-6
        agg_fps = (total_processed / elapsed) if (start_wall and total_processed > 0) else 0.0
        per_stream_fps = (
            sum(float(m["fps"]) for m in per_stream.values()) / max(1, len(per_stream))
            if per_stream
            else 0.0
        )
        agg_skip_ratio = (total_skipped / total_processed) if total_processed > 0 else 0.0
        avg_batch_size = (total_batch_items / total_batches) if total_batches > 0 else 0.0

        return {
            "stream_count": len(per_stream),
            "processed_frames": total_processed,
            "dropped_frames": total_dropped,
            "fps": round(float(agg_fps), 2),
            "per_stream_fps": round(float(per_stream_fps), 2),
            **summarize_stage_latencies(all_lats),
            "keyframes": total_keyframes,
            "skipped_detect_frames": total_skipped,
            "detect_skip_ratio": round(float(agg_skip_ratio), 4),
            "avg_batch_size": round(float(avg_batch_size), 3),
            "total_batches": total_batches,
            "streams": per_stream,
        }
