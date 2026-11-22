"""Unit and integration tests for pipeline, rules engine, and ablation suite."""

from __future__ import annotations

from pathlib import Path
import threading
import time
import cv2
import numpy as np
import pytest

from core.byte_tracker import ByteTracker, ByteTrackerConfig
from core.detector_adapter import Detection, DetectorAdapter, MockDetectorAdapter
from core.kalman_filter import NSAKalmanFilter
from core.trajectory_pool import STrack
from evaluation.ablation_experiments import run_ablation_suite
from evaluation.make_reproduction_table import (
    format_markdown_table,
    generate_reproduction_table,
)
from pipeline.batch_scheduler import (
    AdaptiveKeyframeConfig,
    AdaptiveKeyframeController,
    BatchInferenceScheduler,
)
from pipeline.multi_stream import MultiStreamBatchCoordinator, StreamSpec
from pipeline.rules_engine import (
    CampusRoadRulesEngine,
    GeoFenceZone,
    HomographyMapper,
    LaneConfig,
    LoiteringConfig,
    point_in_polygon,
)
from pipeline.video_stream import FramePacket, RingBuffer, VideoStreamReader


def test_ring_buffer_backpressure():
    rb = RingBuffer(capacity=3, drop_policy="oldest")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    for i in range(1, 6):
        rb.put(FramePacket(frame_id=i, timestamp=float(i), frame=frame))

    assert rb.size == 3
    assert rb.total_pushed == 5
    assert rb.dropped_frames == 2  # frames 1 and 2 dropped
    batch = rb.get_batch(max_batch=5, timeout=0.01)
    assert [p.frame_id for p in batch] == [3, 4, 5]


def test_video_stream_and_batch_scheduler():
    frames = [np.zeros((720, 1280, 3), dtype=np.uint8) for _ in range(12)]
    reader = VideoStreamReader(source=frames, buffer_capacity=16)
    detector = MockDetectorAdapter.create_synthetic_campus_scenario(num_frames=12)
    tracker = ByteTracker(ByteTrackerConfig())
    rules = CampusRoadRulesEngine(
        lanes=[
            LaneConfig(
                lane_id="lane_eastbound",
                name="Eastbound",
                polygon=[(0, 200), (1280, 200), (1280, 400), (0, 400)],
                direction=(1.0, 0.0),
            )
        ]
    )

    scheduler = BatchInferenceScheduler(
        detector=detector,
        tracker=tracker,
        rules_engine=rules,
        stream_reader=reader,
        batch_size=4,
    )

    collected = []
    with reader:
        scheduler.start(on_result=collected.append)
        while not reader.is_eof or len(collected) < 12:
            if scheduler._thread is not None and not scheduler._thread.is_alive():
                break
        scheduler.stop()

    assert len(collected) == 12
    metrics = scheduler.get_metrics()
    assert metrics["processed_frames"] == 12
    assert metrics["detect_ms"] >= 0.0
    assert metrics["track_ms"] >= 0.0
    assert metrics["rules_ms"] >= 0.0


