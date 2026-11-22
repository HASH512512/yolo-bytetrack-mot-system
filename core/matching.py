"""Pure NumPy/SciPy bounding-box similarity matrices, appearance, and linear assignment."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple, TYPE_CHECKING
import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

if TYPE_CHECKING:
    from core.detector_adapter import Detection


def bbox_iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """Compute pairwise IoU matrix between two sets of [x1, y1, x2, y2] boxes.

    Args:
        boxes_a: Array of shape (N, 4).
        boxes_b: Array of shape (M, 4).

    Returns:
        IoU matrix of shape (N, M) with values in [0, 1].
    """
    a = np.asarray(boxes_a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(boxes_b, dtype=np.float64).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float64)

    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    wh = np.maximum(0.0, rb - lt)
    inter = wh[:, :, 0] * wh[:, :, 1]

    area_a = np.maximum(0.0, a[:, 2] - a[:, 0]) * np.maximum(0.0, a[:, 3] - a[:, 1])
    area_b = np.maximum(0.0, b[:, 2] - b[:, 0]) * np.maximum(0.0, b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0.0, inter / np.maximum(union, 1e-12), 0.0)


def bbox_diou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """Compute pairwise Distance-IoU (DIoU) matrix between [x1, y1, x2, y2] boxes.

    DIoU = IoU - (center_dist^2 / enclosing_diag^2), returning values in [-1, 1].
    """
    a = np.asarray(boxes_a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(boxes_b, dtype=np.float64).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float64)

    iou = bbox_iou_matrix(a, b)

    cx_a = 0.5 * (a[:, 0] + a[:, 2])[:, None]
    cy_a = 0.5 * (a[:, 1] + a[:, 3])[:, None]
    cx_b = 0.5 * (b[:, 0] + b[:, 2])[None, :]
    cy_b = 0.5 * (b[:, 1] + b[:, 3])[None, :]
    center_dist_sq = (cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2

    enc_x1 = np.minimum(a[:, None, 0], b[None, :, 0])
    enc_y1 = np.minimum(a[:, None, 1], b[None, :, 1])
    enc_x2 = np.maximum(a[:, None, 2], b[None, :, 2])
    enc_y2 = np.maximum(a[:, None, 3], b[None, :, 3])
    diag_sq = np.maximum((enc_x2 - enc_x1) ** 2 + (enc_y2 - enc_y1) ** 2, 1e-12)

    return iou - (center_dist_sq / diag_sq)


def iou_distance(
    boxes_a: np.ndarray,
    boxes_b: np.ndarray,
    use_diou: bool = False,
) -> np.ndarray:
    """Compute pairwise association cost matrix (1 - IoU or 1 - DIoU)."""
    if use_diou:
        return 1.0 - bbox_diou_matrix(boxes_a, boxes_b)
    return 1.0 - bbox_iou_matrix(boxes_a, boxes_b)


def fuse_score(
    cost_matrix: np.ndarray, detections: Sequence["Detection"]
) -> np.ndarray:
    """Fuse IoU/DIoU similarity with detection confidence (ByteTrack score fusion)."""
    if cost_matrix.size == 0:
        return cost_matrix
    iou_sim = 1.0 - cost_matrix
    det_scores = np.asarray([d.conf for d in detections], dtype=np.float64)[None, :]
    fuse_sim = iou_sim * det_scores
    return 1.0 - fuse_sim


def linear_assignment(
    cost_matrix: np.ndarray, thresh: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Solve minimum-cost bipartite matching with threshold gating.

    Returns:
        matches: (K, 2) array of matched (row_idx, col_idx) pairs.
        unmatched_a: 1D array of unmatched row indices.
        unmatched_b: 1D array of unmatched column indices.
    """
    cost = np.asarray(cost_matrix, dtype=np.float64)
    if cost.size == 0:
        n_rows, n_cols = cost.shape if cost.ndim == 2 else (0, 0)
        return (
            np.empty((0, 2), dtype=int),
            np.arange(n_rows, dtype=int),
            np.arange(n_cols, dtype=int),
        )

    row_ind, col_ind = linear_sum_assignment(cost)
    matches = []
    matched_rows = set()
    matched_cols = set()

    for r, c in zip(row_ind, col_ind):
        if cost[r, c] <= thresh:
            matches.append((r, c))
            matched_rows.add(int(r))
            matched_cols.add(int(c))

    unmatched_a = np.array(
        [i for i in range(cost.shape[0]) if i not in matched_rows], dtype=int
    )
    unmatched_b = np.array(
        [j for j in range(cost.shape[1]) if j not in matched_cols], dtype=int
    )
    matches_arr = (
        np.asarray(matches, dtype=int).reshape(-1, 2)
        if matches
        else np.empty((0, 2), dtype=int)
    )
    return matches_arr, unmatched_a, unmatched_b


def class_penalty_matrix(
    track_classes: Sequence[int],
    det_classes: Sequence[int],
    penalty: float = 0.5,
) -> np.ndarray:
    """Compute (N, M) class-mismatch penalty matrix (0.0 if equal, penalty otherwise)."""
    n = len(track_classes)
    m = len(det_classes)
    if n == 0 or m == 0:
        return np.zeros((n, m), dtype=np.float64)
    tc = np.asarray(track_classes, dtype=np.int64).reshape(-1, 1)
    dc = np.asarray(det_classes, dtype=np.int64).reshape(1, -1)
    return np.where(tc == dc, 0.0, float(penalty)).astype(np.float64)


