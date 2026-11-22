"""Homography pixel-to-meter mapping and campus road rules engine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple
import numpy as np

from core.trajectory_pool import STrack


def point_in_polygon(
    point: Tuple[float, float], polygon: Sequence[Tuple[float, float]]
) -> bool:
    """Ray-casting point-in-polygon test.

    Args:
        point: (x, y) coordinate.
        polygon: Sequence of (x, y) vertices defining a closed polygon.
    """
    n = len(polygon)
    if n < 3:
        return False

    x, y = float(point[0]), float(point[1])
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) if (yj - yi) != 0.0 else 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


class HomographyMapper:
    """Maps 2D image pixel coordinates to ground-plane metric coordinates (meters)."""

    def __init__(
        self,
        matrix: Optional[np.ndarray] = None,
        meters_per_pixel: float = 0.05,
    ) -> None:
        if matrix is not None:
            mat = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
            self.matrix = mat
        else:
            # Default isotropic scale fallback when no camera calibration is provided
            s = float(meters_per_pixel)
            self.matrix = np.array(
                [[s, 0.0, 0.0], [0.0, s, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64
            )

    @classmethod
    def from_point_pairs(
        cls,
        pixel_points: Sequence[Tuple[float, float]],
        world_points: Sequence[Tuple[float, float]],
    ) -> "HomographyMapper":
        """Fit a 3x3 homography matrix from >= 4 (pixel, world_meter) correspondences."""
        if len(pixel_points) < 4 or len(pixel_points) != len(world_points):
            raise ValueError("Need at least 4 matching (pixel, world) point pairs.")

        src = np.asarray(pixel_points, dtype=np.float64)
        dst = np.asarray(world_points, dtype=np.float64)

        # Pure NumPy DLT (Direct Linear Transform) for homography
        rows = []
        for (x, y), (u, v) in zip(src, dst):
            rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
            rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
        a_mat = np.asarray(rows, dtype=np.float64)
        _, _, vt = np.linalg.svd(a_mat)
        h = vt[-1].reshape(3, 3)
        if abs(h[2, 2]) > 1e-12:
            h = h / h[2, 2]
        return cls(matrix=h)

    def pixel_to_meter(self, points: np.ndarray | Sequence[Tuple[float, float]]) -> np.ndarray:
        """Transform (2,) or (N, 2) pixel coordinates into metric (x, y) coordinates."""
        pts = np.asarray(points, dtype=np.float64)
        single = pts.ndim == 1
        if single:
            pts = pts.reshape(1, 2)
        if len(pts) == 0:
            return np.empty((0, 2), dtype=np.float64)

        ones = np.ones((len(pts), 1), dtype=np.float64)
        homog = np.hstack([pts, ones])  # (N, 3)
        proj = homog @ self.matrix.T
        w = np.where(np.abs(proj[:, 2:3]) > 1e-12, proj[:, 2:3], 1e-12)
        world = proj[:, :2] / w
        return world[0] if single else world


@dataclass
class LaneConfig:
    """Directed lane polygon for wrong-way (逆行) detection."""

    lane_id: str
    name: str
    polygon: List[Tuple[float, float]]
    direction: Tuple[float, float]
    cos_threshold: float = -0.3
    min_displacement_px: float = 15.0
    allowed_classes: Optional[List[str]] = None


@dataclass
class GeoFenceZone:
    """Restricted polygon zone for intrusion (越界) detection."""

    zone_id: str
    name: str
    polygon: List[Tuple[float, float]]
    allowed_classes: Optional[List[str]] = None


@dataclass
class LoiteringConfig:
    """Speed & dwell-time thresholds for loitering/congestion (滞留) detection."""

    zone_id: str = "global_loitering"
    name: str = "Loitering Zone"
    polygon: Optional[List[Tuple[float, float]]] = None
    max_speed_mps: float = 0.4
    min_dwell_seconds: float = 5.0
    target_classes: Optional[List[str]] = None


@dataclass
class Violation:
    """Structured traffic rule violation event."""

    violation_id: str
    rule_type: str  # "wrong_way" | "intrusion" | "loitering"
    track_id: int
    cls_name: str
    zone_or_lane_id: str
    timestamp: float
    frame_id: int
    bbox: List[float]
    world_pos: Tuple[float, float]
    details: Dict[str, Any] = field(default_factory=dict)


class CampusRoadRulesEngine:
    """Evaluates active trajectories against campus road traffic rules."""

    def __init__(
        self,
        homography: Optional[HomographyMapper] = None,
        lanes: Optional[Sequence[LaneConfig]] = None,
        zones: Optional[Sequence[GeoFenceZone]] = None,
        loitering_configs: Optional[Sequence[LoiteringConfig]] = None,
        cooldown_seconds: float = 5.0,
    ) -> None:
        self.homography = homography or HomographyMapper()
        self.lanes: List[LaneConfig] = list(lanes or [])
        self.zones: List[GeoFenceZone] = list(zones or [])
        self.loitering_configs: List[LoiteringConfig] = list(loitering_configs or [])
        self.cooldown_seconds = float(cooldown_seconds)

        # Per-track state for speed/dwell-time & alert deduplication
        self._track_samples: Dict[int, Deque[Tuple[float, float, float]]] = {}
        self._dwell_start: Dict[Tuple[int, str], float] = {}
        self._last_alert: Dict[Tuple[int, str, str], float] = {}
        self._violation_seq = 0

    def reset(self) -> None:
        self._track_samples.clear()
        self._dwell_start.clear()
        self._last_alert.clear()
        self._violation_seq = 0

    def get_roi_polygons(
        self, include_lanes: bool = True
    ) -> List[List[Tuple[float, float]]]:
        """Return configured ROI polygons (optionally excluding broad lane zones)."""
        polygons: List[List[Tuple[float, float]]] = []
        if include_lanes:
            for lane in self.lanes:
                if lane.polygon:
                    polygons.append([(float(x), float(y)) for x, y in lane.polygon])
        for zone in self.zones:
            if zone.polygon:
                polygons.append([(float(x), float(y)) for x, y in zone.polygon])
        for lcfg in self.loitering_configs:
            if lcfg.polygon:
                polygons.append([(float(x), float(y)) for x, y in lcfg.polygon])
        return polygons

    @classmethod
    def from_zone_dicts(
        cls,
        zones: Sequence[Dict[str, Any]],
        homography: Optional[HomographyMapper] = None,
        cooldown_seconds: float = 5.0,
    ) -> "CampusRoadRulesEngine":
        """Build a CampusRoadRulesEngine from DB/API zone record dictionaries."""
        lanes: List[LaneConfig] = []
        geofences: List[GeoFenceZone] = []
        loitering_configs: List[LoiteringConfig] = []

        for idx, raw_zone in enumerate(zones, start=1):
            z = raw_zone.model_dump() if hasattr(raw_zone, "model_dump") else dict(raw_zone)
            raw_type = z.get("zone_type", "")
            ztype = str(getattr(raw_type, "value", raw_type)).strip().lower()

            raw_id = z.get("id")
            if raw_id is None:
                raw_id = z.get("zone_id", z.get("lane_id", idx))
            zone_id = str(raw_id)
            name = str(z.get("name") or f"{ztype}_{zone_id}")

            raw_poly = z.get("polygon")
            if isinstance(raw_poly, str):
                raw_poly = json.loads(raw_poly)
            polygon: List[Tuple[float, float]] = (
                [(float(pt[0]), float(pt[1])) for pt in raw_poly]
                if raw_poly
                else []
            )

            if ztype == "lane":
                raw_dir = z.get("direction_vector")
                if raw_dir is None:
                    raw_dir = z.get("direction")
                if isinstance(raw_dir, str):
                    raw_dir = json.loads(raw_dir)
                direction = (
                    (float(raw_dir[0]), float(raw_dir[1]))
                    if raw_dir and len(raw_dir) >= 2
                    else (1.0, 0.0)
                )
                cos_thresh = (
                    float(z["cos_threshold"])
                    if z.get("cos_threshold") is not None
                    else -0.3
                )
                min_disp = (
                    float(z["min_displacement_px"])
                    if z.get("min_displacement_px") is not None
                    else 15.0
                )
                allowed = (
                    list(z["allowed_classes"])
                    if z.get("allowed_classes") is not None
                    else None
                )
                lanes.append(
                    LaneConfig(
                        lane_id=zone_id,
                        name=name,
                        polygon=polygon,
                        direction=direction,
                        cos_threshold=cos_thresh,
                        min_displacement_px=min_disp,
                        allowed_classes=allowed,
                    )
                )
            elif ztype == "intrusion":
                allowed = (
                    list(z["allowed_classes"])
                    if z.get("allowed_classes") is not None
                    else None
                )
                geofences.append(
                    GeoFenceZone(
                        zone_id=zone_id,
                        name=name,
                        polygon=polygon,
                        allowed_classes=allowed,
                    )
                )
            elif ztype == "loitering":
                speed_limit = (
                    float(z["speed_limit"])
                    if z.get("speed_limit") is not None
                    else float(z.get("max_speed_mps", 0.4))
                )
                dwell_limit = (
                    float(z["dwell_limit_sec"])
                    if z.get("dwell_limit_sec") is not None
                    else float(z.get("min_dwell_seconds", 5.0))
                )
                tc = z.get("target_classes", z.get("allowed_classes"))
                target_classes = list(tc) if tc is not None else None
                loitering_configs.append(
                    LoiteringConfig(
                        zone_id=zone_id,
                        name=name,
                        polygon=polygon if polygon else None,
                        max_speed_mps=speed_limit,
                        min_dwell_seconds=dwell_limit,
                        target_classes=target_classes,
                    )
                )
            else:
                raise ValueError(f"Unsupported zone_type: {ztype!r}")

        return cls(
            homography=homography,
            lanes=lanes,
            zones=geofences,
            loitering_configs=loitering_configs,
            cooldown_seconds=cooldown_seconds,
        )

    def _should_emit(
        self, track_id: int, rule_type: str, target_id: str, timestamp: float
    ) -> bool:
        key = (track_id, rule_type, target_id)
        last_ts = self._last_alert.get(key)
        if last_ts is not None and (timestamp - last_ts) < self.cooldown_seconds:
            return False
        self._last_alert[key] = timestamp
        return True

    def _next_violation_id(self) -> str:
        self._violation_seq += 1
        return f"vio_{self._violation_seq:06d}"

    def evaluate(
        self,
        tracks: Sequence[STrack],
        frame_id: int,
        timestamp: Optional[float] = None,
    ) -> List[Violation]:
        """Evaluate all active tracks for wrong-way, intrusion, and loitering."""
        ts = float(timestamp if timestamp is not None else frame_id / 30.0)
        violations: List[Violation] = []

        for track in tracks:
            tid = int(track.track_id)
            anchor_px = track.bottom_center
            world_xy = self.homography.pixel_to_meter(anchor_px)
            world_pos = (float(world_xy[0]), float(world_xy[1]))
            bbox = [float(v) for v in track.xyxy]

            samples = self._track_samples.setdefault(tid, deque(maxlen=30))
            samples.append((ts, anchor_px[0], anchor_px[1]))

            # (a) Wrong-way (逆行)
            if len(samples) >= 3:
                t0, x0, y0 = samples[0]
                dx = anchor_px[0] - x0
                dy = anchor_px[1] - y0
                disp = float(np.hypot(dx, dy))
                for lane in self.lanes:
                    if (
                        lane.allowed_classes is not None
                        and track.cls_name not in lane.allowed_classes
                    ):
                        continue
                    if disp < lane.min_displacement_px:
                        continue
                    if not point_in_polygon(anchor_px, lane.polygon):
                        continue
                    ldx, ldy = lane.direction
                    lnorm = float(np.hypot(ldx, ldy))
                    if lnorm <= 1e-9:
                        continue
                    cos_sim = (dx * ldx + dy * ldy) / (disp * lnorm)
                    if cos_sim < lane.cos_threshold and self._should_emit(
                        tid, "wrong_way", lane.lane_id, ts
                    ):
                        violations.append(
                            Violation(
                                violation_id=self._next_violation_id(),
                                rule_type="wrong_way",
                                track_id=tid,
                                cls_name=track.cls_name,
                                zone_or_lane_id=lane.lane_id,
                                timestamp=ts,
                                frame_id=frame_id,
                                bbox=bbox,
                                world_pos=world_pos,
                                details={
                                    "cosine_similarity": round(float(cos_sim), 4),
                                    "displacement_px": round(disp, 2),
                                },
                            )
                        )

            # (b) Intrusion / Geo-fence (越界)
            for zone in self.zones:
                if (
                    zone.allowed_classes is not None
                    and track.cls_name in zone.allowed_classes
                ):
                    continue
                if point_in_polygon(anchor_px, zone.polygon) and self._should_emit(
                    tid, "intrusion", zone.zone_id, ts
                ):
                    violations.append(
                        Violation(
                            violation_id=self._next_violation_id(),
                            rule_type="intrusion",
                            track_id=tid,
                            cls_name=track.cls_name,
                            zone_or_lane_id=zone.zone_id,
                            timestamp=ts,
                            frame_id=frame_id,
                            bbox=bbox,
                            world_pos=world_pos,
                            details={"zone_name": zone.name},
                        )
                    )

            # (c) Loitering / Congestion (滞留)
            if len(samples) >= 2:
                prev_ts, prev_x, prev_y = samples[-2]
                dt = max(ts - prev_ts, 1e-4)
                prev_world = self.homography.pixel_to_meter((prev_x, prev_y))
                dist_m = float(
                    np.hypot(world_pos[0] - prev_world[0], world_pos[1] - prev_world[1])
                )
                speed_mps = dist_m / dt

                for lcfg in self.loitering_configs:
                    if (
                        lcfg.target_classes is not None
                        and track.cls_name not in lcfg.target_classes
                    ):
                        continue
                    in_zone = (
                        True
                        if lcfg.polygon is None
                        else point_in_polygon(anchor_px, lcfg.polygon)
                    )
                    dwell_key = (tid, lcfg.zone_id)
                    if in_zone and speed_mps <= lcfg.max_speed_mps:
                        start_ts = self._dwell_start.setdefault(dwell_key, ts)
                        dwell_sec = ts - start_ts
                        if dwell_sec >= lcfg.min_dwell_seconds and self._should_emit(
                            tid, "loitering", lcfg.zone_id, ts
                        ):
                            violations.append(
                                Violation(
                                    violation_id=self._next_violation_id(),
                                    rule_type="loitering",
                                    track_id=tid,
                                    cls_name=track.cls_name,
                                    zone_or_lane_id=lcfg.zone_id,
                                    timestamp=ts,
                                    frame_id=frame_id,
                                    bbox=bbox,
                                    world_pos=world_pos,
                                    details={
                                        "speed_mps": round(speed_mps, 3),
                                        "dwell_seconds": round(dwell_sec, 2),
                                    },
                                )
                            )
                    else:
                        self._dwell_start.pop(dwell_key, None)

        return violations
