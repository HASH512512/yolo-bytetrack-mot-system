"""Pure NumPy 8-state Kalman Filter and Noise Scale Adaptive (NSA) Kalman Filter."""

from __future__ import annotations

from typing import Optional, Tuple
import numpy as np


class KalmanFilter:
    """Standard 8-state constant-velocity Kalman filter for bounding box tracking.

    State vector:
        x = [cx, cy, a, h, vx, vy, va, vh]
    where (cx, cy) is box center, a = w / h is aspect ratio, and h is height.
    """

    def __init__(
        self,
        std_weight_position: float = 1.0 / 20.0,
        std_weight_velocity: float = 1.0 / 160.0,
    ) -> None:
        ndim = 4
        dt = 1.0

        self._motion_mat = np.eye(2 * ndim, dtype=np.float64)
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt

        self._update_mat = np.eye(ndim, 2 * ndim, dtype=np.float64)
        self._std_weight_position = float(std_weight_position)
        self._std_weight_velocity = float(std_weight_velocity)

    def initiate(self, measurement: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Create track state from an unassociated measurement [cx, cy, a, h]."""
        measurement = np.asarray(measurement, dtype=np.float64).reshape(4)
        mean_pos = measurement
        mean_vel = np.zeros_like(mean_pos)
        mean = np.r_[mean_pos, mean_vel]

        h = max(measurement[3], 1.0)
        std = [
            2.0 * self._std_weight_position * h,
            2.0 * self._std_weight_position * h,
            1e-2,
            2.0 * self._std_weight_position * h,
            10.0 * self._std_weight_velocity * h,
            10.0 * self._std_weight_velocity * h,
            1e-5,
            10.0 * self._std_weight_velocity * h,
        ]
        covariance = np.diag(np.square(std))
        return mean, covariance

    def _process_noise_cov(self, h: float) -> np.ndarray:
        h = max(float(h), 1.0)
        std_pos = [
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-2,
            self._std_weight_position * h,
        ]
        std_vel = [
            self._std_weight_velocity * h,
            self._std_weight_velocity * h,
            1e-5,
            self._std_weight_velocity * h,
        ]
        return np.diag(np.square(np.r_[std_pos, std_vel]))

    def _measurement_noise_cov(
        self, mean: np.ndarray, confidence: Optional[float] = None
    ) -> np.ndarray:
        h = max(float(mean[3]), 1.0)
        std = [
            self._std_weight_position * h,
            self._std_weight_position * h,
            1e-1,
            self._std_weight_position * h,
        ]
        return np.diag(np.square(std))

    def predict(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run Kalman prediction step for a single track."""
        motion_cov = self._process_noise_cov(mean[3])
        mean = self._motion_mat @ mean
        covariance = self._motion_mat @ covariance @ self._motion_mat.T + motion_cov
        return mean, covariance

    def multi_predict(
        self, mean: np.ndarray, covariance: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run vectorized Kalman prediction step for N tracks."""
        if len(mean) == 0:
            return mean, covariance

        h = np.maximum(mean[:, 3], 1.0)
        std_pos = np.stack(
            [
                self._std_weight_position * h,
                self._std_weight_position * h,
                np.full_like(h, 1e-2),
                self._std_weight_position * h,
            ],
            axis=1,
        )
        std_vel = np.stack(
            [
                self._std_weight_velocity * h,
                self._std_weight_velocity * h,
                np.full_like(h, 1e-5),
                self._std_weight_velocity * h,
            ],
            axis=1,
        )
        sqr = np.square(np.concatenate([std_pos, std_vel], axis=1))
        motion_cov = np.zeros((len(mean), 8, 8), dtype=np.float64)
        idx = np.arange(8)
        motion_cov[:, idx, idx] = sqr

        mean = mean @ self._motion_mat.T
        covariance = (
            self._motion_mat @ covariance @ self._motion_mat.T + motion_cov
        )
        return mean, covariance

    def project(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        confidence: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Project state distribution to measurement space."""
        innovation_cov = self._measurement_noise_cov(mean, confidence=confidence)
        projected_mean = self._update_mat @ mean
        projected_cov = (
            self._update_mat @ covariance @ self._update_mat.T + innovation_cov
        )
        return projected_mean, projected_cov

    def update(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurement: np.ndarray,
        confidence: Optional[float] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Run Kalman measurement correction step."""
        measurement = np.asarray(measurement, dtype=np.float64).reshape(4)
        projected_mean, projected_cov = self.project(
            mean, covariance, confidence=confidence
        )

        # Solve S * K^T = H * P  =>  K = (S^{-1} H P)^T
        hp = self._update_mat @ covariance
        kalman_gain = np.linalg.solve(projected_cov, hp).T

        innovation = measurement - projected_mean
        new_mean = mean + kalman_gain @ innovation
        new_covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return new_mean, new_covariance

    def gating_distance(
        self,
        mean: np.ndarray,
        covariance: np.ndarray,
        measurements: np.ndarray,
        only_position: bool = False,
        confidence: Optional[float] = None,
    ) -> np.ndarray:
        """Compute squared Mahalanobis distance between state and measurements."""
        if len(measurements) == 0:
            return np.empty((0,), dtype=np.float64)

        projected_mean, projected_cov = self.project(
            mean, covariance, confidence=confidence
        )
        if only_position:
            projected_mean = projected_mean[:2]
            projected_cov = projected_cov[:2, :2]
            measurements = measurements[:, :2]

        d = measurements - projected_mean
        cholesky_factor = np.linalg.cholesky(projected_cov)
        z = np.linalg.solve(cholesky_factor, d.T)
        return np.sum(z * z, axis=0)


class NSAKalmanFilter(KalmanFilter):
    """Noise Scale Adaptive (NSA) Kalman Filter.

    Scales measurement noise covariance R_k inversely with detection confidence
    so low-confidence detections (e.g. partial occlusions) have higher R_k and
    therefore lower Kalman gain, preventing noisy boxes from pulling velocity.
    """

    def __init__(
        self,
        std_weight_position: float = 1.0 / 20.0,
        std_weight_velocity: float = 1.0 / 160.0,
        min_conf: float = 0.05,
        power: float = 1.0,
        mode: str = "inverse",
    ) -> None:
        super().__init__(
            std_weight_position=std_weight_position,
            std_weight_velocity=std_weight_velocity,
        )
        mode_norm = str(mode).strip().lower()
        if mode_norm not in ("inverse", "strongsort"):
            raise ValueError(
                f"Unsupported NSAKalmanFilter mode: {mode!r}. Expected 'inverse' or 'strongsort'."
            )
        self.min_conf = float(min_conf)
        self.power = float(power)
        self.mode = mode_norm

    def noise_scale(self, confidence: Optional[float]) -> float:
        """Compute adaptive R_k multiplier from detection confidence."""
        if confidence is None:
            return 1.0
        c = float(np.clip(confidence, self.min_conf, 1.0))
        if self.mode == "strongsort":
            base = max(0.5, (1.0 - c) * 4.0 + 0.5)
            return float(base ** self.power)
        return float((1.0 / c) ** self.power)

    def _measurement_noise_cov(
        self, mean: np.ndarray, confidence: Optional[float] = None
    ) -> np.ndarray:
        base_r = super()._measurement_noise_cov(mean, confidence=confidence)
        return base_r * self.noise_scale(confidence)
