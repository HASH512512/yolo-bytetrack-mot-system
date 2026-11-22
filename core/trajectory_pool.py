"""Tracklet (STrack) state machine and short-term occlusion recovery pool."""

from __future__ import annotations

from collections import deque
from enum import IntEnum
from typing import Deque, List, Optional, Tuple
import numpy as np

from core.detector_adapter import Detection
from core.kalman_filter import KalmanFilter
from core.matching import iou_distance


class TrackState(IntEnum):
    """Lifecycle states of a single trajectory."""

    NEW = 0
    TRACKED = 1
    LOST = 2
    REMOVED = 3


class STrack:
    """Single-object trajectory state with Kalman state and recent history."""

    def __init__(
        self,
        detection: Detection,
        history_len: int = 64,
    ) -> None:
        self._tlwh = detection.tlwh.astype(np.float64)
        self.score: float = float(detection.conf)
        self.cls_id: int = int(detection.cls_id)
        self.cls_name: str = str(detection.cls_name)

        self.track_id: int = 0
        self.state: TrackState = TrackState.NEW
        self.is_activated: bool = False

        self.frame_id: int = 0
        self.start_frame: int = 0
        self.tracklet_len: int = 0

        self.kalman_filter: Optional[KalmanFilter] = None
        self.mean: Optional[np.ndarray] = None
        self.covariance: Optional[np.ndarray] = None

        self.history: Deque[Tuple[int, float, float]] = deque(maxlen=history_len)
        self.last_observation: Optional[np.ndarray] = None
        self.last_obs_frame: int = 0
        self.obs_history: Deque[Tuple[int, float, float]] = deque(maxlen=8)
        self.appearance_feat: Optional[np.ndarray] = None
        self._last_obs_mean: Optional[np.ndarray] = None
        self._last_obs_cov: Optional[np.ndarray] = None

    @property
    def end_frame(self) -> int:
        return self.frame_id

    @property
    def tlwh(self) -> np.ndarray:
        """Current bounding box in [x1, y1, w, h] format."""
        if self.mean is None:
            return self._tlwh.copy()
        cx, cy, a, h = self.mean[:4]
        h = max(float(h), 1.0)
        w = max(float(a * h), 1.0)
        return np.array([cx - 0.5 * w, cy - 0.5 * h, w, h], dtype=np.float64)

    @property
    def xyxy(self) -> np.ndarray:
        """Current bounding box in [x1, y1, x2, y2] format."""
        x1, y1, w, h = self.tlwh
        return np.array([x1, y1, x1 + w, y1 + h], dtype=np.float64)

    @property
    def center(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return (0.5 * (x1 + x2), 0.5 * (y1 + y2))

    @property
    def bottom_center(self) -> Tuple[float, float]:
        x1, _, x2, y2 = self.xyxy
        return (0.5 * (x1 + x2), float(y2))

    @staticmethod
    def tlwh_to_xyah(tlwh: np.ndarray) -> np.ndarray:
        x1, y1, w, h = tlwh
        cx = x1 + 0.5 * w
        cy = y1 + 0.5 * h
        aspect = w / max(h, 1e-6)
        return np.array([cx, cy, aspect, h], dtype=np.float64)

    def _record_history(self, frame_id: int) -> None:
        cx, cy = self.center
        self.history.append((int(frame_id), float(cx), float(cy)))

    def _record_observation(self, z_xyah: np.ndarray, frame_id: int) -> None:
        self.last_observation = np.asarray(z_xyah, dtype=np.float64).reshape(4).copy()
        self.last_obs_frame = int(frame_id)
        self.obs_history.append(
            (int(frame_id), float(self.last_observation[0]), float(self.last_observation[1]))
        )
        if self.mean is not None and self.covariance is not None:
            self._last_obs_mean = self.mean.copy()
            self._last_obs_cov = self.covariance.copy()

    @property
    def velocity_vector(self) -> Tuple[float, float]:
        """Unit (vx, vy) motion direction computed from recent obs_history."""
        if len(self.obs_history) < 2:
            return (0.0, 0.0)
        _, x0, y0 = self.obs_history[0]
        _, x1, y1 = self.obs_history[-1]
        dx = float(x1 - x0)
        dy = float(y1 - y0)
        norm = float(np.hypot(dx, dy))
        if norm < 1e-6:
            return (0.0, 0.0)
        return (dx / norm, dy / norm)

    def update_appearance(
        self, feat: Optional[np.ndarray], alpha: float = 0.85
    ) -> None:
        """Update EMA appearance template with L2 renormalization."""
        if feat is None:
            return
        vec = np.asarray(feat, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm <= 1e-8:
            return
        vec = (vec / norm).astype(np.float32)
        if self.appearance_feat is None or self.appearance_feat.shape != vec.shape:
            self.appearance_feat = vec.copy()
            return
        a = float(np.clip(alpha, 0.0, 1.0))
        blended = a * self.appearance_feat + (1.0 - a) * vec
        b_norm = float(np.linalg.norm(blended))
        if b_norm > 1e-8:
            self.appearance_feat = (blended / b_norm).astype(np.float32)

    def activate(
        self,
        kalman_filter: KalmanFilter,
        frame_id: int,
        track_id: int,
    ) -> None:
        """Initialize a new active track."""
        self.kalman_filter = kalman_filter
        self.track_id = int(track_id)
        z_curr = self.tlwh_to_xyah(self._tlwh)
        self.mean, self.covariance = self.kalman_filter.initiate(z_curr)
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        self.is_activated = frame_id == 1 or self.is_activated
        # In ByteTrack, tracks created on frame > 1 start unconfirmed unless activated.
        # However, if frame_id == 1 they are immediately activated.
        if frame_id == 1:
            self.is_activated = True
        self.frame_id = int(frame_id)
        self.start_frame = int(frame_id)
        self._record_observation(z_curr, frame_id)
        self._record_history(frame_id)

    def re_activate(
        self,
        new_track: "STrack",
        frame_id: int,
        new_id: Optional[int] = None,
        use_oru: bool = False,
    ) -> None:
        """Recover a LOST track with a newly matched detection."""
        assert self.kalman_filter is not None
        assert self.mean is not None and self.covariance is not None
        z_curr = self.tlwh_to_xyah(new_track._tlwh)
        gap = int(frame_id) - int(self.last_obs_frame)

        if use_oru and self.last_observation is not None and 2 <= gap <= 60:
            z_prev = self.last_observation
            if self._last_obs_mean is not None and self._last_obs_cov is not None:
                mean = self._last_obs_mean.copy()
                cov = self._last_obs_cov.copy()
            else:
                mean = self.mean.copy()
                cov = self.covariance.copy()

            for k in range(1, gap):
                alpha = k / float(gap)
                z_t = (1.0 - alpha) * z_prev + alpha * z_curr
                mean, cov = self.kalman_filter.predict(mean, cov)
                mean, cov = self.kalman_filter.update(
                    mean, cov, z_t, confidence=0.5
                )

            mean, cov = self.kalman_filter.predict(mean, cov)
            self.mean, self.covariance = self.kalman_filter.update(
                mean, cov, z_curr, confidence=new_track.score
            )
        else:
            self.mean, self.covariance = self.kalman_filter.update(
                self.mean,
                self.covariance,
                z_curr,
                confidence=new_track.score,
            )

        self._tlwh = new_track._tlwh.copy()
        self.tracklet_len = 0
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.frame_id = int(frame_id)
        if new_id is not None:
            self.track_id = int(new_id)
        self.score = float(new_track.score)
        self.cls_id = int(new_track.cls_id)
        self.cls_name = str(new_track.cls_name)
        self._record_observation(z_curr, frame_id)
        self._record_history(frame_id)

    def update(self, new_track: "STrack", frame_id: int) -> None:
        """Update an active TRACKED trajectory with a matched detection."""
        assert self.kalman_filter is not None
        assert self.mean is not None and self.covariance is not None
        self.frame_id = int(frame_id)
        self.tracklet_len += 1
        z_curr = self.tlwh_to_xyah(new_track._tlwh)
        self._tlwh = new_track._tlwh.copy()
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean,
            self.covariance,
            z_curr,
            confidence=new_track.score,
        )
        self.state = TrackState.TRACKED
        self.is_activated = True
        self.score = float(new_track.score)
        self.cls_id = int(new_track.cls_id)
        self.cls_name = str(new_track.cls_name)
        self._record_observation(z_curr, frame_id)
        self._record_history(frame_id)

    def predict(self) -> None:
        """Advance Kalman state by one frame."""
        if self.kalman_filter is None or self.mean is None or self.covariance is None:
            return
        mean_state = self.mean.copy()
        if self.state != TrackState.TRACKED:
            # Zero out height velocity during occlusion to avoid box-size drift
            mean_state[7] = 0.0
        self.mean, self.covariance = self.kalman_filter.predict(
            mean_state, self.covariance
        )

    def mark_lost(self) -> None:
        self.state = TrackState.LOST

    def mark_removed(self) -> None:
        self.state = TrackState.REMOVED


class TrajectoryPool:
    """Manages active, lost (occlusion recovery), and removed trajectories."""

    def __init__(self, max_time_lost: int = 30) -> None:
        self.max_time_lost = int(max_time_lost)
        self.tracked_stracks: List[STrack] = []
        self.lost_stracks: List[STrack] = []
        self.removed_stracks: List[STrack] = []
        self._next_id: int = 0

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def reset(self) -> None:
        self.tracked_stracks.clear()
        self.lost_stracks.clear()
        self.removed_stracks.clear()
        self._next_id = 0

    @staticmethod
    def multi_predict(
        stracks: List[STrack], kalman_filter: KalmanFilter
    ) -> None:
        """Vectorized Kalman prediction across a list of STracks."""
        if not stracks:
            return
        means = np.asarray([t.mean for t in stracks], dtype=np.float64)
        covs = np.asarray([t.covariance for t in stracks], dtype=np.float64)
        for i, t in enumerate(stracks):
            if t.state != TrackState.TRACKED:
                means[i, 7] = 0.0
        new_means, new_covs = kalman_filter.multi_predict(means, covs)
        for t, m, c in zip(stracks, new_means, new_covs):
            t.mean = m
            t.covariance = c

    @staticmethod
    def join_stracks(
        list_a: List[STrack], list_b: List[STrack]
    ) -> List[STrack]:
        exists = {t.track_id for t in list_a}
        res = list(list_a)
        for t in list_b:
            if t.track_id not in exists:
                exists.add(t.track_id)
                res.append(t)
        return res

    @staticmethod
    def sub_stracks(
        list_a: List[STrack], list_b: List[STrack]
    ) -> List[STrack]:
        remove_ids = {t.track_id for t in list_b}
        return [t for t in list_a if t.track_id not in remove_ids]

    @staticmethod
    def remove_duplicate_stracks(
        stracks_a: List[STrack], stracks_b: List[STrack], iou_thresh: float = 0.15
    ) -> Tuple[List[STrack], List[STrack]]:
        if not stracks_a or not stracks_b:
            return stracks_a, stracks_b
        boxes_a = np.asarray([t.xyxy for t in stracks_a], dtype=np.float64)
        boxes_b = np.asarray([t.xyxy for t in stracks_b], dtype=np.float64)
        pdist = iou_distance(boxes_a, boxes_b, use_diou=False)
        pairs = np.where(pdist < iou_thresh)
        dup_a, dup_b = set(), set()
        for p, q in zip(*pairs):
            time_p = stracks_a[p].frame_id - stracks_a[p].start_frame
            time_q = stracks_b[q].frame_id - stracks_b[q].start_frame
            if time_p > time_q:
                dup_b.add(int(q))
            else:
                dup_a.add(int(p))
        res_a = [t for i, t in enumerate(stracks_a) if i not in dup_a]
        res_b = [t for j, t in enumerate(stracks_b) if j not in dup_b]
        return res_a, res_b

    def prune_lost(self, current_frame_id: int) -> List[STrack]:
        """Move expired lost tracks to removed_stracks."""
        kept_lost: List[STrack] = []
        newly_removed: List[STrack] = []
        for track in self.lost_stracks:
            if current_frame_id - track.end_frame > self.max_time_lost:
                track.mark_removed()
                newly_removed.append(track)
            else:
                kept_lost.append(track)
        self.lost_stracks = kept_lost
        self.removed_stracks.extend(newly_removed)
        return newly_removed
