"""Unit and integration tests for core detection, Kalman, matching, and ByteTrack."""

from __future__ import annotations

import numpy as np
import pytest

from core.byte_tracker import ByteTracker, ByteTrackerConfig
from core.detector_adapter import (
    Detection,
    FramePreprocessor,
    MockDetectorAdapter,
    ONNXDetectorAdapter,
    ROISlicePreprocessor,
    TensorRTDetectorAdapter,
    YOLODetectorAdapter,
    create_detector_adapter,
    decode_yolo_output,
    detections_to_numpy,
    gpu_letterbox_batch,
    letterbox,
    nms_boxes,
)
from core.gmc import GlobalMotionCompensator, warp_kalman_state
from core.kalman_filter import KalmanFilter, NSAKalmanFilter
from core.matching import (
    appearance_cosine_distance,
    bbox_diou_matrix,
    bbox_iou_matrix,
    class_penalty_matrix,
    extract_hsv_histogram,
    extract_hsv_histogram_from_hsv,
    fuse_score,
    iou_distance,
    linear_assignment,
    prepare_downsampled_hsv,
    velocity_direction_cost,
)
from core.trajectory_pool import STrack, TrackState, TrajectoryPool


def test_detection_and_mock_adapter():
    det = Detection([10, 20, 50, 100], 0.9, 2, "car")
    assert det.area == pytest.approx(40.0 * 80.0)
    assert np.allclose(det.tlwh, [10, 20, 40, 80])
    assert np.allclose(det.xywh, [30, 60, 40, 80])
    assert np.allclose(det.to_xyah(), [30, 60, 0.5, 80])

    arr = detections_to_numpy([det])
    assert arr.shape == (1, 6)
    assert arr[0, 4] == pytest.approx(0.9)

    mock = MockDetectorAdapter.create_synthetic_campus_scenario(num_frames=5)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    d0 = mock.detect(frame)
    assert len(d0) == 3
    assert {d.cls_name for d in d0} == {"car", "motorcycle", "person"}

    # Lazy YOLO adapter should instantiate without weights on disk
    yolo = YOLODetectorAdapter(model_path="nonexistent.pt", lazy_load=True)
    assert yolo.model_path == "nonexistent.pt"


def test_nsa_kalman_resists_low_conf_velocity_pull():
    kf_std = KalmanFilter()
    kf_nsa = NSAKalmanFilter()

    z0 = np.array([100.0, 200.0, 0.5, 80.0])
    m_std, c_std = kf_std.initiate(z0)
    m_nsa, c_nsa = kf_nsa.initiate(z0)

    # Establish steady rightward velocity vx = +10 px/frame over 5 high-conf frames
    for step in range(1, 6):
        z = np.array([100.0 + 10.0 * step, 200.0, 0.5, 80.0])
        m_std, c_std = kf_std.predict(m_std, c_std)
        m_std, c_std = kf_std.update(m_std, c_std, z, confidence=0.95)

        m_nsa, c_nsa = kf_nsa.predict(m_nsa, c_nsa)
        m_nsa, c_nsa = kf_nsa.update(m_nsa, c_nsa, z, confidence=0.95)

    # Inject a noisy, low-confidence detection jumping backward by 35 px
    z_noisy = np.array([125.0, 200.0, 0.5, 80.0])
    m_std, c_std = kf_std.predict(m_std, c_std)
    m_std_after, _ = kf_std.update(m_std, c_std, z_noisy, confidence=0.12)

    m_nsa, c_nsa = kf_nsa.predict(m_nsa, c_nsa)
    m_nsa_after, _ = kf_nsa.update(m_nsa, c_nsa, z_noisy, confidence=0.12)

    # NSA-Kalman should preserve positive velocity much better than standard KF
    assert m_nsa_after[4] > m_std_after[4]
    assert m_nsa_after[4] > 5.0


def test_iou_diou_and_linear_assignment():
    a = np.array([[0, 0, 10, 10], [100, 100, 120, 120]], dtype=np.float64)
    b = np.array([[0, 0, 10, 10], [150, 150, 170, 170]], dtype=np.float64)

    iou = bbox_iou_matrix(a, b)
    diou = bbox_diou_matrix(a, b)
    assert iou[0, 0] == pytest.approx(1.0)
    assert diou[0, 0] == pytest.approx(1.0)
    assert iou[1, 1] == pytest.approx(0.0)
    assert diou[1, 1] < 0.0  # non-overlapping boxes get center-distance penalty

    cost = iou_distance(a, b, use_diou=True)
    matches, u_a, u_b = linear_assignment(cost, thresh=0.8)
    assert matches.tolist() == [[0, 0]]
    assert u_a.tolist() == [1]
    assert u_b.tolist() == [1]

    # Empty matrix edge case
    m_empty, ua_e, ub_e = linear_assignment(np.empty((0, 3)), thresh=0.8)
    assert len(m_empty) == 0
    assert len(ua_e) == 0
    assert ub_e.tolist() == [0, 1, 2]

    # Score fusion
    dets = [Detection([0, 0, 10, 10], 0.5, 0), Detection([150, 150, 170, 170], 0.9, 0)]
    fused = fuse_score(iou_distance(a, b, use_diou=False), dets)
    assert fused[0, 0] == pytest.approx(0.5)


