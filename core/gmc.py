"""Global Motion Compensation (GMC) for moving, handheld, and UAV cameras."""

from __future__ import annotations

from collections import deque
import time
from typing import Deque, Optional, Sequence, Tuple
import cv2
import numpy as np


VALID_GMC_METHODS = ("none", "sparse_optflow", "phase", "hybrid")


def warp_kalman_state(
    mean: np.ndarray,
    covariance: np.ndarray,
    H: np.ndarray,
    flick_thresh_px: float = 40.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Warp an 8D Kalman state [cx, cy, a, h, vx, vy, va, vh] by 2x3 affine matrix H.

    Transforms position [cx, cy] via H, rotates/scales velocity [vx, vy] via H[:, :2],
    scales height/vh by sqrt(max(1e-4, abs(det(H[:, :2])))), updates covariance via
    J @ P @ J.T (preserving symmetry), and inflates position/velocity variance
    moderately on violent camera pans/flicks (>= flick_thresh_px).
    """
    m = np.asarray(mean, dtype=np.float64).reshape(8)
    cov = np.asarray(covariance, dtype=np.float64).reshape(8, 8)
    h_mat = np.asarray(H, dtype=np.float64).reshape(2, 3)

    rot = h_mat[:, :2]
    trans = h_mat[:, 2]
    det = float(np.linalg.det(rot))
    scale_h = float(np.sqrt(max(1e-4, abs(det))))

    jac = np.eye(8, dtype=np.float64)
    jac[0:2, 0:2] = rot
    jac[3, 3] = scale_h
    jac[4:6, 4:6] = rot
    jac[7, 7] = scale_h

    new_mean = jac @ m
    new_mean[0:2] += trans

    new_cov = jac @ cov @ jac.T
    new_cov = 0.5 * (new_cov + new_cov.T)

    shift_px = float(np.hypot(trans[0], trans[1]))
    if shift_px >= float(flick_thresh_px):
        excess_ratio = min(4.0, shift_px / max(float(flick_thresh_px), 1.0))
        pos_inflate = (0.15 * shift_px) ** 2
        vel_inflate = ((0.05 * shift_px) ** 2) * excess_ratio
        new_cov[0, 0] += pos_inflate
        new_cov[1, 1] += pos_inflate
        new_cov[4, 4] += vel_inflate
        new_cov[5, 5] += vel_inflate

    return new_mean, new_cov


class GlobalMotionCompensator:
    """Estimates inter-frame camera ego-motion as a 2x3 affine matrix (prev -> curr)."""

    def __init__(
        self,
        method: str = "sparse_optflow",
        downscale: int = 2,
        flick_thresh_px: float = 40.0,
        max_corners: int = 200,
        quality_level: float = 0.01,
        min_distance: int = 10,
        min_inliers: int = 8,
    ) -> None:
        method_norm = str(method).strip().lower()
        if method_norm not in VALID_GMC_METHODS:
            raise ValueError(
                f"Unsupported GMC method: {method!r}. Expected one of {VALID_GMC_METHODS}."
            )
        self.method: str = method_norm
        self.downscale: int = max(1, int(downscale))
        self.flick_thresh_px: float = float(flick_thresh_px)
        self.max_corners: int = int(max_corners)
        self.quality_level: float = float(quality_level)
        self.min_distance: int = int(min_distance)
        self.min_inliers: int = int(min_inliers)

        self._prev_gray: Optional[np.ndarray] = None
        self._prev_bboxes: Optional[List[Tuple[float, float, float, float]]] = None
        self._hann_window: Optional[np.ndarray] = None
        self._hann_shape: Optional[Tuple[int, int]] = None
        self.last_shift_px: float = 0.0
        self._timings_ms: Deque[float] = deque(maxlen=200)

    @property
    def avg_gmc_ms(self) -> float:
        if not self._timings_ms:
            return 0.0
        return float(np.mean(self._timings_ms))

    def reset(self) -> None:
        """Reset previous-frame buffer and latency statistics."""
        self._prev_gray = None
        self._prev_bboxes = None
        self._hann_window = None
        self._hann_shape = None
        self.last_shift_px = 0.0
        self._timings_ms.clear()

    @staticmethod
    def _to_uint8_gray(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            gray = frame
        else:
            gray = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_BGR2GRAY)
        if gray.dtype != np.uint8:
            if np.issubdtype(gray.dtype, np.floating) and float(np.max(gray)) <= 1.0:
                gray = np.clip(gray * 255.0, 0.0, 255.0).astype(np.uint8)
            else:
                gray = np.clip(gray, 0, 255).astype(np.uint8)
        return gray

    def _downscale_gray(
        self, gray: np.ndarray
    ) -> Tuple[np.ndarray, float, float]:
        h, w = gray.shape[:2]
        if self.downscale <= 1 or min(h, w) < self.downscale * 16:
            return gray, 1.0, 1.0
        small_w = max(16, w // self.downscale)
        small_h = max(16, h // self.downscale)
        small = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        return small, float(w) / float(small_w), float(h) / float(small_h)

    def _get_hann(self, shape: Tuple[int, int]) -> np.ndarray:
        h, w = shape
        if self._hann_window is None or self._hann_shape != (h, w):
            self._hann_window = cv2.createHanningWindow((w, h), cv2.CV_32F)
            self._hann_shape = (h, w)
        return self._hann_window

    def _estimate_sparse_optflow(
        self,
        prev_small: np.ndarray,
        curr_small: np.ndarray,
        bboxes: Optional[Sequence[Sequence[float]]],
        scale_x: float,
        scale_y: float,
        orig_hw: Tuple[int, int],
    ) -> Tuple[Optional[np.ndarray], int]:
        sh, sw = prev_small.shape[:2]
        mask = np.full((sh, sw), 255, dtype=np.uint8)
        prev_boxes = self._prev_bboxes if self._prev_bboxes else bboxes
        if prev_boxes:
            for box in prev_boxes:
                if len(box) < 4:
                    continue
                x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                pad_x = max(4.0, 0.1 * max(0.0, x2 - x1))
                pad_y = max(4.0, 0.1 * max(0.0, y2 - y1))
                mx1 = max(0, min(sw, int(np.floor((x1 - pad_x) / scale_x))))
                my1 = max(0, min(sh, int(np.floor((y1 - pad_y) / scale_y))))
                mx2 = max(0, min(sw, int(np.ceil((x2 + pad_x) / scale_x))))
                my2 = max(0, min(sh, int(np.ceil((y2 + pad_y) / scale_y))))
                if mx2 > mx1 and my2 > my1:
                    mask[my1:my2, mx1:mx2] = 0

        p0 = cv2.goodFeaturesToTrack(
            prev_small,
            maxCorners=self.max_corners,
            qualityLevel=self.quality_level,
            minDistance=max(2, self.min_distance // max(1, self.downscale)),
            mask=mask,
        )
        if p0 is None or len(p0) < self.min_inliers:
            return None, 0

        p1, status, _ = cv2.calcOpticalFlowPyrLK(
            prev_small,
            curr_small,
            p0,
            None,
            winSize=(21, 21),
            maxLevel=3,
        )
        if p1 is None or status is None:
            return None, 0

        valid = (status.ravel() == 1) & np.isfinite(p1[:, 0, 0]) & np.isfinite(p1[:, 0, 1])
        if bboxes and np.any(valid):
            curr_mask = np.ones(len(p1), dtype=bool)
            px = p1[:, 0, 0] * scale_x
            py = p1[:, 0, 1] * scale_y
            for box in bboxes:
                if len(box) < 4:
                    continue
                x1, y1, x2, y2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                curr_mask &= ~((px >= x1 - 4.0) & (px <= x2 + 4.0) & (py >= y1 - 4.0) & (py <= y2 + 4.0))
            if int(np.sum(valid & curr_mask)) >= self.min_inliers:
                valid &= curr_mask
        pts0 = p0[valid]
        pts1 = p1[valid]
        if len(pts0) < self.min_inliers:
            return None, int(len(pts0))

        h_small, inliers = cv2.estimateAffinePartial2D(
            pts0,
            pts1,
            method=cv2.RANSAC,
            ransacReprojThreshold=3.0,
        )
        inlier_count = int(inliers.sum()) if inliers is not None else 0
        if h_small is None or not np.all(np.isfinite(h_small)) or inlier_count < self.min_inliers:
            return None, inlier_count

        det = float(np.linalg.det(h_small[:, :2]))
        sx_norm = float(np.hypot(h_small[0, 0], h_small[1, 0]))
        sy_norm = float(np.hypot(h_small[0, 1], h_small[1, 1]))
        if not (0.25 <= det <= 4.0 and 0.5 <= sx_norm <= 2.0 and 0.5 <= sy_norm <= 2.0):
            return None, inlier_count

        h_full = h_small.astype(np.float32).copy()
        h_full[0, 2] *= float(scale_x)
        h_full[1, 2] *= float(scale_y)
        max_dim = float(max(orig_hw))
        if np.hypot(float(h_full[0, 2]), float(h_full[1, 2])) > 0.75 * max_dim:
            return None, inlier_count
        return h_full, inlier_count

    def _estimate_phase(
        self,
        prev_small: np.ndarray,
        curr_small: np.ndarray,
        scale_x: float,
        scale_y: float,
        orig_hw: Tuple[int, int],
    ) -> np.ndarray:
        identity = np.eye(2, 3, dtype=np.float32)
        if float(np.std(prev_small)) < 1e-3 or float(np.std(curr_small)) < 1e-3:
            return identity

        hann = self._get_hann(curr_small.shape[:2])
        (dx_s, dy_s), response = cv2.phaseCorrelate(
            prev_small.astype(np.float32),
            curr_small.astype(np.float32),
            window=hann,
        )
        if not (np.isfinite(dx_s) and np.isfinite(dy_s)) or float(response) < 0.05:
            return identity

        tx = float(dx_s * scale_x)
        ty = float(dy_s * scale_y)
        if np.hypot(tx, ty) > 0.75 * float(max(orig_hw)):
            return identity

        return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty]], dtype=np.float32)

    def apply(
        self,
        frame: np.ndarray,
        bboxes: Optional[Sequence[Sequence[float]]] = None,
    ) -> np.ndarray:
        """Estimate 2x3 affine matrix H mapping previous frame into current frame."""
        identity = np.eye(2, 3, dtype=np.float32)
        if self.method == "none" or frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            self.last_shift_px = 0.0
            return identity

        t0 = time.perf_counter()
        orig_hw = (int(frame.shape[0]), int(frame.shape[1]))
        gray = self._to_uint8_gray(frame)
        curr_small, scale_x, scale_y = self._downscale_gray(gray)

        if self._prev_gray is None or self._prev_gray.shape != curr_small.shape:
            self._prev_gray = curr_small
            self._prev_bboxes = (
                [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in bboxes if len(b) >= 4]
                if bboxes
                else None
            )
            self.last_shift_px = 0.0
            self._timings_ms.append((time.perf_counter() - t0) * 1000.0)
            return identity

        if self.method == "phase":
            h_mat = self._estimate_phase(
                self._prev_gray, curr_small, scale_x, scale_y, orig_hw
            )
        elif self.method == "sparse_optflow":
            h_opt, _ = self._estimate_sparse_optflow(
                self._prev_gray, curr_small, bboxes, scale_x, scale_y, orig_hw
            )
            h_mat = h_opt if h_opt is not None else identity
        else:  # hybrid
            h_opt, inliers = self._estimate_sparse_optflow(
                self._prev_gray, curr_small, bboxes, scale_x, scale_y, orig_hw
            )
            if h_opt is not None and inliers >= self.min_inliers:
                h_mat = h_opt
            else:
                h_mat = self._estimate_phase(
                    self._prev_gray, curr_small, scale_x, scale_y, orig_hw
                )

        self._prev_gray = curr_small
        self._prev_bboxes = (
            [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in bboxes if len(b) >= 4]
            if bboxes
            else None
        )
        self.last_shift_px = float(np.hypot(float(h_mat[0, 2]), float(h_mat[1, 2])))
        self._timings_ms.append((time.perf_counter() - t0) * 1000.0)
        return h_mat.astype(np.float32)
