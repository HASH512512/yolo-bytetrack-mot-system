"""Comprehensive backend tests covering SQLite WAL CRUD, REST endpoints, and WebSocket."""

from __future__ import annotations

import json
from time import monotonic, sleep
import pytest
from fastapi.testclient import TestClient

from backend.db import Database
from backend.schemas import (
    StreamConfig,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TrackItem,
    ViolationEvent,
    ViolationType,
    ZoneCreate,
    ZoneType,
)
from backend.server import app


# ----------------------------------------------------------------------
# 1. SQLite WAL & Database CRUD Unit Tests
# ----------------------------------------------------------------------


def test_sqlite_wal_and_tables(tmp_path):
    db_file = tmp_path / "test_wal.db"
    db = Database(db_file)

    # Check WAL mode
    with db.transaction() as cur:
        cur.execute("PRAGMA journal_mode;")
        row = cur.fetchone()
        assert row[0].lower() == "wal"

    # Task CRUD
    created_task = db.create_task(
        TaskCreate(
            name="Unit Test Task",
            video_source="test.mp4",
            config=StreamConfig(fps=30, use_nsa=True),
        )
    )
    assert created_task.id is not None
    assert created_task.name == "Unit Test Task"
    assert created_task.status == TaskStatus.IDLE

    fetched = db.get_task(created_task.id)
    assert fetched is not None
    assert fetched.name == "Unit Test Task"

    # Update Task
    updated = db.update_task(
        created_task.id,
        TaskUpdate(name="Renamed Task", status=TaskStatus.RUNNING),
    )
    assert updated is not None
    assert updated.name == "Renamed Task"
    assert updated.status == TaskStatus.RUNNING

    # Zones CRUD
    zone = db.create_zone(
        ZoneCreate(
            task_id=created_task.id,
            name="Speed Zone",
            zone_type=ZoneType.LANE,
            polygon=[[0, 0], [100, 0], [100, 50], [0, 50]],
            direction_vector=[1.0, 0.0],
            speed_limit=20.0,
        )
    )
    assert zone.id is not None
    assert zone.task_id == created_task.id

    zones_list = db.list_zones(task_id=created_task.id)
    assert len(zones_list) == 1
    assert zones_list[0].name == "Speed Zone"

    # Trajectories Batch Insert
    tracks = [
        TrackItem(
            track_id=1,
            bbox=[10.0, 20.0, 50.0, 80.0],
            class_name="vehicle",
            conf=0.95,
            speed=12.5,
            direction=[1.0, 0.0],
        ),
        TrackItem(
            track_id=2,
            bbox=[100.0, 120.0, 140.0, 180.0],
            class_name="pedestrian",
            conf=0.91,
            speed=1.4,
        ),
    ]
    inserted_count = db.insert_trajectories_batch(
        task_id=created_task.id, frame_id=1, timestamp=100.0, tracks=tracks
    )
    assert inserted_count == 2

    history = db.get_track_history(created_task.id, track_id=1)
    assert len(history) == 1
    assert history[0]["frame_id"] == 1

    # Violations CRUD
    v_event = ViolationEvent(
        task_id=created_task.id,
        track_id=1,
        violation_type=ViolationType.WRONG_WAY,
        timestamp=100.0,
        frame_id=1,
        bbox=[10.0, 20.0, 50.0, 80.0],
        zone_id=zone.id,
        acknowledged=False,
        details={"speed": 12.5},
    )
    saved_v = db.create_violation(v_event)
    assert saved_v.id is not None
    assert not saved_v.acknowledged

    violations = db.list_violations(task_id=created_task.id)
    assert len(violations) >= 1

    ack = db.acknowledge_violation(saved_v.id)
    assert ack is not None
    assert ack.acknowledged is True

    # Delete Zone & Task
    assert db.delete_zone(zone.id) is True
    assert db.delete_task(created_task.id) is True
    assert db.get_task(created_task.id) is None
    db.close()