def test_trajectory_pool_lifecycle():
    kf = NSAKalmanFilter()
    pool = TrajectoryPool(max_time_lost=5)
    t1 = STrack(Detection([10, 10, 50, 90], 0.9, 0, "person"))
    tid = pool.next_id()
    t1.activate(kf, frame_id=1, track_id=tid)
    assert t1.state == TrackState.TRACKED
    assert t1.is_activated

    t1.mark_lost()
    assert t1.state == TrackState.LOST
    pool.lost_stracks.append(t1)

    # Not expired yet at frame 6 (6 - 1 = 5 <= 5)
    assert len(pool.prune_lost(6)) == 0
    # Expired at frame 7 (7 - 1 = 6 > 5)
    removed = pool.prune_lost(7)
    assert len(removed) == 1
    assert removed[0].state == TrackState.REMOVED


def test_bytetrack_two_stage_and_occlusion_recovery():
    tracker = ByteTracker(
        ByteTrackerConfig(
            track_thresh=0.5,
            low_thresh=0.1,
            max_time_lost=20,
            use_nsa=True,
            use_diou=True,
            use_recovery_pool=True,
        )
    )

    initial_id = None
    # Frames 1..10: steady motion with high confidence
    for f in range(1, 11):
        cx = 100.0 + f * 8.0
        dets = [Detection([cx - 25, 200, cx + 25, 280], 0.9, 2, "car")]
        tracks = tracker.update(dets, frame_id=f)
        assert len(tracks) == 1
        if initial_id is None:
            initial_id = tracks[0].track_id
        assert tracks[0].track_id == initial_id

    # Frames 11..14: Stage-2 low-confidence detections (0.25 < 0.5) should keep track alive!
    for f in range(11, 15):
        cx = 100.0 + f * 8.0
        dets = [Detection([cx - 25, 200, cx + 25, 280], 0.25, 2, "car")]
        tracks = tracker.update(dets, frame_id=f)
        assert len(tracks) == 1
        assert tracks[0].track_id == initial_id

    # Frames 15..22: Full occlusion (0 detections) -> track moves to lost pool & extrapolates
    for f in range(15, 23):
        tracks = tracker.update([], frame_id=f)
        assert len(tracks) == 0

    # Frame 23: Target reappears at extrapolated position -> recovered with SAME track_id!
    cx = 100.0 + 23 * 8.0
    tracks = tracker.update(
        [Detection([cx - 25, 200, cx + 25, 280], 0.88, 2, "car")], frame_id=23
    )
    assert len(tracks) == 1
    assert tracks[0].track_id == initial_id


def test_missing_weights_raise_file_not_found_without_download():
    with pytest.raises(FileNotFoundError):
        YOLODetectorAdapter(model_path="definitely_missing_yolov8n.pt", lazy_load=False)
    with pytest.raises(FileNotFoundError):
        YOLODetectorAdapter(model_path="", lazy_load=False)
    with pytest.raises(FileNotFoundError):
        ONNXDetectorAdapter(model_path="definitely_missing_model.onnx", lazy_load=False)
    with pytest.raises(FileNotFoundError):
        create_detector_adapter("yolo", model_path="missing.pt", lazy_load=False)
    with pytest.raises(FileNotFoundError):
        create_detector_adapter("onnx", model_path="missing.onnx", lazy_load=False)
    with pytest.raises(ValueError):
        create_detector_adapter("unknown_backend")

    mock = create_detector_adapter("mock")
    assert isinstance(mock, MockDetectorAdapter)
    assert len(mock.detect(np.zeros((720, 1280, 3), dtype=np.uint8))) == 3