def test_homography_and_rules_engine():
    # Fit homography mapping 100 px -> 5 meters (scale 0.05 m/px)
    px = [(0, 0), (200, 0), (200, 200), (0, 200)]
    wm = [(0, 0), (10, 0), (10, 10), (0, 10)]
    mapper = HomographyMapper.from_point_pairs(px, wm)
    pt = mapper.pixel_to_meter((100, 100))
    assert np.allclose(pt, [5.0, 5.0], atol=1e-4)

    assert point_in_polygon((50, 50), [(0, 0), (100, 0), (100, 100), (0, 100)])
    assert not point_in_polygon((150, 50), [(0, 0), (100, 0), (100, 100), (0, 100)])

    rules = CampusRoadRulesEngine(
        homography=mapper,
        lanes=[
            LaneConfig(
                lane_id="lane_1",
                name="Main East",
                polygon=[(0, 0), (1000, 0), (1000, 400), (0, 400)],
                direction=(1.0, 0.0),
                cos_threshold=-0.3,
                min_displacement_px=15.0,
            )
        ],
        zones=[
            GeoFenceZone(
                zone_id="no_entry",
                name="Pedestrian Plaza",
                polygon=[(400, 450), (700, 450), (700, 700), (400, 700)],
                allowed_classes=["person"],
            )
        ],
        loitering_configs=[
            LoiteringConfig(
                zone_id="loiter_1",
                max_speed_mps=0.4,
                min_dwell_seconds=3.0,
            )
        ],
        cooldown_seconds=10.0,
    )

    kf = NSAKalmanFilter()

    def make_track(tid: int, x1: float, y1: float, x2: float, y2: float, cls_name: str, frame_id: int) -> STrack:
        t = STrack(Detection([x1, y1, x2, y2], 0.9, 0, cls_name))
        t.activate(kf, frame_id=1, track_id=tid)
        t.is_activated = True
        t.frame_id = frame_id
        return t

    all_violations = []
    for f in range(1, 10):
        ts = f * 1.0  # 1 second per step
        # Track 1: wrong-way car moving left in eastbound lane
        t_wrong = make_track(1, 600 - f * 20, 150, 660 - f * 20, 200, "car", f)
        # Track 2: car intruding into pedestrian plaza
        t_intrude = make_track(2, 500, 500, 560, 560, "car", f)
        # Track 3: loitering person stationary at (200, 600)
        t_loiter = make_track(3, 190, 540, 210, 600, "person", f)

        vios = rules.evaluate([t_wrong, t_intrude, t_loiter], frame_id=f, timestamp=ts)
        all_violations.extend(vios)

    rule_types = {v.rule_type for v in all_violations}
    assert "wrong_way" in rule_types
    assert "intrusion" in rule_types
    assert "loitering" in rule_types


def test_ablation_suite_and_reproduction_table(tmp_path: Path):
    results = generate_reproduction_table(
        output_dir=tmp_path, num_frames=80, num_targets=6, seed=42
    )
    assert len(results) == 6
    names = [r.name for r in results]
    assert "Baseline (Standard ByteTrack)" in names
    assert "Full (Enhanced ByteTrack)" in names

    # Full model should outperform or match baseline on IDSW & IDF1
    baseline = results[0]
    full = results[-1]
    assert baseline.use_recovery_pool is True
    assert full.data_kind == "synthetic"
    assert full.metrics.idf1 >= baseline.metrics.idf1
    assert full.metrics.id_switches <= baseline.metrics.id_switches

    md = format_markdown_table(results)
    assert "MOTA" in md and "IDF1" in md and "IDSW" in md
    assert (tmp_path / "ablation_table.md").exists()
    assert (tmp_path / "ablation_results.json").exists()