def test_wrong_way_normalization_and_track_queries(tmp_path):
    # Backward compatibility: normalize "wrong-way" -> "wrong_way"
    evt = ViolationEvent.model_validate(
        {
            "task_id": 1,
            "track_id": 7,
            "violation_type": "wrong-way",
            "timestamp": 100.0,
            "frame_id": 10,
            "bbox": [10.0, 20.0, 50.0, 60.0],
        }
    )
    assert evt.violation_type == ViolationType.WRONG_WAY
    assert evt.violation_type.value == "wrong_way"

    db_file = tmp_path / "test_tracks.db"
    db = Database(db_file)

    # Verify composite index exists
    with db.transaction() as cur:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_trajectories_task_track_ts';"
        )
        assert cur.fetchone() is not None

    task = db.create_task(TaskCreate(name="Track Query Task", video_source="demo"))
    assert task.id is not None

    # Insert legacy "wrong-way" row directly to verify DB read normalization
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO violations (
                task_id, track_id, violation_type, timestamp, frame_id,
                bbox, zone_id, acknowledged, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task.id, 1, "wrong-way", 10.5, 2, "[0,0,10,10]", None, 0, "{}", "2026-01-01T00:00:00Z"),
        )

    v_list = db.list_violations(task_id=task.id, violation_type="wrong_way")
    assert len(v_list) == 1
    assert v_list[0].violation_type == ViolationType.WRONG_WAY
    assert len(db.list_violations(task_id=task.id, violation_type="wrong-way")) == 1

    # Insert trajectories across two frames and two tracks
    db.insert_trajectories_batch(
        task_id=task.id,
        frame_id=1,
        timestamp=10.0,
        tracks=[
            TrackItem(track_id=1, bbox=[10, 20, 50, 60], class_name="car", conf=0.9, speed=5.0),
            TrackItem(track_id=2, bbox=[100, 120, 130, 180], class_name="person", conf=0.85, speed=1.2),
        ],
    )
    db.insert_trajectories_batch(
        task_id=task.id,
        frame_id=2,
        timestamp=10.5,
        tracks=[
            TrackItem(track_id=1, bbox=[20, 20, 60, 60], class_name="car", conf=0.95, speed=7.0),
        ],
    )

    summaries = db.list_track_summaries(task_id=task.id)
    assert len(summaries) == 2
    by_tid = {s["track_id"]: s for s in summaries}
    assert by_tid[1]["class_name"] == "car"
    assert by_tid[1]["point_count"] == 2
    assert by_tid[1]["first_frame"] == 1
    assert by_tid[1]["last_frame"] == 2
    assert by_tid[1]["avg_speed"] == 6.0
    assert by_tid[1]["max_speed"] == 7.0
    assert by_tid[1]["violation_count"] == 1
    assert by_tid[2]["violation_count"] == 0

    car_only = db.list_track_summaries(task_id=task.id, class_name="car")
    assert len(car_only) == 1 and car_only[0]["track_id"] == 1

    pts = db.query_trajectories(task_id=task.id, track_id=1)
    assert len(pts) == 2
    assert pts[0]["frame_id"] == 1 and pts[1]["frame_id"] == 2
    assert pts[0]["cx"] == 30.0 and pts[0]["cy"] == 40.0
    assert pts[0]["w"] == 40.0 and pts[0]["h"] == 40.0

    # Ensure db.close() releases Windows file locks cleanly
    db.close()
    db_file.unlink()


# ----------------------------------------------------------------------
# 2. FastAPI REST Endpoints Integration Tests
# ----------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_violation_demo_reuses_task_and_detects_three_rules(tmp_path, monkeypatch):
    db = Database(tmp_path / "violation_demo.db")
    monkeypatch.setattr("backend.server.db", db)
    monkeypatch.setattr("backend.stream_engine.db", db)
    # Simulate a database from before the dedicated demo existed.
    legacy = db.create_task(TaskCreate(name="Existing demo", video_source="demo"))
    expected_types = {"wrong_way", "intrusion", "loitering"}

    with TestClient(app) as client:
        prepared = client.post("/api/tasks/violation-demo")
        assert prepared.status_code == 200
        demo = prepared.json()
        task_id = demo["id"]
        assert task_id != legacy.id
        assert demo["config"]["enable_roi_rules"]
        zones = client.get(f"/api/zones?task_id={task_id}").json()
        assert len(zones) == 4
        assert client.post("/api/tasks/violation-demo").json()["id"] == task_id
        assert client.get(f"/api/zones?task_id={task_id}").json() == zones
        assert db.get_task(legacy.id) == legacy
        assert db.list_zones(legacy.id) == []

        endpoint = f"/api/violations?task_id={task_id}"
        assert client.get(endpoint).json() == []
        assert client.post(f"/api/tasks/{task_id}/start").status_code == 200
        deadline = monotonic() + 10
        while monotonic() < deadline:
            alerts = client.get(endpoint).json()
            if {event["violation_type"] for event in alerts} == expected_types:
                break
            sleep(0.05)
        assert {event["violation_type"] for event in alerts} == expected_types
        assert all(event["frame_id"] <= 180 for event in alerts)
        assert all(event["zone_id"] in {zone["id"] for zone in zones} for event in alerts)
        assert {
            (event["violation_type"], event["details"]["cls_name"]) for event in alerts
        } == {("wrong_way", "motorcycle"), ("intrusion", "person"), ("loitering", "person")}

        cars = client.get(f"/api/tracks?task_id={task_id}&class_name=car").json()["summaries"]
        assert cars
        assert all(car["violation_count"] == 0 for car in cars)