def test_onnx_letterbox_nms_and_decode_layouts():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    padded, scale, (pad_x, pad_y) = letterbox(frame, 640)
    assert padded.shape == (640, 640, 3)
    assert scale == pytest.approx(0.5)
    assert (pad_x, pad_y) == pytest.approx((0.0, 140.0))

    # Map original box [200, 100, 400, 300] -> net cx=150, cy=240, w=100, h=100
    # Plus overlapping lower-score same-class box (suppressed) & different-class box (kept)
    rows = np.array(
        [
            [150.0, 240.0, 100.0, 100.0, 0.90, 0.05, 0.01],  # cls 0, score 0.90
            [155.0, 242.0, 100.0, 100.0, 0.70, 0.02, 0.01],  # cls 0, overlaps box 0 -> suppressed
            [150.0, 240.0, 100.0, 100.0, 0.05, 0.85, 0.01],  # cls 1, same coords -> kept (class-aware!)
            [400.0, 300.0, 60.0, 80.0, 0.10, 0.05, 0.10],    # below conf_thresh 0.25 -> filtered
        ],
        dtype=np.float32,
    )
    names = {0: "person", 1: "bicycle", 2: "car"}

    # Test both (N, C) and (C, N) layouts
    dets_nc = decode_yolo_output(
        rows,
        orig_shape=(720, 1280),
        scale=scale,
        pad=(pad_x, pad_y),
        conf_thresh=0.25,
        iou_thresh=0.45,
        class_names=names,
    )
    dets_cn = decode_yolo_output(
        rows.T,
        orig_shape=(720, 1280),
        scale=scale,
        pad=(pad_x, pad_y),
        conf_thresh=0.25,
        iou_thresh=0.45,
        class_names=names,
    )
    for dets in (dets_nc, dets_cn):
        assert len(dets) == 2
        assert {d.cls_name for d in dets} == {"person", "bicycle"}
        assert np.allclose(dets[0].bbox, [200.0, 100.0, 400.0, 300.0], atol=1e-3)

    # Direct NMS check: same class suppressed, different class preserved
    boxes = np.array([[0, 0, 50, 50], [2, 2, 52, 52], [0, 0, 50, 50]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    cls_ids = np.array([0, 0, 2], dtype=np.int64)
    kept = nms_boxes(boxes, scores, cls_ids, iou_thresh=0.5)
    assert kept == [0, 2]


def test_onnx_adapter_batch_fp16_and_roi_preprocessor():
    class _FakeSession:
        def __init__(self, batch_dim, inp_type):
            self.calls = []
            self.batch_dim = batch_dim
            self.inp_type = inp_type

        def run(self, _outputs, feed):
            arr = next(iter(feed.values()))
            self.calls.append((arr.shape, arr.dtype))
            b = arr.shape[0]
            # Output (B, C=6, N=1): center box inside 640x640 net
            out = np.zeros((b, 6, 1), dtype=np.float32)
            out[:, 0, 0] = 320.0
            out[:, 1, 0] = 320.0
            out[:, 2, 0] = 80.0
            out[:, 3, 0] = 80.0
            out[:, 4, 0] = 0.91
            return [out]

    # Dynamic batch + float16 casting
    adapter_dyn = ONNXDetectorAdapter(
        model_path="dummy.onnx",
        class_names={0: "person", 1: "car"},
        lazy_load=True,
    )
    fake_dyn = _FakeSession(batch_dim="batch", inp_type="tensor(float16)")
    adapter_dyn._session = fake_dyn
    adapter_dyn._input_name = "images"
    adapter_dyn._input_shape = ["batch", 3, 640, 640]
    adapter_dyn._input_type = "tensor(float16)"

    frames = [np.zeros((640, 640, 3), dtype=np.uint8) for _ in range(3)]
    batch_dets = adapter_dyn.detect_batch(frames)
    assert len(batch_dets) == 3
    assert all(len(d) == 1 and d[0].cls_name == "person" for d in batch_dets)
    assert len(fake_dyn.calls) == 1
    assert fake_dyn.calls[0] == ((3, 3, 640, 640), np.float16)

    # Static batch=1 fallback loops per frame
    adapter_static = ONNXDetectorAdapter(
        model_path="dummy.onnx",
        class_names={0: "person"},
        lazy_load=True,
    )
    fake_static = _FakeSession(batch_dim=1, inp_type="tensor(float)")
    adapter_static._session = fake_static
    adapter_static._input_name = "images"
    adapter_static._input_shape = [1, 3, 640, 640]
    adapter_static._input_type = "tensor(float)"

    batch_dets_static = adapter_static.detect_batch(frames[:2])
    assert len(batch_dets_static) == 2
    assert len(fake_static.calls) == 2
    assert fake_static.calls[0] == ((1, 3, 640, 640), np.float32)

    # FramePreprocessor / ROISlicePreprocessor
    default_pre = ROISlicePreprocessor()
    assert not default_pre.enabled
    big_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    slices_off = default_pre.preprocess(big_frame)
    assert len(slices_off) == 1 and slices_off[0][1] == (0, 0)

    roi_pre = ROISlicePreprocessor(rois=[(100, 50, 740, 690)], enabled=True)
    assert isinstance(roi_pre, FramePreprocessor)
    slices_on = roi_pre.preprocess(big_frame)
    assert len(slices_on) == 1
    assert slices_on[0][0].shape == (640, 640, 3)
    assert slices_on[0][1] == (100, 50)

    adapter_dyn.preprocessor = roi_pre
    roi_dets = adapter_dyn.detect(big_frame)
    assert len(roi_dets) == 1
    assert np.allclose(roi_dets[0].bbox, [380.0, 330.0, 460.0, 410.0], atol=1e-3)


def test_nsa_kalman_strongsort_mode():
    kf_strong = NSAKalmanFilter(mode="strongsort")
    assert kf_strong.noise_scale(0.12) > kf_strong.noise_scale(0.95) * 3.0

    kf_std = KalmanFilter()
    z0 = np.array([100.0, 200.0, 0.5, 80.0])
    m_std, c_std = kf_std.initiate(z0)
    m_str, c_str = kf_strong.initiate(z0)

    for step in range(1, 6):
        z = np.array([100.0 + 10.0 * step, 200.0, 0.5, 80.0])
        m_std, c_std = kf_std.predict(m_std, c_std)
        m_std, c_std = kf_std.update(m_std, c_std, z, confidence=0.95)
        m_str, c_str = kf_strong.predict(m_str, c_str)
        m_str, c_str = kf_strong.update(m_str, c_str, z, confidence=0.95)

    z_noisy = np.array([125.0, 200.0, 0.5, 80.0])
    m_std, c_std = kf_std.predict(m_std, c_std)
    m_std_after, _ = kf_std.update(m_std, c_std, z_noisy, confidence=0.12)
    m_str, c_str = kf_strong.predict(m_str, c_str)
    m_str_after, _ = kf_strong.update(m_str, c_str, z_noisy, confidence=0.12)
    assert m_str_after[4] > m_std_after[4]
    assert m_str_after[4] > 5.0


def test_class_aware_prevents_person_car_swap():
    pen = class_penalty_matrix([0, 2], [2, 0], penalty=0.5)
    assert np.allclose(pen, [[0.5, 0.0], [0.0, 0.5]])

    # Track 1 = person (cls 0), Track 2 = car (cls 2) heavily overlapping during crossing
    cfg_on = ByteTrackerConfig.official_baseline()
    cfg_on.use_class_aware = True
    tracker = ByteTracker(cfg_on)

    t_init = tracker.update(
        [
            Detection([100, 100, 150, 200], 0.9, 0, "person"),
            Detection([100, 100, 160, 200], 0.9, 2, "car"),
        ],
        frame_id=1,
    )
    id_map = {t.cls_name: t.track_id for t in t_init}
    assert set(id_map.keys()) == {"person", "car"}

    # Frame 2: boxes overlap heavily and slightly swap center priority
    t_cross = tracker.update(
        [
            Detection([105, 100, 165, 200], 0.92, 2, "car"),
            Detection([102, 100, 152, 200], 0.88, 0, "person"),
        ],
        frame_id=2,
    )
    cross_map = {t.cls_name: t.track_id for t in t_cross}
    assert cross_map == id_map


def test_vdc_and_oru_curve_occlusion_recovery():
    vdc = velocity_direction_cost(
        track_velocities=np.array([[1.0, 0.0], [0.0, 0.0]]),
        track_anchors=np.array([[100.0, 100.0], [100.0, 100.0]]),
        det_centers=np.array([[120.0, 100.0], [100.0, 120.0], [80.0, 100.0]]),
    )
    assert vdc[0, 0] == pytest.approx(0.0)
    assert vdc[0, 1] == pytest.approx(0.5)
    assert vdc[0, 2] == pytest.approx(1.0)
    assert np.allclose(vdc[1], 0.0)  # zero velocity norm -> 0.0

    # Unit test ORU directly on STrack during curved occlusion
    kf_no_oru = NSAKalmanFilter()
    kf_oru = NSAKalmanFilter()
    t_no_oru = STrack(Detection([100, 100, 140, 180], 0.9, 2, "car"))
    t_oru = STrack(Detection([100, 100, 140, 180], 0.9, 2, "car"))
    t_no_oru.activate(kf_no_oru, frame_id=1, track_id=1)
    t_oru.activate(kf_oru, frame_id=1, track_id=1)

    for f in range(2, 6):
        cx = 120.0 + (f - 1) * 8.0
        det = STrack(Detection([cx - 20, 100, cx + 20, 180], 0.9, 2, "car"))
        t_no_oru.update(det, frame_id=f)
        t_oru.update(det, frame_id=f)

    assert t_oru.velocity_vector == pytest.approx((1.0, 0.0), abs=1e-4)

    # Occlusion frames 6..19 where target turns downward (dy = +8 per frame, vx -> 0)
    t_no_oru.mark_lost()
    t_oru.mark_lost()
    for _ in range(6, 20):
        t_no_oru.predict()
        t_oru.predict()

    # Reappear at frame 20 shifted downward by +120 px (vx ~ 0, vy ~ 8.0)
    reappear = STrack(Detection([132, 220, 172, 300], 0.9, 2, "car"))
    t_no_oru.re_activate(reappear, frame_id=20, use_oru=False)
    t_oru.re_activate(reappear, frame_id=20, use_oru=True)

    # ORU adapts downward velocity (vy ~ 8.0), reduces stale vx, and tightens covariance
    assert t_oru.mean[5] > 5.0
    assert abs(t_oru.mean[4]) < abs(t_no_oru.mean[4])
    assert float(np.trace(t_oru.covariance)) < float(np.trace(t_no_oru.covariance))
    assert abs(t_oru.center[1] - 260.0) < abs(t_no_oru.center[1] - 260.0)

    # End-to-end ByteTracker VDC + ORU recovery after crossing + occlusion
    tracker = ByteTracker(ByteTrackerConfig.full_enhanced())
    tid = None
    for f in range(1, 9):
        cx = 100.0 + f * 10.0
        cy = 200.0
        ts = tracker.update([Detection([cx - 20, cy - 30, cx + 20, cy + 30], 0.9, 2, "car")], frame_id=f)
        assert len(ts) == 1
        tid = tid or ts[0].track_id
    for f in range(9, 13):
        assert tracker.update([], frame_id=f) == []
    # Reappear smoothly curved downward (dx=+10/frame, dy=+3/frame since frame 8)
    cx_r = 100.0 + 13 * 10.0
    cy_r = 200.0 + 5 * 3.0
    ts_r = tracker.update(
        [Detection([cx_r - 20, cy_r - 30, cx_r + 20, cy_r + 30], 0.9, 2, "car")],
        frame_id=13,
    )
    assert len(ts_r) == 1
    assert ts_r[0].track_id == tid


def test_lazy_appearance_gating_on_crossing_colored_targets():
    assert extract_hsv_histogram(None, np.array([0, 0, 20, 20])) is None
    tiny = np.zeros((10, 10, 3), dtype=np.uint8)
    assert extract_hsv_histogram(tiny, np.array([0, 0, 3, 8])) is None

    dist_none = appearance_cosine_distance([None], [np.array([1.0, 0.0])])
    assert dist_none[0, 0] == pytest.approx(0.5)

    def make_scene(box_red, box_blue):
        img = np.zeros((240, 360, 3), dtype=np.uint8)
        # Draw Red on upper-left/lower-left strip inside box_red and Blue inside box_blue
        # When overlapping, paint non-overlapping left/right regions first so each crop retains distinct color
        rx1, ry1, rx2, ry2 = [int(v) for v in box_red]
        bx1, by1, bx2, by2 = [int(v) for v in box_blue]
        rmid = (ry1 + ry2) // 2
        bmid = (by1 + by2) // 2
        img[ry1:rmid, rx1:rx2] = (0, 0, 255)      # Red top
        img[rmid:ry2, rx1:rx2] = (0, 255, 255)    # Yellow bottom
        # Paint blue target into its non-occluded vertical slice or full box if disjoint
        img[by1:bmid, bx1:bx2] = (255, 0, 0)      # Blue top
        img[bmid:by2, bx1:bx2] = (0, 255, 0)      # Green bottom
        # Preserve Red target's own left/right visible portion if overlapping
        if rx1 < bx2 and bx1 < rx2:
            split = (rx1 + rx2 + bx1 + bx2) // 4
            if rx1 < split:
                img[ry1:rmid, rx1:min(rx2, split)] = (0, 0, 255)
                img[rmid:ry2, rx1:min(rx2, split)] = (0, 255, 255)
        return img

    tracker = ByteTracker(ByteTrackerConfig.full_enhanced())
    # Frames 1..6: well-separated colored targets of SAME class (person) moving toward each other
    # Red: cx = 50 + f * 15 (65..140); Blue: cx = 290 - f * 15 (275..200)
    red_id = None
    blue_id = None
    for f in range(1, 7):
        cx_r = 50 + f * 15
        cx_b = 290 - f * 15
        bx_r = [cx_r - 22, 60, cx_r + 22, 140]
        bx_b = [cx_b - 22, 62, cx_b + 22, 142]
        frame = make_scene(bx_r, bx_b)
        tracks = tracker.update(
            [Detection(bx_r, 0.9, 0, "person"), Detection(bx_b, 0.9, 0, "person")],
            frame_id=f,
            frame=frame,
        )
        assert len(tracks) == 2
        if red_id is None:
            tracks_sorted = sorted(tracks, key=lambda t: t.center[0])
            red_id = tracks_sorted[0].track_id
            blue_id = tracks_sorted[1].track_id

    # While well-separated, lazy appearance gating must NOT trigger!
    assert tracker.lazy_appearance_calls == 0

    # Frames 7..9: crossing overlap where bboxes converge at cx=170 (ambiguous spatial costs!)
    for f in (7, 8, 9):
        cx_r = 50 + f * 15   # 155, 170, 185
        cx_b = 290 - f * 15  # 185, 170, 155
        bx_r = [cx_r - 22, 60, cx_r + 22, 140]
        bx_b = [cx_b - 22, 62, cx_b + 22, 142]
        frame = make_scene(bx_r, bx_b)
        tracks = tracker.update(
            [Detection(bx_b, 0.91, 0, "person"), Detection(bx_r, 0.90, 0, "person")],
            frame_id=f,
            frame=frame,
        )
        assert len(tracks) == 2

    assert tracker.lazy_appearance_calls > 0
    assert tracker.lazy_appearance_calls < tracker.total_frames_updated

    # Frames 10..12: separated again -> Red continued right (>200), Blue continued left (<140)
    for f in range(10, 13):
        cx_r = 50 + f * 15
        cx_b = 290 - f * 15
        bx_r = [cx_r - 22, 60, cx_r + 22, 140]
        bx_b = [cx_b - 22, 62, cx_b + 22, 142]
        frame = make_scene(bx_r, bx_b)
        tracks = tracker.update(
            [Detection(bx_r, 0.9, 0, "person"), Detection(bx_b, 0.9, 0, "person")],
            frame_id=f,
            frame=frame,
        )
    by_id = {t.track_id: t for t in tracks}
    assert by_id[red_id].center[0] > 210.0
    assert by_id[blue_id].center[0] < 130.0


def test_bytetrack_predict_only_and_config_presets():
    base_cfg = ByteTrackerConfig.official_baseline()
    assert base_cfg.use_recovery_pool is True
    assert not any(
        [
            base_cfg.use_nsa,
            base_cfg.use_diou,
            base_cfg.use_class_aware,
            base_cfg.use_vdc,
            base_cfg.use_oru,
            base_cfg.use_lazy_appearance,
        ]
    )
    full_cfg = ByteTrackerConfig.full_enhanced()
    assert all(
        [
            full_cfg.use_recovery_pool,
            full_cfg.use_nsa,
            full_cfg.use_diou,
            full_cfg.use_class_aware,
            full_cfg.use_vdc,
            full_cfg.use_oru,
            full_cfg.use_lazy_appearance,
        ]
    )

    tracker = ByteTracker(full_cfg)
    tid = None
    for f in range(1, 6):
        cx = 100.0 + f * 10.0
        ts = tracker.update([Detection([cx - 20, 200, cx + 20, 260], 0.9, 2, "car")], frame_id=f)
        assert len(ts) == 1
        tid = tid or ts[0].track_id

    cx5 = tracker.pool.tracked_stracks[0].center[0]
    p6 = tracker.predict_only(frame_id=6)
    assert len(p6) == 1
    assert p6[0].track_id == tid
    assert p6[0].state == TrackState.TRACKED
    assert p6[0].frame_id == 6
    cx6 = p6[0].center[0]
    assert cx6 > cx5 + 5.0

    p7 = tracker.predict_only(frame_id=7)
    assert len(p7) == 1
    assert p7[0].frame_id == 7
    assert p7[0].center[0] > cx6 + 5.0

    # Keyframe resumption at frame 8 preserves ID
    cx8 = 100.0 + 8 * 10.0
    ts8 = tracker.update([Detection([cx8 - 20, 200, cx8 + 20, 260], 0.9, 2, "car")], frame_id=8)
    assert len(ts8) == 1
    assert ts8[0].track_id == tid


def test_gpu_letterbox_batch_and_trt_missing_file(tmp_path):
    import torch

    frames = [np.zeros((1080, 1920, 3), dtype=np.uint8) for _ in range(2)]
    frames[0][10:50, 10:50, 2] = 255  # BGR red channel

    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    tensor, metas = gpu_letterbox_batch(frames, target_hw=(640, 640), device=dev, half=True)

    assert tuple(tensor.shape) == (2, 3, 640, 640)
    expected_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    assert tensor.dtype == expected_dtype
    assert float(tensor.min().item()) >= 0.0
    assert float(tensor.max().item()) <= 1.0
    assert len(metas) == 2
    orig_h, orig_w, scale, (pad_x, pad_y) = metas[0]
    assert (orig_h, orig_w) == (1080, 1920)
    assert abs(scale - (640.0 / 1920.0)) < 1e-6
    assert pad_x == 0.0
    assert abs(pad_y - 140.0) < 1e-6

    missing_engine = tmp_path / "does_not_exist.engine"
    adapter = TensorRTDetectorAdapter(missing_engine, lazy_load=True)
    with pytest.raises(FileNotFoundError):
        adapter.detect(frames[0])


def _make_textured_canvas(width: int = 1800, height: int = 800, seed: int = 17) -> np.ndarray:
    import cv2

    rng = np.random.default_rng(seed)
    bg = np.full((height, width, 3), 30, dtype=np.uint8)
    for _ in range(550):
        x = int(rng.integers(15, width - 15))
        y = int(rng.integers(15, height - 15))
        r = int(rng.integers(4, 16))
        col = tuple(int(c) for c in rng.integers(90, 255, size=3))
        cv2.circle(bg, (x, y), r, col, -1)
        cv2.rectangle(bg, (x - 5, y - 5), (x + 5, y + 5), (240, 240, 240), 1)
    return bg


def test_gmc_methods_shift_recovery_and_warp_kalman_state(monkeypatch):
    import cv2

    bg = _make_textured_canvas(1600, 720)
    f1 = bg[100:580, 500:1140].copy()
    # Pan camera left + up => scene shifts (+50 px right, +12 px down)
    f2 = bg[88:568, 450:1090].copy()

    # Paint a moving foreground decoy box that must NOT bias background GMC
    f1_fg = f1.copy()
    f2_fg = f2.copy()
    cv2.rectangle(f1_fg, (100, 100), (220, 240), (0, 255, 0), -1)
    cv2.rectangle(f2_fg, (40, 120), (160, 260), (0, 255, 0), -1)

    for method in ("sparse_optflow", "phase", "hybrid"):
        gmc = GlobalMotionCompensator(method=method, downscale=2, flick_thresh_px=40.0)
        h1 = gmc.apply(f1_fg, bboxes=[[100, 100, 220, 240]])
        assert np.allclose(h1, np.eye(2, 3), atol=1e-5)
        assert gmc.last_shift_px == 0.0

        h2 = gmc.apply(f2_fg, bboxes=[[40, 120, 160, 260]])
        assert abs(float(h2[0, 2]) - 50.0) < 3.0
        assert abs(float(h2[1, 2]) - 12.0) < 3.0
        assert gmc.last_shift_px > 45.0
        assert gmc.avg_gmc_ms >= 0.0

        gmc.reset()
        assert gmc.last_shift_px == 0.0
        assert gmc.avg_gmc_ms == 0.0

    # Verify "none" returns identity
    gmc_none = GlobalMotionCompensator(method="none")
    assert np.allclose(gmc_none.apply(f1), np.eye(2, 3))
    assert np.allclose(gmc_none.apply(f2), np.eye(2, 3))

    # Verify "hybrid" falls back to "phase" when sparse_optflow yields < min_inliers
    gmc_hybrid = GlobalMotionCompensator(method="hybrid", downscale=2)
    gmc_hybrid.apply(f1)
    monkeypatch.setattr(
        gmc_hybrid,
        "_estimate_sparse_optflow",
        lambda *args, **kwargs: (None, 0),
    )
    h_fb = gmc_hybrid.apply(f2)
    assert abs(float(h_fb[0, 2]) - 50.0) < 2.0
    assert abs(float(h_fb[1, 2]) - 12.0) < 2.0

    # Verify warp_kalman_state transforms mean/vel/scale & inflates covariance on flicks
    kf = NSAKalmanFilter()
    mean0, cov0 = kf.initiate(np.array([200.0, 150.0, 0.5, 80.0]))
    mean0[4] = 10.0  # vx
    mean0[5] = 0.0   # vy
    mean0[7] = 2.0   # vh

    # 90-degree rotation + 1.5x scale + small shift (< flick_thresh)
    theta = np.pi / 2.0
    scale = 1.5
    h_small = np.array(
        [
            [scale * np.cos(theta), -scale * np.sin(theta), 10.0],
            [scale * np.sin(theta), scale * np.cos(theta), 5.0],
        ],
        dtype=np.float32,
    )
    m_rot, c_rot = warp_kalman_state(mean0, cov0, h_small, flick_thresh_px=40.0)
    assert np.allclose(c_rot, c_rot.T)
    assert m_rot[0] == pytest.approx(-150.0 * scale + 10.0, abs=1e-4)
    assert m_rot[1] == pytest.approx(200.0 * scale + 5.0, abs=1e-4)
    assert m_rot[3] == pytest.approx(80.0 * scale, abs=1e-4)
    assert m_rot[4] == pytest.approx(0.0, abs=1e-4)
    assert m_rot[5] == pytest.approx(10.0 * scale, abs=1e-4)
    assert m_rot[7] == pytest.approx(2.0 * scale, abs=1e-4)

    # Pure translation flick (>= flick_thresh) inflates pos/vel variances
    h_flick = np.array([[1.0, 0.0, 55.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    h_mild = np.array([[1.0, 0.0, 5.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    _, c_mild = warp_kalman_state(mean0, cov0, h_mild, flick_thresh_px=40.0)
    m_flick, c_flick = warp_kalman_state(mean0, cov0, h_flick, flick_thresh_px=40.0)
    assert m_flick[0] == pytest.approx(255.0)
    assert c_flick[0, 0] > c_mild[0, 0] + 20.0
    assert c_flick[4, 4] > c_mild[4, 4] + 1.0


def test_bytetrack_gmc_zero_id_switch_across_camera_pans_and_flick():
    import cv2

    bg = _make_textured_canvas(2000, 720, seed=99)
    view_w, view_h = 640, 480

    # Camera pans left 50 px/frame across frames 1..7, then flicks +65 px with blur at frame 8
    cam_xs = [900 - i * 50 for i in range(7)] + [900 - 6 * 50 - 65, 900 - 6 * 50 - 80]

    # World coordinates of a small car (width=46, height=64) stationary in world space
    world_car = (1020.0, 220.0, 46.0, 64.0)
    world_person = (1140.0, 260.0, 32.0, 72.0)

    def build_frame_and_dets(cam_x: int, blur: bool = False):
        crop = bg[120 : 120 + view_h, cam_x : cam_x + view_w].copy()
        if blur:
            crop = cv2.GaussianBlur(crop, (21, 1), 0)
        dets = []
        for idx, (wx, wy, bw, bh, cls_id, cls_name, color) in enumerate(
            [
                (*world_car, 2, "car", (40, 180, 250)),
                (*world_person, 0, "person", (220, 90, 60)),
            ]
        ):
            x1 = wx - cam_x
            y1 = wy - 120.0
            x2 = x1 + bw
            y2 = y1 + bh
            cv2.rectangle(crop, (int(x1), int(y1)), (int(x2), int(y2)), color, -1)
            dets.append(Detection([x1, y1, x2, y2], 0.92, cls_id, cls_name))
        return crop, dets

    # Without GMC: +50 px pan breaks narrow boxes -> ID switches / drops
    tracker_no_gmc = ByteTracker(
        ByteTrackerConfig(use_gmc=False, use_lazy_appearance=False)
    )
    seen_ids_no_gmc = set()
    for f_idx, cx in enumerate(cam_xs, start=1):
        frame, dets = build_frame_and_dets(cx, blur=(f_idx == 8))
        tracks = tracker_no_gmc.update(dets, frame_id=f_idx, frame=frame)
        seen_ids_no_gmc.update(t.track_id for t in tracks)
    assert len(seen_ids_no_gmc) > 2

    # With GMC hybrid (handles pans + blurred flick fallback): zero ID switches!
    tracker_gmc = ByteTracker(
        ByteTrackerConfig(
            use_gmc=True,
            gmc_method="hybrid",
            gmc_downscale=2,
            gmc_flick_thresh_px=40.0,
            use_lazy_appearance=False,
        )
    )
    seen_ids_gmc = set()
    shifts = []
    for f_idx, cx in enumerate(cam_xs, start=1):
        frame, dets = build_frame_and_dets(cx, blur=(f_idx == 8))
        tracks = tracker_gmc.update(dets, frame_id=f_idx, frame=frame)
        assert len(tracks) == 2
        seen_ids_gmc.update(t.track_id for t in tracks)
        shifts.append(tracker_gmc.last_gmc_shift_px)

    assert seen_ids_gmc == {1, 2}
    assert max(shifts[1:]) >= 45.0


def test_lazy_hsv_single_conversion_and_cache_speed(monkeypatch):
    import time
    import cv2

    cvt_calls = []
    real_cvt = cv2.cvtColor

    def counted_cvt(src, code, *args, **kwargs):
        if code == cv2.COLOR_BGR2HSV:
            cvt_calls.append(src.shape[:2])
        return real_cvt(src, code, *args, **kwargs)

    monkeypatch.setattr(cv2, "cvtColor", counted_cvt)

    # 1080p dense scene with 12 targets (8 separated + 2 overlapping pair at y=460)
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    dets = []
    for i in range(10):
        x1 = 80 + (i % 5) * 300
        y1 = 80 + (i // 5) * 320
        x2, y2 = x1 + 90, y1 + 180
        frame[y1:y2, x1:x2] = ((i * 37) % 255, (i * 83) % 255, 200)
        dets.append(Detection([x1, y1, x2, y2], 0.9, 0, "person"))
    # Pair of tightly overlapping targets at Frame 2 (dx=4px) triggering ambiguity
    for k, (x1, y1, col) in enumerate(
        [(200, 720, (0, 0, 255)), (204, 722, (255, 0, 0))]
    ):
        x2, y2 = x1 + 90, y1 + 180
        frame[y1:y2, x1:x2] = col
        dets.append(Detection([x1, y1, x2, y2], 0.9, 0, "person"))

    cfg = ByteTrackerConfig.full_enhanced()
    cfg.appearance_update_interval = 10
    tracker = ByteTracker(cfg)

    # Frame 1: initial template extraction across 12 targets -> AT MOST 1 downsampled BGR2HSV call!
    tracker.update(dets, frame_id=1, frame=frame)
    assert len(cvt_calls) == 1
    assert max(cvt_calls[0]) <= 640

    # Frame 2 (ambiguous overlapping pair triggers Stage-1 LazyHSV) -> AT MOST 1 downsampled BGR2HSV call!
    cvt_calls.clear()
    tracker.update(dets, frame_id=2, frame=frame)
    assert len(cvt_calls) == 1
    assert max(cvt_calls[0]) <= 640
    assert tracker.lazy_appearance_calls >= 1

    # Frame 3 with only separated targets & non-refresh frame -> ZERO BGR2HSV calls!
    cvt_calls.clear()
    tracker.update(dets[:10], frame_id=3, frame=frame)
    assert len(cvt_calls) == 0

    # Verify downsampled HSV cache matches full-res cosine similarity and is fast
    monkeypatch.undo()
    hsv_cache = prepare_downsampled_hsv(frame, max_dim=640)
    t0 = time.perf_counter()
    for _ in range(5):
        c_once = prepare_downsampled_hsv(frame, max_dim=640)
        feats_cached = [extract_hsv_histogram_from_hsv(c_once, d.bbox) for d in dets]
    cached_ms = (time.perf_counter() - t0) * 1000.0
    feats_direct = [extract_hsv_histogram(frame, d.bbox) for d in dets]
    for fc, fd in zip(feats_cached, feats_direct):
        assert fc is not None and fd is not None
        assert float(np.dot(fc, fd)) > 0.95
    assert cached_ms < 500.0
