"""Pure Python/NumPy ByteTrack two-stage association with configurable ablations."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple
import numpy as np

from core.detector_adapter import Detection
from core.gmc import GlobalMotionCompensator, warp_kalman_state
from core.kalman_filter import KalmanFilter, NSAKalmanFilter
from core.matching import (
    appearance_cosine_distance,
    class_penalty_matrix,
    extract_hsv_histogram_from_hsv,
    fuse_score,
    iou_distance,
    linear_assignment,
    prepare_downsampled_hsv,
    velocity_direction_cost,
)
from core.trajectory_pool import STrack, TrackState, TrajectoryPool


@dataclass
class ByteTrackerConfig:
    """Configuration and ablation switches for ByteTracker."""

    track_thresh: float = 0.5
    low_thresh: float = 0.1
    new_track_thresh: float = 0.6
    match_thresh: float = 0.8
    second_match_thresh: float = 0.5
    unconfirmed_match_thresh: float = 0.7
    max_time_lost: int = 30
    use_nsa: bool = True
    use_diou: bool = True
    use_recovery_pool: bool = True
    use_score_fusion: bool = True
    use_class_aware: bool = True
    class_mismatch_penalty: float = 0.5
    use_vdc: bool = True
    vdc_weight: float = 0.2
    use_oru: bool = True
    use_lazy_appearance: bool = True
    appearance_weight: float = 0.25
    appearance_ambiguity_thresh: float = 0.15
    appearance_update_interval: int = 10
    use_gmc: bool = False
    gmc_method: str = "sparse_optflow"
    gmc_downscale: int = 2
    gmc_flick_thresh_px: float = 40.0

    @classmethod
    def official_baseline(cls) -> "ByteTrackerConfig":
        """Return official ByteTrack baseline configuration (all 6 enhancements off)."""
        return cls(
            use_nsa=False,
            use_diou=False,
            use_recovery_pool=True,
            use_score_fusion=True,
            use_class_aware=False,
            use_vdc=False,
            use_oru=False,
            use_lazy_appearance=False,
            use_gmc=False,
        )

    @classmethod
    def full_enhanced(cls) -> "ByteTrackerConfig":
        """Return full enhanced ByteTrack configuration (all enhancements enabled)."""
        return cls(
            use_nsa=True,
            use_diou=True,
            use_recovery_pool=True,
            use_score_fusion=True,
            use_class_aware=True,
            use_vdc=True,
            use_oru=True,
            use_lazy_appearance=True,
        )


class ByteTracker:
    """Pure Python/NumPy ByteTrack multi-object tracker.

    Supports two-stage association (high-confidence detections first, then
    low-confidence detections) and ablation toggles for NSA-Kalman, DIoU
    matching, and the short-term occlusion recovery pool.
    """

    def __init__(self, config: Optional[ByteTrackerConfig] = None) -> None:
        self.config = config or ByteTrackerConfig()
        self.kalman_filter: KalmanFilter = (
            NSAKalmanFilter() if self.config.use_nsa else KalmanFilter()
        )
        max_lost = self.config.max_time_lost if self.config.use_recovery_pool else 0
        self.pool = TrajectoryPool(max_time_lost=max_lost)
        self.frame_id: int = 0
        self.lazy_appearance_calls: int = 0
        self.total_frames_updated: int = 0
        self.gmc = GlobalMotionCompensator(
            method=self.config.gmc_method,
            downscale=self.config.gmc_downscale,
            flick_thresh_px=self.config.gmc_flick_thresh_px,
        )
        self.last_gmc_shift_px: float = 0.0

    def reset(self) -> None:
        self.frame_id = 0
        self.lazy_appearance_calls = 0
        self.total_frames_updated = 0
        self.last_gmc_shift_px = 0.0
        self.gmc.reset()
        self.pool.reset()

    def _warp_tracks_for_gmc(self, h_mat: np.ndarray) -> None:
        """Apply camera affine transform H (prev -> curr) to tracked and lost pools."""
        if np.allclose(h_mat, np.eye(2, 3, dtype=np.float32), atol=1e-6):
            return

        rot = np.asarray(h_mat[:, :2], dtype=np.float64)
        trans = np.asarray(h_mat[:, 2], dtype=np.float64)
        scale_h = float(np.sqrt(max(1e-4, abs(float(np.linalg.det(rot))))))
        flick_thresh = self.config.gmc_flick_thresh_px

        all_tracks = self.pool.join_stracks(
            self.pool.tracked_stracks, self.pool.lost_stracks
        )
        for track in all_tracks:
            if track.mean is not None and track.covariance is not None:
                track.mean, track.covariance = warp_kalman_state(
                    track.mean,
                    track.covariance,
                    h_mat,
                    flick_thresh_px=flick_thresh,
                )
            if track._last_obs_mean is not None and track._last_obs_cov is not None:
                track._last_obs_mean, track._last_obs_cov = warp_kalman_state(
                    track._last_obs_mean,
                    track._last_obs_cov,
                    h_mat,
                    flick_thresh_px=flick_thresh,
                )
            if track.last_observation is not None:
                xy = rot @ track.last_observation[:2] + trans
                track.last_observation[0] = float(xy[0])
                track.last_observation[1] = float(xy[1])
                track.last_observation[3] = float(
                    max(1.0, track.last_observation[3] * scale_h)
                )
            if track.obs_history:
                warped_obs = deque(maxlen=track.obs_history.maxlen)
                for fid, ox, oy in track.obs_history:
                    nxy = rot @ np.array([ox, oy], dtype=np.float64) + trans
                    warped_obs.append((fid, float(nxy[0]), float(nxy[1])))
                track.obs_history = warped_obs

    def _maybe_update_appearance(
        self,
        track: STrack,
        frame: Optional[np.ndarray],
        feat_provider: Optional[Callable[[], Optional[np.ndarray]]] = None,
        cached_feat: Optional[np.ndarray] = None,
    ) -> None:
        if frame is None or not self.config.use_lazy_appearance:
            return
        interval = max(1, int(self.config.appearance_update_interval))
        if track.appearance_feat is None or (self.frame_id % interval == 0):
            if cached_feat is not None:
                feat = cached_feat
            elif feat_provider is not None:
                feat = feat_provider()
            else:
                feat = None
            track.update_appearance(feat)

    def predict_only(self, frame_id: Optional[int] = None) -> List[STrack]:
        """Advance active and lost Kalman states by one frame without detections."""
        if frame_id is None:
            self.frame_id += 1
        else:
            self.frame_id = int(frame_id)
        self.total_frames_updated += 1

        to_predict = self.pool.join_stracks(
            self.pool.tracked_stracks, self.pool.lost_stracks
        )
        self.pool.multi_predict(to_predict, self.kalman_filter)

        for track in self.pool.tracked_stracks:
            track.frame_id = self.frame_id
            track._record_history(self.frame_id)

        return [t for t in self.pool.tracked_stracks if t.is_activated]

    def update(
        self,
        detections: Sequence[Detection],
        frame_id: Optional[int] = None,
        frame: Optional[np.ndarray] = None,
    ) -> List[STrack]:
        """Advance tracker by one frame and return currently active STracks."""
        if frame_id is None:
            self.frame_id += 1
        else:
            self.frame_id = int(frame_id)
        self.total_frames_updated += 1

        cfg = self.config
        activated_stracks: List[STrack] = []
        refind_stracks: List[STrack] = []
        lost_stracks: List[STrack] = []
        removed_stracks: List[STrack] = []

        # 1. Global Motion Compensation (camera pan / UAV / FPS flick warp)
        if cfg.use_gmc and frame is not None:
            h_mat = self.gmc.apply(frame, bboxes=[d.bbox for d in detections])
            self.last_gmc_shift_px = float(self.gmc.last_shift_px)
            self._warp_tracks_for_gmc(h_mat)
        else:
            self.last_gmc_shift_px = 0.0

        # 2. Partition detections into high-confidence and low-confidence sets
        high_dets: List[Detection] = []
        low_dets: List[Detection] = []
        for det in detections:
            if det.conf >= cfg.track_thresh:
                high_dets.append(det)
            elif det.conf >= cfg.low_thresh:
                low_dets.append(det)

        high_stracks = [STrack(d) for d in high_dets]
        low_stracks = [STrack(d) for d in low_dets]

        # 3. Split existing tracked_stracks into confirmed vs unconfirmed
        unconfirmed: List[STrack] = []
        tracked_stracks: List[STrack] = []
        for track in self.pool.tracked_stracks:
            if not track.is_activated:
                unconfirmed.append(track)
            else:
                tracked_stracks.append(track)

        # Build Stage-1 candidate pool (active + lost if recovery pool is enabled)
        if cfg.use_recovery_pool:
            strack_pool = self.pool.join_stracks(tracked_stracks, self.pool.lost_stracks)
        else:
            strack_pool = list(tracked_stracks)

        # Predict current locations via KalmanFilter / NSAKalmanFilter
        self.pool.multi_predict(strack_pool, self.kalman_filter)

        # 3. Stage-1 Association: strack_pool <-> high_stracks
        pool_boxes = (
            np.asarray([t.xyxy for t in strack_pool], dtype=np.float64)
            if strack_pool
            else np.empty((0, 4), dtype=np.float64)
        )
        high_boxes = (
            np.asarray([t.xyxy for t in high_stracks], dtype=np.float64)
            if high_stracks
            else np.empty((0, 4), dtype=np.float64)
        )
        dists = iou_distance(pool_boxes, high_boxes, use_diou=cfg.use_diou)
        if cfg.use_score_fusion and high_dets:
            dists = fuse_score(dists, high_dets)
        if cfg.use_class_aware and strack_pool and high_dets:
            dists = dists + class_penalty_matrix(
                [t.cls_id for t in strack_pool],
                [d.cls_id for d in high_dets],
                penalty=cfg.class_mismatch_penalty,
            )
        if cfg.use_vdc and strack_pool and high_dets:
            track_vels = np.asarray(
                [t.velocity_vector for t in strack_pool], dtype=np.float64
            )
            track_anchors = np.asarray(
                [
                    (float(t.last_observation[0]), float(t.last_observation[1]))
                    if t.last_observation is not None
                    else t.center
                    for t in strack_pool
                ],
                dtype=np.float64,
            )
            det_centers = 0.5 * (high_boxes[:, :2] + high_boxes[:, 2:])
            dists = dists + cfg.vdc_weight * velocity_direction_cost(
                track_vels, track_anchors, det_centers
            )

        det_feats_cache: Dict[int, Optional[np.ndarray]] = {}
        hsv_cache: Optional[Tuple[np.ndarray, float, float, int, int]] = None
        hsv_ready = False

        def get_det_feat(det_idx: int, bbox: np.ndarray) -> Optional[np.ndarray]:
            nonlocal hsv_cache, hsv_ready
            if det_idx in det_feats_cache:
                return det_feats_cache[det_idx]
            if frame is None:
                det_feats_cache[det_idx] = None
                return None
            if not hsv_ready:
                hsv_cache = prepare_downsampled_hsv(frame)
                hsv_ready = True
            feat = extract_hsv_histogram_from_hsv(
                hsv_cache, bbox, fallback_frame=frame
            )
            det_feats_cache[det_idx] = feat
            return feat

        if (
            cfg.use_lazy_appearance
            and frame is not None
            and strack_pool
            and high_dets
        ):
            triggered_rows: List[int] = []
            for i, track in enumerate(strack_pool):
                row_costs = dists[i]
                row_min = float(np.min(row_costs))
                is_lost_candidate = (
                    track.state == TrackState.LOST
                    and track.appearance_feat is not None
                    and row_min <= cfg.match_thresh + 0.15
                )
                close_cols = int(
                    np.sum(row_costs <= row_min + cfg.appearance_ambiguity_thresh)
                )
                is_ambiguous = row_min <= cfg.match_thresh and close_cols >= 2
                if is_lost_candidate or is_ambiguous:
                    triggered_rows.append(i)

            if triggered_rows:
                self.lazy_appearance_calls += 1
                for i in triggered_rows:
                    track = strack_pool[i]
                    if track.appearance_feat is None:
                        continue
                    row_costs = dists[i]
                    row_min = float(np.min(row_costs))
                    cand_cols = [
                        j
                        for j in range(len(high_dets))
                        if row_costs[j]
                        <= max(
                            cfg.match_thresh + 0.15,
                            row_min + cfg.appearance_ambiguity_thresh,
                        )
                    ]
                    if not cand_cols:
                        continue
                    cand_feats = [
                        get_det_feat(j, high_dets[j].bbox) for j in cand_cols
                    ]
                    app_dists = appearance_cosine_distance(
                        [track.appearance_feat], cand_feats
                    )[0]
                    for idx_c, j in enumerate(cand_cols):
                        dists[i, j] += cfg.appearance_weight * float(app_dists[idx_c])

        matches, u_track, u_detection = linear_assignment(dists, thresh=cfg.match_thresh)

        for itracked, idet in matches:
            det_idx = int(idet)
            track = strack_pool[itracked]
            det_track = high_stracks[det_idx]
            if track.state == TrackState.TRACKED:
                track.update(det_track, self.frame_id)
                self._maybe_update_appearance(
                    track,
                    frame,
                    feat_provider=lambda idx=det_idx: get_det_feat(
                        idx, high_dets[idx].bbox
                    ),
                    cached_feat=det_feats_cache.get(det_idx),
                )
                activated_stracks.append(track)
            else:
                track.re_activate(
                    det_track, self.frame_id, new_id=None, use_oru=cfg.use_oru
                )
                self._maybe_update_appearance(
                    track,
                    frame,
                    feat_provider=lambda idx=det_idx: get_det_feat(
                        idx, high_dets[idx].bbox
                    ),
                    cached_feat=det_feats_cache.get(det_idx),
                )
                refind_stracks.append(track)

        # 4. Stage-2 Association: remaining TRACKED stracks <-> low_stracks
        r_tracked_stracks = [
            strack_pool[i]
            for i in u_track
            if strack_pool[i].state == TrackState.TRACKED
        ]
        r_boxes = (
            np.asarray([t.xyxy for t in r_tracked_stracks], dtype=np.float64)
            if r_tracked_stracks
            else np.empty((0, 4), dtype=np.float64)
        )
        low_boxes = (
            np.asarray([t.xyxy for t in low_stracks], dtype=np.float64)
            if low_stracks
            else np.empty((0, 4), dtype=np.float64)
        )
        dists_second = iou_distance(r_boxes, low_boxes, use_diou=cfg.use_diou)
        if cfg.use_class_aware and r_tracked_stracks and low_dets:
            dists_second = dists_second + class_penalty_matrix(
                [t.cls_id for t in r_tracked_stracks],
                [d.cls_id for d in low_dets],
                penalty=cfg.class_mismatch_penalty,
            )
        matches_second, u_r_track, _ = linear_assignment(
            dists_second, thresh=cfg.second_match_thresh
        )

        for itracked, idet in matches_second:
            track = r_tracked_stracks[itracked]
            det_track = low_stracks[idet]
            if track.state == TrackState.TRACKED:
                track.update(det_track, self.frame_id)
                activated_stracks.append(track)
            else:
                track.re_activate(
                    det_track, self.frame_id, new_id=None, use_oru=cfg.use_oru
                )
                refind_stracks.append(track)

        for it in u_r_track:
            track = r_tracked_stracks[it]
            if track.state != TrackState.LOST:
                if cfg.use_recovery_pool:
                    track.mark_lost()
                    lost_stracks.append(track)
                else:
                    track.mark_removed()
                    removed_stracks.append(track)

        # 5. Associate unconfirmed tracks with remaining high-confidence detections
        rem_high_stracks = [high_stracks[i] for i in u_detection]
        unconf_boxes = (
            np.asarray([t.xyxy for t in unconfirmed], dtype=np.float64)
            if unconfirmed
            else np.empty((0, 4), dtype=np.float64)
        )
        rem_high_boxes = (
            np.asarray([t.xyxy for t in rem_high_stracks], dtype=np.float64)
            if rem_high_stracks
            else np.empty((0, 4), dtype=np.float64)
        )
        dists_unconf = iou_distance(
            unconf_boxes, rem_high_boxes, use_diou=cfg.use_diou
        )
        if cfg.use_score_fusion and rem_high_stracks:
            rem_high_dets = [high_dets[i] for i in u_detection]
            dists_unconf = fuse_score(dists_unconf, rem_high_dets)
        if cfg.use_class_aware and unconfirmed and rem_high_stracks:
            dists_unconf = dists_unconf + class_penalty_matrix(
                [t.cls_id for t in unconfirmed],
                [t.cls_id for t in rem_high_stracks],
                penalty=cfg.class_mismatch_penalty,
            )

        matches_u, u_unconfirmed, u_rem_det = linear_assignment(
            dists_unconf, thresh=cfg.unconfirmed_match_thresh
        )

        for itracked, idet in matches_u:
            unconf_track = unconfirmed[itracked]
            unconf_track.update(rem_high_stracks[idet], self.frame_id)
            orig_idx = int(u_detection[idet])
            self._maybe_update_appearance(
                unconf_track,
                frame,
                feat_provider=lambda idx=orig_idx: get_det_feat(
                    idx, high_dets[idx].bbox
                ),
                cached_feat=det_feats_cache.get(orig_idx),
            )
            activated_stracks.append(unconf_track)

        for it in u_unconfirmed:
            track = unconfirmed[it]
            track.mark_removed()
            removed_stracks.append(track)

        # 6. Initialize new tracks from remaining high-confidence detections
        for idet in u_rem_det:
            track = rem_high_stracks[idet]
            if track.score < cfg.new_track_thresh:
                continue
            track.activate(
                self.kalman_filter, self.frame_id, track_id=self.pool.next_id()
            )
            orig_idx = int(u_detection[idet])
            self._maybe_update_appearance(
                track,
                frame,
                feat_provider=lambda idx=orig_idx: get_det_feat(
                    idx, high_dets[idx].bbox
                ),
                cached_feat=det_feats_cache.get(orig_idx),
            )
            activated_stracks.append(track)

        # 7. Update pool state & prune expired lost tracks
        self.pool.tracked_stracks = [
            t for t in self.pool.tracked_stracks if t.state == TrackState.TRACKED
        ]
        self.pool.tracked_stracks = self.pool.join_stracks(
            self.pool.tracked_stracks, activated_stracks
        )
        self.pool.tracked_stracks = self.pool.join_stracks(
            self.pool.tracked_stracks, refind_stracks
        )

        self.pool.lost_stracks = self.pool.sub_stracks(
            self.pool.lost_stracks, self.pool.tracked_stracks
        )
        if cfg.use_recovery_pool:
            self.pool.lost_stracks.extend(lost_stracks)
            self.pool.prune_lost(self.frame_id)
            self.pool.lost_stracks = self.pool.sub_stracks(
                self.pool.lost_stracks, self.pool.removed_stracks
            )
        else:
            self.pool.lost_stracks.clear()

        self.pool.removed_stracks.extend(removed_stracks)

        self.pool.tracked_stracks, self.pool.lost_stracks = (
            self.pool.remove_duplicate_stracks(
                self.pool.tracked_stracks, self.pool.lost_stracks
            )
        )

        return [t for t in self.pool.tracked_stracks if t.is_activated]
