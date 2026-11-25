"""SQLite database layer with WAL mode for Campus MOT System."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Tuple

from backend.schemas import (
    StreamConfig,
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskUpdate,
    TrackItem,
    ViolationEvent,
    ViolationType,
    ZoneCreate,
    ZoneRead,
    ZoneType,
)

DEFAULT_DB_DIR = Path("D:/Code/GraduationProject/data")
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "mot_system.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite manager using WAL journal mode."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        if db_path is None:
            self.db_path = DEFAULT_DB_PATH
        else:
            self.db_path = Path(db_path) if db_path != ":memory:" else ":memory:"

        if self.db_path != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._conns_by_thread: Dict[int, Tuple[threading.Thread, sqlite3.Connection]] = {}
        self._epoch: int = 0
        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Returns or creates a connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        conn_epoch = getattr(self._local, "epoch", -1)
        if conn is None or conn_epoch != self._epoch:
            db_target = str(self.db_path)
            conn = sqlite3.connect(db_target, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")

            curr_thread = threading.current_thread()
            tid = curr_thread.ident or id(curr_thread)
            stale_conns: List[sqlite3.Connection] = []
            with self._conns_lock:
                for k in list(self._conns_by_thread.keys()):
                    th, old_conn = self._conns_by_thread[k]
                    if k == tid or not th.is_alive():
                        stale_conns.append(old_conn)
                        del self._conns_by_thread[k]
                self._conns_by_thread[tid] = (curr_thread, conn)
                current_epoch = self._epoch

            for old_conn in stale_conns:
                if old_conn is not conn:
                    try:
                        old_conn.close()
                    except Exception:
                        pass

            self._local.conn = conn
            self._local.epoch = current_epoch
        return conn

    def close(self) -> None:
        """Close all tracked thread-local SQLite connections (prevents Windows file locks)."""
        with self._conns_lock:
            self._epoch += 1
            conns = [c for _, c in self._conns_by_thread.values()]
            self._conns_by_thread.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None
        self._local.epoch = -1

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_schema(self) -> None:
        """Initializes tables and indices."""
        with self.transaction() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    video_source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    config TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    zone_type TEXT NOT NULL,
                    polygon TEXT NOT NULL,
                    direction_vector TEXT,
                    speed_limit REAL,
                    dwell_limit_sec REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    frame_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    w REAL NOT NULL,
                    h REAL NOT NULL,
                    class_name TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    speed REAL DEFAULT 0.0,
                    direction TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    violation_type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    frame_id INTEGER NOT NULL,
                    bbox TEXT NOT NULL,
                    zone_id INTEGER,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
            """)
            # Indices for rapid querying
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trajectories_track ON trajectories(task_id, track_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trajectories_frame ON trajectories(task_id, frame_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trajectories_task_track_ts ON trajectories(task_id, track_id, timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_task ON violations(task_id, acknowledged);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_zones_task ON zones(task_id);")

    # ==================== Task CRUD ====================

    def create_task(self, task: TaskCreate) -> TaskRead:
        with self.transaction() as cur:
            return self._insert_task(cur, task)

    def get_or_create_task_for_source(
        self, task: TaskCreate, create_zones: Callable[[int], Sequence[ZoneCreate]]
    ) -> TaskRead:
        """Provision a built-in source and its zones atomically."""
        with self.transaction() as cur:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT * FROM tasks WHERE video_source = ? ORDER BY id LIMIT 1",
                (task.video_source,),
            )
            row = cur.fetchone()
            if row:
                return self._row_to_task(row)
            created = self._insert_task(cur, task)
            for zone in create_zones(created.id):
                self._insert_zone(cur, zone)
            return created

    def _insert_task(self, cur: sqlite3.Cursor, task: TaskCreate) -> TaskRead:
        now = _now_iso()
        config_obj = task.config or StreamConfig(video_source=task.video_source)
        cur.execute(
            """
            INSERT INTO tasks (name, video_source, status, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task.name, task.video_source, TaskStatus.IDLE.value, config_obj.model_dump_json(), now, now),
        )

        return TaskRead(
            id=cur.lastrowid,
            name=task.name,
            video_source=task.video_source,
            status=TaskStatus.IDLE,
            config=config_obj,
            created_at=now,
            updated_at=now,
        )

    def get_task(self, task_id: int) -> Optional[TaskRead]:
        with self.transaction() as cur:
            cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_task(row)

    def list_tasks(self) -> List[TaskRead]:
        with self.transaction() as cur:
            cur.execute("SELECT * FROM tasks ORDER BY id DESC")
            rows = cur.fetchall()
            return [self._row_to_task(r) for r in rows]

    def update_task(self, task_id: int, update: TaskUpdate) -> Optional[TaskRead]:
        current = self.get_task(task_id)
        if not current:
            return None

        new_name = update.name if update.name is not None else current.name
        new_source = update.video_source if update.video_source is not None else current.video_source
        new_status = update.status if update.status is not None else current.status
        new_config = update.config if update.config is not None else current.config
        now = _now_iso()

        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET name = ?, video_source = ?, status = ?, config = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_name, new_source, new_status.value, new_config.model_dump_json(), now, task_id),
            )

        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cur.rowcount > 0

    def _row_to_task(self, row: sqlite3.Row) -> TaskRead:
        config_dict = json.loads(row["config"])
        return TaskRead(
            id=row["id"],
            name=row["name"],
            video_source=row["video_source"],
            status=TaskStatus(row["status"]),
            config=StreamConfig.model_validate(config_dict),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ==================== Zone CRUD ====================

    def create_zone(self, zone: ZoneCreate) -> ZoneRead:
        with self.transaction() as cur:
            return self._insert_zone(cur, zone)

    def _insert_zone(self, cur: sqlite3.Cursor, zone: ZoneCreate) -> ZoneRead:
        now = _now_iso()
        polygon_json = json.dumps(zone.polygon)
        dir_json = json.dumps(zone.direction_vector) if zone.direction_vector else None

        cur.execute(
            """
            INSERT INTO zones (task_id, name, zone_type, polygon, direction_vector, speed_limit, dwell_limit_sec, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone.task_id,
                zone.name,
                zone.zone_type.value,
                polygon_json,
                dir_json,
                zone.speed_limit,
                zone.dwell_limit_sec,
                now,
            ),
        )

        return ZoneRead(
            id=cur.lastrowid,
            task_id=zone.task_id,
            name=zone.name,
            zone_type=zone.zone_type,
            polygon=zone.polygon,
            direction_vector=zone.direction_vector,
            speed_limit=zone.speed_limit,
            dwell_limit_sec=zone.dwell_limit_sec,
            created_at=now,
        )

    def get_zone(self, zone_id: int) -> Optional[ZoneRead]:
        with self.transaction() as cur:
            cur.execute("SELECT * FROM zones WHERE id = ?", (zone_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_zone(row)

    def list_zones(self, task_id: Optional[int] = None) -> List[ZoneRead]:
        with self.transaction() as cur:
            if task_id is not None:
                cur.execute("SELECT * FROM zones WHERE task_id = ? ORDER BY id ASC", (task_id,))
            else:
                cur.execute("SELECT * FROM zones ORDER BY id ASC")
            rows = cur.fetchall()
            return [self._row_to_zone(r) for r in rows]

    def delete_zone(self, zone_id: int) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
            return cur.rowcount > 0

    def _row_to_zone(self, row: sqlite3.Row) -> ZoneRead:
        return ZoneRead(
            id=row["id"],
            task_id=row["task_id"],
            name=row["name"],
            zone_type=ZoneType(row["zone_type"]),
            polygon=json.loads(row["polygon"]),
            direction_vector=json.loads(row["direction_vector"]) if row["direction_vector"] else None,
            speed_limit=row["speed_limit"],
            dwell_limit_sec=row["dwell_limit_sec"],
            created_at=row["created_at"],
        )

    # ==================== Trajectory Batch Insertion ====================

    def insert_trajectories_batch(
        self,
        task_id: int,
        frame_id: int,
        timestamp: float,
        tracks: List[TrackItem],
    ) -> int:
        if not tracks:
            return 0
        now = _now_iso()
        records = []
        for t in tracks:
            bbox = t.bbox
            x, y = bbox[0], bbox[1]
            w, h = max(0.0, bbox[2] - bbox[0]), max(0.0, bbox[3] - bbox[1])
            dir_json = json.dumps(t.direction) if t.direction else None
            records.append(
                (
                    task_id,
                    t.track_id,
                    frame_id,
                    timestamp,
                    x,
                    y,
                    w,
                    h,
                    t.class_name,
                    t.conf,
                    t.speed,
                    dir_json,
                    now,
                )
            )

        with self.transaction() as cur:
            cur.executemany(
                """
                INSERT INTO trajectories (
                    task_id, track_id, frame_id, timestamp, x, y, w, h,
                    class_name, confidence, speed, direction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            return cur.rowcount

    def get_track_history(self, task_id: int, track_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self.transaction() as cur:
            cur.execute(
                """
                SELECT frame_id, timestamp, x, y, w, h, speed
                FROM trajectories
                WHERE task_id = ? AND track_id = ?
                ORDER BY frame_id DESC
                LIMIT ?
                """,
                (task_id, track_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "frame_id": r["frame_id"],
                    "timestamp": r["timestamp"],
                    "cx": r["x"] + 0.5 * r["w"],
                    "cy": r["y"] + 0.5 * r["h"],
                    "speed": r["speed"],
                }
                for r in reversed(rows)
            ]

    def list_track_summaries(
        self,
        task_id: int,
        class_name: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Aggregate per-track summary stats with joined violation counts."""
        query = """
            SELECT
                t.track_id AS track_id,
                MAX(t.class_name) AS class_name,
                MIN(t.frame_id) AS first_frame,
                MAX(t.frame_id) AS last_frame,
                MIN(t.timestamp) AS start_ts,
                MAX(t.timestamp) AS end_ts,
                COUNT(*) AS point_count,
                AVG(COALESCE(t.speed, 0.0)) AS avg_speed,
                MAX(COALESCE(t.speed, 0.0)) AS max_speed,
                COALESCE(v.violation_count, 0) AS violation_count
            FROM trajectories t
            LEFT JOIN (
                SELECT track_id, COUNT(*) AS violation_count
                FROM violations
                WHERE task_id = ?
                GROUP BY track_id
            ) v ON v.track_id = t.track_id
            WHERE t.task_id = ?
        """
        params: List[Any] = [task_id, task_id]
        if class_name and class_name != "all":
            query += " AND t.class_name = ?"
            params.append(class_name)
        if start_ts is not None:
            query += " AND t.timestamp >= ?"
            params.append(float(start_ts))
        if end_ts is not None:
            query += " AND t.timestamp <= ?"
            params.append(float(end_ts))

        query += " GROUP BY t.track_id ORDER BY first_frame ASC, t.track_id ASC LIMIT ?"
        params.append(int(limit))

        with self.transaction() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                {
                    "track_id": int(r["track_id"]),
                    "class_name": str(r["class_name"]),
                    "first_frame": int(r["first_frame"]),
                    "last_frame": int(r["last_frame"]),
                    "start_ts": float(r["start_ts"]),
                    "end_ts": float(r["end_ts"]),
                    "point_count": int(r["point_count"]),
                    "avg_speed": round(float(r["avg_speed"] or 0.0), 3),
                    "max_speed": round(float(r["max_speed"] or 0.0), 3),
                    "violation_count": int(r["violation_count"] or 0),
                }
                for r in rows
            ]

    def query_trajectories(
        self,
        task_id: int,
        track_id: Optional[int] = None,
        class_name: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        limit: int = 2000,
    ) -> List[Dict[str, Any]]:
        """Query trajectory points using composite task/track/timestamp index."""
        query = """
            SELECT track_id, frame_id, timestamp, x, y, w, h, speed, class_name, confidence
            FROM trajectories
            WHERE task_id = ?
        """
        params: List[Any] = [task_id]
        if track_id is not None:
            query += " AND track_id = ?"
            params.append(int(track_id))
        if class_name and class_name != "all":
            query += " AND class_name = ?"
            params.append(class_name)
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(float(start_ts))
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(float(end_ts))

        query += " ORDER BY frame_id ASC, timestamp ASC, id ASC LIMIT ?"
        params.append(int(limit))

        with self.transaction() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                {
                    "track_id": int(r["track_id"]),
                    "frame_id": int(r["frame_id"]),
                    "timestamp": float(r["timestamp"]),
                    "cx": round(float(r["x"] + 0.5 * r["w"]), 3),
                    "cy": round(float(r["y"] + 0.5 * r["h"]), 3),
                    "w": round(float(r["w"]), 3),
                    "h": round(float(r["h"]), 3),
                    "speed": round(float(r["speed"] or 0.0), 3),
                    "class_name": str(r["class_name"]),
                    "confidence": round(float(r["confidence"]), 4),
                }
                for r in rows
            ]

    # ==================== Violations CRUD ====================

    def create_violation(self, violation: ViolationEvent) -> ViolationEvent:
        now = _now_iso()
        bbox_json = json.dumps(violation.bbox)
        details_json = json.dumps(violation.details)

        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO violations (
                    task_id, track_id, violation_type, timestamp, frame_id,
                    bbox, zone_id, acknowledged, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    violation.task_id,
                    violation.track_id,
                    violation.violation_type.value,
                    violation.timestamp,
                    violation.frame_id,
                    bbox_json,
                    violation.zone_id,
                    1 if violation.acknowledged else 0,
                    details_json,
                    now,
                ),
            )
            v_id = cur.lastrowid

        violation.id = v_id
        return violation

    def list_violations(
        self,
        task_id: Optional[int] = None,
        violation_type: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100,
    ) -> List[ViolationEvent]:
        query = "SELECT * FROM violations WHERE 1=1"
        params: List[Any] = []

        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        if violation_type is not None:
            if violation_type in ("wrong_way", "wrong-way"):
                query += " AND violation_type IN ('wrong_way', 'wrong-way')"
            else:
                query += " AND violation_type = ?"
                params.append(violation_type)
        if acknowledged is not None:
            query += " AND acknowledged = ?"
            params.append(1 if acknowledged else 0)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.transaction() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            return [self._row_to_violation(r) for r in rows]

    def acknowledge_violation(self, violation_id: int) -> Optional[ViolationEvent]:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE violations SET acknowledged = 1 WHERE id = ?",
                (violation_id,),
            )
            if cur.rowcount == 0:
                return None
            cur.execute("SELECT * FROM violations WHERE id = ?", (violation_id,))
            row = cur.fetchone()
            return self._row_to_violation(row) if row else None

    def delete_violation(self, violation_id: int) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM violations WHERE id = ?", (violation_id,))
            return cur.rowcount > 0

    def _row_to_violation(self, row: sqlite3.Row) -> ViolationEvent:
        raw_type = row["violation_type"]
        if raw_type == "wrong-way":
            raw_type = "wrong_way"
        return ViolationEvent(
            id=row["id"],
            task_id=row["task_id"],
            track_id=row["track_id"],
            violation_type=ViolationType(raw_type),
            timestamp=row["timestamp"],
            frame_id=row["frame_id"],
            bbox=json.loads(row["bbox"]),
            zone_id=row["zone_id"],
            acknowledged=bool(row["acknowledged"]),
            details=json.loads(row["details"]),
        )


# Global singleton instance for application use
db = Database()