def velocity_direction_cost(
    track_velocities: np.ndarray,
    track_anchors: np.ndarray,
    det_centers: np.ndarray,
) -> np.ndarray:
    """Compute OC-SORT style Velocity Direction Consistency (VDC) cost in [0, 1].

    Returns 0.5 * (1 - cos_sim) between each track's velocity vector and the
    displacement from track_anchors[i] to det_centers[j], or 0.0 when either
    vector norm is < 1e-3.
    """
    vels = np.asarray(track_velocities, dtype=np.float64).reshape(-1, 2)
    anchors = np.asarray(track_anchors, dtype=np.float64).reshape(-1, 2)
    centers = np.asarray(det_centers, dtype=np.float64).reshape(-1, 2)
    n = len(anchors)
    m = len(centers)
    if n == 0 or m == 0:
        return np.zeros((n, m), dtype=np.float64)

    disp = centers[None, :, :] - anchors[:, None, :]
    disp_norm = np.linalg.norm(disp, axis=2)
    vel_norm = np.linalg.norm(vels, axis=1)[:, None]
    denom = vel_norm * disp_norm
    valid = (vel_norm >= 1e-3) & (disp_norm >= 1e-3)
    dot = np.sum(vels[:, None, :] * disp, axis=2)
    cos_sim = np.where(valid, dot / np.maximum(denom, 1e-12), 1.0)
    cos_sim = np.clip(cos_sim, -1.0, 1.0)
    return np.where(valid, 0.5 * (1.0 - cos_sim), 0.0).astype(np.float64)


def extract_hsv_histogram(
    frame: Optional[np.ndarray],
    bbox: np.ndarray,
    h_bins: int = 16,
    s_bins: int = 8,
) -> Optional[np.ndarray]:
    """Extract L2-normalized upper/lower split HSV histogram feature for a bbox."""
    if frame is None or not isinstance(frame, np.ndarray):
        return None
    if frame.ndim < 3 or frame.shape[2] < 3:
        return None

    img_h, img_w = frame.shape[:2]
    box = np.asarray(bbox, dtype=np.float64).reshape(4)
    x1 = max(0, int(np.floor(box[0])))
    y1 = max(0, int(np.floor(box[1])))
    x2 = min(img_w, int(np.ceil(box[2])))
    y2 = min(img_h, int(np.ceil(box[3])))
    if (x2 - x1) < 4 or (y2 - y1) < 4:
        return None

    crop = frame[y1:y2, x1:x2, :3]
    crop_h, crop_w = crop.shape[:2]
    if crop_h > 64 or crop_w > 48:
        crop = cv2.resize(
            crop,
            (min(crop_w, 48), min(crop_h, 64)),
            interpolation=cv2.INTER_NEAREST,
        )
    if crop.dtype != np.uint8:
        if np.issubdtype(crop.dtype, np.floating) and float(np.max(crop)) <= 1.0:
            crop = np.clip(crop * 255.0, 0.0, 255.0).astype(np.uint8)
        else:
            crop = np.clip(crop, 0, 255).astype(np.uint8)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mid = hsv.shape[0] // 2
    parts = (hsv[:mid, :], hsv[mid:, :])

    hists = []
    for part in parts:
        hist = (
            cv2.calcHist(
                [part],
                [0, 1],
                None,
                [int(h_bins), int(s_bins)],
                [0, 180, 0, 256],
            )
            .reshape(-1)
            .astype(np.float32)
        )
        pnorm = float(np.linalg.norm(hist))
        if pnorm > 1e-8:
            hist = hist / pnorm
        hists.append(hist)

    feat = np.concatenate(hists).astype(np.float32)
    total_norm = float(np.linalg.norm(feat))
    if total_norm <= 1e-8:
        return None
    return (feat / total_norm).astype(np.float32)


def appearance_cosine_distance(
    feats_a: Sequence[Optional[np.ndarray]],
    feats_b: Sequence[Optional[np.ndarray]],
) -> np.ndarray:
    """Compute pairwise cosine distance (1 - dot) clipped to [0, 1]; 0.5 when None."""
    n = len(feats_a)
    m = len(feats_b)
    dist = np.full((n, m), 0.5, dtype=np.float64)
    if n == 0 or m == 0:
        return dist

    idx_a = []
    vecs_a = []
    for i, f in enumerate(feats_a):
        if f is None:
            continue
        arr = np.asarray(f, dtype=np.float64).reshape(-1)
        norm = float(np.linalg.norm(arr))
        if norm > 1e-8:
            idx_a.append(i)
            vecs_a.append(arr / norm)

    idx_b = []
    vecs_b = []
    for j, f in enumerate(feats_b):
        if f is None:
            continue
        arr = np.asarray(f, dtype=np.float64).reshape(-1)
        norm = float(np.linalg.norm(arr))
        if norm > 1e-8:
            idx_b.append(j)
            vecs_b.append(arr / norm)

    if not idx_a or not idx_b:
        return dist

    mat_a = np.asarray(vecs_a, dtype=np.float64)
    mat_b = np.asarray(vecs_b, dtype=np.float64)
    cos_sim = mat_a @ mat_b.T
    sub_dist = np.clip(1.0 - cos_sim, 0.0, 1.0)
    dist[np.ix_(idx_a, idx_b)] = sub_dist
    return dist