def test_task_endpoints(client):
    # 1. Create Task
    task_payload = {
        "name": "Traffic Camera North Gate",
        "video_source": "demo",
        "config": {
            "fps": 25,
            "use_nsa": True,
            "use_diou": True,
            "use_recovery_pool": True,
        },
    }
    create_res = client.post("/api/tasks", json=task_payload)
    assert create_res.status_code == 201
    task = create_res.json()
    task_id = task["id"]
    assert task["name"] == "Traffic Camera North Gate"
    assert task["status"] == "idle"

    # 2. Get Task
    get_res = client.get(f"/api/tasks/{task_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == task_id

    # 3. List Tasks
    list_res = client.get("/api/tasks")
    assert list_res.status_code == 200
    tasks = list_res.json()
    assert any(t["id"] == task_id for t in tasks)

    # 4. Update Task
    update_res = client.put(f"/api/tasks/{task_id}", json={"name": "Updated North Gate"})
    assert update_res.status_code == 200
    assert update_res.json()["name"] == "Updated North Gate"

    # 5. Start and Stop Task
    start_res = client.post(f"/api/tasks/{task_id}/start")
    assert start_res.status_code == 200
    assert start_res.json()["status"] == "running"

    stop_res = client.post(f"/api/tasks/{task_id}/stop")
    assert stop_res.status_code == 200
    assert stop_res.json()["status"] == "stopped"

    # 6. Delete Task
    del_res = client.delete(f"/api/tasks/{task_id}")
    assert del_res.status_code == 200
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_zones_endpoints(client):
    # Create task first
    task_res = client.post("/api/tasks", json={"name": "Zone Test Task", "video_source": "demo"})
    task_id = task_res.json()["id"]

    # Create Zone
    zone_payload = {
        "task_id": task_id,
        "name": "Restricted Pedestrian Zone",
        "zone_type": "intrusion",
        "polygon": [[100, 100], [200, 100], [200, 200], [100, 200]],
        "dwell_limit_sec": 5.0,
    }
    create_res = client.post("/api/zones", json=zone_payload)
    assert create_res.status_code == 201
    zone = create_res.json()
    zone_id = zone["id"]
    assert zone["zone_type"] == "intrusion"

    # Get Zone
    get_res = client.get(f"/api/zones/{zone_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Restricted Pedestrian Zone"

    # List Zones
    list_res = client.get(f"/api/zones?task_id={task_id}")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1

    # Delete Zone
    del_res = client.delete(f"/api/zones/{zone_id}")
    assert del_res.status_code == 200
    assert client.get(f"/api/zones/{zone_id}").status_code == 404

    # Clean up task
    client.delete(f"/api/tasks/{task_id}")


def test_violations_endpoints(client):
    # Create task
    task_res = client.post("/api/tasks", json={"name": "Violation Test Task", "video_source": "demo"})
    task_id = task_res.json()["id"]

    # Trigger task start so simulator generates violations
    client.post(f"/api/tasks/{task_id}/start")

    # List Violations
    list_res = client.get(f"/api/violations?task_id={task_id}")
    assert list_res.status_code == 200
    assert isinstance(list_res.json(), list)

    client.post(f"/api/tasks/{task_id}/stop")
    client.delete(f"/api/tasks/{task_id}")


def test_metrics_and_ablation_endpoints(client):
    # Metrics
    m_res = client.get("/api/metrics")
    assert m_res.status_code == 200
    metrics = m_res.json()
    assert "fps" in metrics
    assert "p95_latency_ms" in metrics
    assert "stage_latency" in metrics
    assert "detect_skip_ratio" in metrics
    assert "is_demo_simulation" in metrics
    assert "active_streams" in metrics

    # Ablation
    ab_res = client.get("/api/ablation")
    assert ab_res.status_code == 200
    data = ab_res.json()
    assert len(data) >= 4
    methods = [d["method"] for d in data]
    assert any("Baseline" in m for m in methods)
    assert any("NSA-Kalman" in m for m in methods)
    assert any("DIoU" in m for m in methods)
    assert any("Recovery Pool" in m or "Full" in m for m in methods)
    assert all(d.get("data_kind") in ("synthetic", "public_benchmark", "self_collected") for d in data)


def test_local_assets_and_benchmarks_endpoints(client):
    assets_res = client.get("/api/local-assets")
    assert assets_res.status_code == 200
    assets = assets_res.json()
    assert isinstance(assets["videos"], list)
    assert isinstance(assets["datasets"], list)
    assert isinstance(assets["weights"], list)

    bench_res = client.get("/api/benchmarks")
    assert bench_res.status_code == 200
    bench = bench_res.json()
    assert "available" in bench
    assert "experiments" in bench
    assert isinstance(bench["experiments"], dict)


def test_tracks_endpoint_and_demo_bytetracker_execution(client):
    task_res = client.post(
        "/api/tasks",
        json={
            "name": "Demo ByteTracker Verification",
            "video_source": "demo",
            "config": {
                "video_source": "demo",
                "detector_type": "mock",
                "fps": 25,
                "downsample_fps": 10.0,
                "use_class_aware": True,
                "use_vdc": True,
                "use_oru": True,
                "use_lazy_appearance": True,
                "adaptive_skip": True,
                "max_skip_stride": 2,
            },
        },
    )
    assert task_res.status_code == 201
    task_id = task_res.json()["id"]

    # Add a lane zone to verify CampusRoadRulesEngine integration
    zone_res = client.post(
        "/api/zones",
        json={
            "task_id": task_id,
            "name": "Test Eastbound Lane",
            "zone_type": "lane",
            "polygon": [[0, 260], [1280, 260], [1280, 360], [0, 360]],
            "direction_vector": [1.0, 0.0],
        },
    )
    assert zone_res.status_code == 201

    try:
        with client.websocket_connect(f"/ws/stream/{task_id}") as ws:
            msg_str = ws.receive_text()
            frame = json.loads(msg_str)
            assert frame["task_id"] == task_id
            assert frame["is_demo_simulation"] is True
            assert frame["frame_width"] == 1280
            assert frame["frame_height"] == 720
            assert len(frame["tracks"]) >= 1
            assert frame["metrics"]["is_demo_simulation"] is True
            assert frame["metrics"]["active_streams"] >= 1

        # Verify aggregate metrics while stream active
        m_res = client.get("/api/metrics")
        assert m_res.status_code == 200
        m_json = m_res.json()
        assert m_json["active_streams"] >= 1
        assert m_json["is_demo_simulation"] is True

        # Query persisted tracks via /api/tracks
        tracks_res = client.get(f"/api/tracks?task_id={task_id}")
        assert tracks_res.status_code == 200
        tdata = tracks_res.json()
        assert tdata["task_id"] == task_id
        assert len(tdata["summaries"]) >= 1
        assert len(tdata["points"]) >= 1
        first_tid = tdata["summaries"][0]["track_id"]

        filtered_res = client.get(f"/api/tracks?task_id={task_id}&track_id={first_tid}")
        assert filtered_res.status_code == 200
        assert len(filtered_res.json()["points"]) >= 1
    finally:
        client.post(f"/api/tasks/{task_id}/stop")
        client.delete(f"/api/tasks/{task_id}")


def test_missing_local_source_sets_error_without_downloading(client, tmp_path):
    task_res = client.post(
        "/api/tasks",
        json={
            "name": "Missing Video Task",
            "video_source": "data/videos/does_not_exist_12345.mp4",
            "config": {
                "video_source": "data/videos/does_not_exist_12345.mp4",
                "detector_type": "yolo",
                "model_path": "data/weights/does_not_exist_12345.pt",
            },
        },
    )
    assert task_res.status_code == 201
    task_id = task_res.json()["id"]
    try:
        start_res = client.post(f"/api/tasks/{task_id}/start")
        assert start_res.status_code == 400
        task_info = client.get(f"/api/tasks/{task_id}").json()
        assert task_info["status"] == "error"
    finally:
        client.delete(f"/api/tasks/{task_id}")

    # Also verify existing local seq dir + missing ONNX weight fails cleanly
    seq_dir = tmp_path / "img1"
    seq_dir.mkdir(parents=True)
    onnx_task = client.post(
        "/api/tasks",
        json={
            "name": "Missing ONNX Weights Task",
            "video_source": str(seq_dir),
            "config": {
                "video_source": str(seq_dir),
                "detector_type": "onnx",
                "model_path": str(tmp_path / "missing.onnx"),
            },
        },
    )
    assert onnx_task.status_code == 201
    onnx_id = onnx_task.json()["id"]
    try:
        start_res = client.post(f"/api/tasks/{onnx_id}/start")
        assert start_res.status_code == 400
        assert client.get(f"/api/tasks/{onnx_id}").json()["status"] == "error"
    finally:
        client.delete(f"/api/tasks/{onnx_id}")


def test_real_ablation_lock_loading(tmp_path, monkeypatch, client):
    import backend.server as srv

    exp_dir = tmp_path / "benchmarks" / "experiments"
    exp_dir.mkdir(parents=True)
    lock_payload = {
        "exp_id": "mot17_ablation_test",
        "data_kind": "public_benchmark",
        "metrics": {
            "frames": 100,
            "detect_fps": 85.0,
            "variants": {
                "baseline": {
                    "name": "Baseline (Standard ByteTrack)",
                    "mota": 0.721,
                    "idf1": 0.684,
                    "id_switches": 12,
                    "recall": 0.78,
                    "precision": 0.91,
                    "track_fps": 420.5,
                    "avg_track_ms": 2.38,
                },
                "full": {
                    "name": "Full (Enhanced ByteTrack)",
                    "mota": 0.765,
                    "idf1": 0.742,
                    "id_switches": 4,
                    "recall": 0.81,
                    "precision": 0.93,
                    "track_fps": 380.2,
                    "avg_track_ms": 2.63,
                },
            },
        },
    }
    (exp_dir / "mot17_ablation_test.lock.json").write_text(
        json.dumps(lock_payload), encoding="utf-8"
    )
    monkeypatch.setattr(srv, "PROJECT_ROOT", tmp_path)

    res = client.get("/api/ablation")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    assert rows[0]["data_kind"] == "public_benchmark"
    assert rows[0]["exp_id"] == "mot17_ablation_test"
    assert rows[0]["mota"] == 72.1
    assert rows[0]["fps"] == 420.5
    assert rows[1]["method"] == "Full (Enhanced ByteTrack)"
    assert rows[1]["id_switches"] == 4


def test_video_feed_mjpeg_stream(client):
    assert client.get("/api/tasks/999999/video_feed").status_code == 404

    task_res = client.post("/api/tasks", json={"name": "MJPEG Feed Test", "video_source": "demo"})
    assert task_res.status_code == 201
    task_id = task_res.json()["id"]

    try:
        buf = b""
        with client.stream("GET", f"/api/tasks/{task_id}/video_feed") as resp:
            assert resp.status_code == 200
            assert "multipart/x-mixed-replace" in resp.headers.get("content-type", "")
            for chunk in resp.iter_bytes():
                buf += chunk
                if b"--frame" in buf and b"\xff\xd8" in buf and b"\xff\xd9" in buf:
                    break
        assert b"--frame" in buf
        assert b"\xff\xd8" in buf and b"\xff\xd9" in buf
    finally:
        client.post(f"/api/tasks/{task_id}/stop")
        client.delete(f"/api/tasks/{task_id}")


# ----------------------------------------------------------------------
# 3. WebSocket Real-time Stream Test
# ----------------------------------------------------------------------


def test_websocket_stream(client):
    # Create task
    task_res = client.post("/api/tasks", json={"name": "WS Test Task", "video_source": "demo"})
    task_id = task_res.json()["id"]

    try:
        with client.websocket_connect(f"/ws/stream/{task_id}") as ws:
            # Send heartbeat ping
            ws.send_text("ping")
            # Should receive pong or track frame
            msg_str = ws.receive_text()
            assert len(msg_str) > 0

            # Receive at least one valid TrackFrame JSON
            if msg_str == "pong":
                msg_str = ws.receive_text()

            frame = json.loads(msg_str)
            assert "frame_id" in frame
            assert "task_id" in frame
            assert "tracks" in frame
            assert "metrics" in frame
            assert frame["frame_width"] == 1280
            assert frame["frame_height"] == 720
            assert frame["is_demo_simulation"] is True
    finally:
        client.post(f"/api/tasks/{task_id}/stop")
        client.delete(f"/api/tasks/{task_id}")


def test_task_frame_replay_image_endpoint(client):
    assert client.get("/api/tasks/999999/frame/1").status_code == 404

    task_res = client.post(
        "/api/tasks",
        json={"name": "Replay Frame Test", "video_source": "demo"},
    )
    assert task_res.status_code == 201
    task_id = task_res.json()["id"]
    try:
        resp = client.get(f"/api/tasks/{task_id}/frame/1")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("image/jpeg")
        assert int(resp.headers.get("x-frame-width", "0")) == 1280
        assert int(resp.headers.get("x-frame-height", "0")) == 720
        assert resp.content.startswith(b"\xff\xd8")
        assert resp.content.endswith(b"\xff\xd9")
    finally:
        client.delete(f"/api/tasks/{task_id}")