class _BoxPixelDetector(DetectorAdapter):
    """Detects bright white rectangles drawn in synthetic frames and counts batch calls."""

    def __init__(self) -> None:
        self.batch_calls: int = 0
        self.total_frames_detected: int = 0

    def detect(self, frame: np.ndarray):
        ys, xs = np.where(frame[:, :, 0] > 200)
        if len(xs) == 0:
            return []
        return [
            Detection(
                bbox=[float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                conf=0.92,
                cls_id=2,
                cls_name="car",
            )
        ]

    def detect_batch(self, frames):
        self.batch_calls += 1
        self.total_frames_detected += len(frames)
        return [self.detect(f) for f in frames]


def _make_box_frame(cx: int, cy: int, w: int = 60, h: int = 40, size=(240, 640)) -> np.ndarray:
    img = np.zeros((size[0], size[1], 3), dtype=np.uint8)
    x1 = max(0, cx - w // 2)
    y1 = max(0, cy - h // 2)
    x2 = min(size[1] - 1, cx + w // 2)
    y2 = min(size[0] - 1, cy + h // 2)
    img[y1:y2, x1:x2] = 255
    return img


def test_ring_buffer_block_policy():
    rb = RingBuffer(capacity=2, drop_policy="block")
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    pushed = []

    def producer():
        for i in range(1, 6):
            ok = rb.put(FramePacket(frame_id=i, timestamp=float(i), frame=frame))
            pushed.append((i, ok))
        rb.close()

    t = threading.Thread(target=producer, daemon=True)
    t.start()

    got = []
    while True:
        pkt = rb.get(timeout=0.5)
        if pkt is None:
            break
        got.append(pkt.frame_id)
        time.sleep(0.005)

    t.join(timeout=2.0)
    assert got == [1, 2, 3, 4, 5]
    assert rb.dropped_frames == 0
    assert all(ok for _, ok in pushed)


def test_image_sequence_directory_reader(tmp_path: Path):
    seq_dir = tmp_path / "img1"
    seq_dir.mkdir()
    (seq_dir / "ignore_me.txt").write_text("not an image", encoding="utf-8")

    # Natural sort check: frame1, frame2, frame10, frame11 (lexicographic would yield 1, 10, 11, 2)
    indices = [1, 2, 10, 11]
    for val in indices:
        img = np.full((120, 160, 3), val, dtype=np.uint8)
        cv2.imwrite(str(seq_dir / f"frame{val}.png"), img)

    reader = VideoStreamReader(
        source=seq_dir,
        buffer_capacity=8,
        target_fps=60.0,
        loop_synthetic=False,
    )
    packets = []
    with reader:
        while True:
            pkt = reader.buffer.get(timeout=0.5)
            if pkt is not None:
                packets.append(pkt)
            elif reader.is_eof:
                break

    assert [int(p.frame[0, 0, 0]) for p in packets] == indices
    assert reader.frame_width == 160
    assert reader.frame_height == 120
    assert reader.source_fps == 60.0

    # Looping check
    loop_reader = VideoStreamReader(
        source=str(seq_dir),
        buffer_capacity=8,
        loop_synthetic=True,
    )
    looped = []
    with loop_reader:
        for _ in range(7):
            pkt = loop_reader.buffer.get(timeout=0.5)
            assert pkt is not None
            looped.append(int(pkt.frame[0, 0, 0]))
    assert looped[:4] == indices
    assert looped[4:] == indices[:3]


def test_rules_engine_from_zone_dicts_and_get_roi_polygons():
    zones = [
        {
            "id": 10,
            "name": "Eastbound Lane",
            "zone_type": "lane",
            "polygon": [[0, 0], [1000, 0], [1000, 400], [0, 400]],
            "direction_vector": [1.0, 0.0],
        },
        {
            "id": 20,
            "name": "Plaza",
            "zone_type": "intrusion",
            "polygon": [[400, 450], [700, 450], [700, 700], [400, 700]],
            "allowed_classes": ["person"],
        },
        {
            "id": 30,
            "name": "Gate Loiter",
            "zone_type": "loitering",
            "polygon": [[100, 500], [300, 500], [300, 650], [100, 650]],
            "speed_limit": 0.4,
            "dwell_limit_sec": 3.0,
        },
    ]
    mapper = HomographyMapper(meters_per_pixel=0.05)
    rules = CampusRoadRulesEngine.from_zone_dicts(zones, homography=mapper, cooldown_seconds=10.0)

    assert len(rules.lanes) == 1
    assert len(rules.zones) == 1
    assert len(rules.loitering_configs) == 1
    rois = rules.get_roi_polygons()
    assert len(rois) == 3

    kf = NSAKalmanFilter()

    def make_track(tid: int, x1: float, y1: float, x2: float, y2: float, cls_name: str, f: int) -> STrack:
        t = STrack(Detection([x1, y1, x2, y2], 0.9, 0, cls_name))
        t.activate(kf, frame_id=1, track_id=tid)
        t.is_activated = True
        t.frame_id = f
        return t

    vios = []
    for f in range(1, 8):
        ts = float(f)
        t_wrong = make_track(1, 600 - f * 20, 150, 660 - f * 20, 200, "car", f)
        t_intrude = make_track(2, 500, 500, 560, 560, "car", f)
        t_loiter = make_track(3, 190, 540, 210, 600, "person", f)
        vios.extend(rules.evaluate([t_wrong, t_intrude, t_loiter], frame_id=f, timestamp=ts))

    assert {v.rule_type for v in vios} == {"wrong_way", "intrusion", "loitering"}


def test_adaptive_keyframe_controller_and_skip_continuity():
    # 1. Unit checks for AdaptiveKeyframeController triggers
    disabled_ctrl = AdaptiveKeyframeController(AdaptiveKeyframeConfig(enabled=False, max_skip_stride=3))
    assert disabled_ctrl.should_detect(1, []) == (True, "always")

    cfg = AdaptiveKeyframeConfig(
        enabled=True,
        max_skip_stride=3,
        accel_trigger_px=18.0,
        roi_guard_margin_px=30.0,
    )
    ctrl = AdaptiveKeyframeController(cfg)
    assert ctrl.should_detect(1, [])[0] is True

    kf = NSAKalmanFilter()
    t1 = STrack(Detection([90, 90, 130, 130], 0.9, 2, "car"))
    t1.activate(kf, frame_id=1, track_id=1)
    assert t1.mean is not None
    t1.mean[4] = 5.0
    t1.mean[5] = 0.0
    ctrl.record_keyframe(1, [t1])

    # Within stride, steady velocity, outside ROI -> skip
    roi = [[(500.0, 500.0), (600.0, 500.0), (600.0, 600.0), (500.0, 600.0)]]
    assert ctrl.should_detect(2, [t1], roi_polygons=roi) == (False, "kalman_skip")
    # Stride limit reached (4 - 1 >= 3)
    should, reason = ctrl.should_detect(4, [t1], roi_polygons=roi)
    assert should is True and reason == "stride_limit"

    # Velocity jump >= accel_trigger_px
    t1.mean[4] = 28.0
    should, reason = ctrl.should_detect(2, [t1], roi_polygons=roi)
    assert should is True and reason == "accel_trigger"

    # Reset velocity, move bottom_center within 30px margin of ROI bbox [500, 600]
    t1.mean[4] = 5.0
    t1.mean[0] = 520.0
    t1.mean[1] = 465.0  # h=40 => bottom_center.y = 485 >= 500 - 30
    should, reason = ctrl.should_detect(2, [t1], roi_polygons=roi)
    assert should is True and reason == "roi_guard"

    # 2. End-to-end BatchInferenceScheduler continuity across skipped frames
    detector = _BoxPixelDetector()
    tracker = ByteTracker(ByteTrackerConfig())
    scheduler = BatchInferenceScheduler(
        detector=detector,
        tracker=tracker,
        rules_engine=None,
        batch_size=4,
        adaptive_config=AdaptiveKeyframeConfig(enabled=True, max_skip_stride=3),
        include_frame_in_result=True,
    )

    packets = [
        FramePacket(
            frame_id=f,
            timestamp=f * 0.04,
            frame=_make_box_frame(cx=80 + f * 10, cy=120),
        )
        for f in range(1, 13)
    ]

    all_results = []
    observed_centers = []
    # Feed batch 1 (1 packet to establish track), then batches of 4
    for chunk in (packets[:1], packets[1:5], packets[5:9], packets[9:12]):
        batch_res = scheduler.process_batch(chunk)
        all_results.extend(batch_res)
        for res in batch_res:
            observed_centers.append((res.frame_id, res.tracks[0].center[0]))

    assert len(all_results) == 12
    assert detector.total_frames_detected < 12
    skipped_results = [r for r in all_results if not r.is_keyframe]
    assert len(skipped_results) >= 4
    assert all(r.keyframe_reason == "kalman_skip" for r in skipped_results)
    assert all(r.latency.detect_ms == 0.0 for r in skipped_results)
    assert all(r.frame is not None for r in all_results)

    # Track ID stays continuous and keeps advancing smoothly across keyframes & skipped frames
    track_ids = {r.tracks[0].track_id for r in all_results if r.tracks}
    assert len(track_ids) == 1
    assert all(len(r.tracks) == 1 for r in all_results)
    final_track = all_results[-1].tracks[0]
    history_xs = [cx for _, cx, _ in final_track.history]
    assert len(history_xs) == 12
    assert history_xs[-1] > history_xs[0]
    # After the second keyframe establishes non-zero velocity, skipped frames keep advancing cx
    second_kf_idx = next(i for i, r in enumerate(all_results) if i > 0 and r.is_keyframe)
    for i in range(second_kf_idx + 1, len(history_xs)):
        assert history_xs[i] > history_xs[i - 1]

    metrics = scheduler.get_metrics()
    assert metrics["processed_frames"] == 12
    assert metrics["keyframes"] + metrics["skipped_detect_frames"] == 12
    assert metrics["skipped_detect_frames"] == len(skipped_results)
    assert 0.0 < metrics["detect_skip_ratio"] < 1.0


def test_multi_stream_coordinator_4streams_and_backpressure():
    detector = _BoxPixelDetector()
    coord = MultiStreamBatchCoordinator(
        detector=detector,
        max_batch_size=8,
        poll_timeout=0.01,
        include_frame_in_result=False,
    )

    num_frames = 12
    collected_by_stream = {f"cam_{i}": [] for i in range(4)}
    readers = []

    for i in range(4):
        frames = [_make_box_frame(cx=60 + f * 8, cy=100 + i * 20) for f in range(1, num_frames + 1)]
        reader = VideoStreamReader(source=frames, buffer_capacity=16)
        readers.append(reader)
        rules = (
            CampusRoadRulesEngine(
                lanes=[
                    LaneConfig(
                        lane_id="east",
                        name="East",
                        polygon=[(0, 0), (640, 0), (640, 240), (0, 240)],
                        direction=(1.0, 0.0),
                    )
                ]
            )
            if i == 0
            else None
        )
        adaptive = (
            AdaptiveKeyframeConfig(enabled=True, max_skip_stride=2)
            if i == 3
            else None
        )
        coord.add_stream(
            StreamSpec(
                stream_id=f"cam_{i}",
                reader=reader,
                tracker=ByteTracker(ByteTrackerConfig()),
                rules_engine=rules,
                adaptive_config=adaptive,
                on_result=lambda sid, res: collected_by_stream[sid].append(res),
            )
        )

    for r in readers:
        r.start()
    coord.start()
    try:
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            if all(len(collected_by_stream[f"cam_{i}"]) == num_frames for i in range(4)):
                break
            if coord._thread is not None and not coord._thread.is_alive():
                break
            time.sleep(0.01)
    finally:
        coord.stop()
        for r in readers:
            r.stop()

    for i in range(4):
        sid = f"cam_{i}"
        assert len(collected_by_stream[sid]) == num_frames
        # Each stream tracks its own single moving target cleanly
        tids = {r.tracks[0].track_id for r in collected_by_stream[sid] if r.tracks}
        assert len(tids) == 1

    agg = coord.get_metrics()
    assert agg["stream_count"] == 4
    assert agg["processed_frames"] == 4 * num_frames
    assert agg["fps"] > 0.0
    assert agg["p95_ms"] >= 0.0
    assert agg["avg_batch_size"] > 1.0
    assert detector.batch_calls < 4 * num_frames
    assert set(agg["streams"].keys()) == {"cam_0", "cam_1", "cam_2", "cam_3"}
    assert coord.get_metrics("cam_3")["detect_skip_ratio"] > 0.0

    # Round-robin fairness & backpressure test (capacity=2, max_batch_size=2 across 4 streams)
    rr_detector = _BoxPixelDetector()
    rr_coord = MultiStreamBatchCoordinator(detector=rr_detector, max_batch_size=2, poll_timeout=0.0)
    rr_readers = []
    for i in range(4):
        reader = VideoStreamReader(source=[], buffer_capacity=2, drop_policy="oldest")
        for f in range(1, 5):  # push 4 frames into capacity=2 => 2 dropped per stream
            reader.buffer.put(
                FramePacket(frame_id=f, timestamp=float(f), frame=_make_box_frame(cx=60 + f * 8, cy=100))
            )
        reader.buffer.close()
        rr_readers.append(reader)
        rr_coord.add_stream(
            StreamSpec(stream_id=f"rr_{i}", reader=reader, tracker=ByteTracker(ByteTrackerConfig()))
        )

    step1 = rr_coord.step_once()
    step2 = rr_coord.step_once()
    assert set(step1.keys()) == {"rr_0", "rr_1"}
    assert set(step2.keys()) == {"rr_2", "rr_3"}
    rr_metrics = rr_coord.get_metrics()
    assert rr_metrics["dropped_frames"] == 8
    assert rr_coord.remove_stream("rr_0") is True
    assert rr_coord.remove_stream("rr_0") is False
