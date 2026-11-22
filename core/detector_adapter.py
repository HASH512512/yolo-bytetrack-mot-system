"""Unified detector adapter interfaces and implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Callable, Dict, List, Optional, Sequence, Tuple
import cv2
import numpy as np


@dataclass
class Detection:
    """Standardized single-object detection output.

    Attributes:
        bbox: Bounding box in [x1, y1, x2, y2] pixel coordinates.
        conf: Detection confidence score in [0.0, 1.0].
        cls_id: Integer class ID.
        cls_name: Human-readable class label.
    """

    bbox: np.ndarray
    conf: float
    cls_id: int
    cls_name: str = "object"

    def __post_init__(self) -> None:
        self.bbox = np.asarray(self.bbox, dtype=np.float32).reshape(4)
        self.conf = float(self.conf)
        self.cls_id = int(self.cls_id)

    @property
    def xyxy(self) -> np.ndarray:
        return self.bbox

    @property
    def tlwh(self) -> np.ndarray:
        """Top-left-width-height [x1, y1, w, h]."""
        x1, y1, x2, y2 = self.bbox
        return np.array([x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)], dtype=np.float32)

    @property
    def xywh(self) -> np.ndarray:
        """Center-x, center-y, width, height [cx, cy, w, h]."""
        x1, y1, x2, y2 = self.bbox
        w = max(0.0, x2 - x1)
        h = max(0.0, y2 - y1)
        return np.array([x1 + 0.5 * w, y1 + 0.5 * h, w, h], dtype=np.float32)

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

    def to_xyah(self) -> np.ndarray:
        """Convert to Kalman measurement [cx, cy, aspect_ratio, height]."""
        cx, cy, w, h = self.xywh
        aspect = w / max(h, 1e-6)
        return np.array([cx, cy, aspect, h], dtype=np.float32)


def detections_to_numpy(detections: Sequence[Detection]) -> np.ndarray:
    """Convert a sequence of Detection objects to an (N, 6) array [x1, y1, x2, y2, conf, cls_id]."""
    if not detections:
        return np.empty((0, 6), dtype=np.float32)
    rows = [
        [d.bbox[0], d.bbox[1], d.bbox[2], d.bbox[3], d.conf, float(d.cls_id)]
        for d in detections
    ]
    return np.asarray(rows, dtype=np.float32)


class FramePreprocessor(ABC):
    """Abstract preprocessing hook producing (sub_frame, (offset_x, offset_y)) slices."""

    @abstractmethod
    def preprocess(
        self, frame: np.ndarray
    ) -> List[Tuple[np.ndarray, Tuple[int, int]]]:
        """Transform a frame into one or more slices with top-left offsets."""


class ROISlicePreprocessor(FramePreprocessor):
    """Optional ROI slicing hook reserved for multi-zone or high-res detection.

    Disabled by default: returns [(frame, (0, 0))] without cropping.
    """

    def __init__(
        self,
        rois: Optional[Sequence[Tuple[int, int, int, int]]] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.rois: List[Tuple[int, int, int, int]] = [
            (int(x1), int(y1), int(x2), int(y2)) for x1, y1, x2, y2 in (rois or [])
        ]
        self.enabled: bool = bool(self.rois) if enabled is None else bool(enabled)

    def preprocess(
        self, frame: np.ndarray
    ) -> List[Tuple[np.ndarray, Tuple[int, int]]]:
        if not self.enabled or not self.rois:
            return [(frame, (0, 0))]
        img_h, img_w = frame.shape[:2]
        slices: List[Tuple[np.ndarray, Tuple[int, int]]] = []
        for x1, y1, x2, y2 in self.rois:
            cx1 = max(0, min(img_w, int(x1)))
            cy1 = max(0, min(img_h, int(y1)))
            cx2 = max(0, min(img_w, int(x2)))
            cy2 = max(0, min(img_h, int(y2)))
            if cx2 > cx1 and cy2 > cy1:
                slices.append((frame[cy1:cy2, cx1:cx2], (cx1, cy1)))
        return slices if slices else [(frame, (0, 0))]


def letterbox(
    frame: np.ndarray,
    new_shape: int | Tuple[int, int] = 640,
    color: Tuple[int, int, int] = (114, 114, 114),
) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    """Resize and pad an image while preserving aspect ratio.

    Returns:
        (padded_image, scale, (pad_x, pad_y))
    """
    orig_h, orig_w = frame.shape[:2]
    if isinstance(new_shape, int):
        target_h, target_w = int(new_shape), int(new_shape)
    else:
        target_h, target_w = int(new_shape[0]), int(new_shape[1])

    scale = min(target_h / max(orig_h, 1), target_w / max(orig_w, 1))
    new_w = max(1, int(round(orig_w * scale)))
    new_h = max(1, int(round(orig_h * scale)))

    dw = target_w - new_w
    dh = target_h - new_h
    pad_left = int(round(dw / 2.0 - 0.1))
    pad_right = dw - pad_left
    pad_top = int(round(dh / 2.0 - 0.1))
    pad_bottom = dh - pad_top

    if (orig_w, orig_h) != (new_w, new_h):
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    else:
        resized = frame

    padded = cv2.copyMakeBorder(
        resized,
        pad_top,
        pad_bottom,
        pad_left,
        pad_right,
        cv2.BORDER_CONSTANT,
        value=color,
    )
    return padded, float(scale), (float(pad_left), float(pad_top))


def nms_boxes(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    iou_thresh: float = 0.45,
) -> List[int]:
    """Class-aware Non-Maximum Suppression returning kept indices sorted by score."""
    boxes_arr = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    scores_arr = np.asarray(scores, dtype=np.float64).reshape(-1)
    cls_arr = np.asarray(class_ids, dtype=np.int64).reshape(-1)
    if len(boxes_arr) == 0:
        return []

    kept: List[int] = []
    for cls_id in np.unique(cls_arr):
        cls_mask = np.where(cls_arr == cls_id)[0]
        order = cls_mask[np.argsort(-scores_arr[cls_mask], kind="mergesort")]
        while order.size > 0:
            i = int(order[0])
            kept.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes_arr[i, 0], boxes_arr[rest, 0])
            yy1 = np.maximum(boxes_arr[i, 1], boxes_arr[rest, 1])
            xx2 = np.minimum(boxes_arr[i, 2], boxes_arr[rest, 2])
            yy2 = np.minimum(boxes_arr[i, 3], boxes_arr[rest, 3])
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            area_i = max(0.0, boxes_arr[i, 2] - boxes_arr[i, 0]) * max(
                0.0, boxes_arr[i, 3] - boxes_arr[i, 1]
            )
            area_r = np.maximum(0.0, boxes_arr[rest, 2] - boxes_arr[rest, 0]) * np.maximum(
                0.0, boxes_arr[rest, 3] - boxes_arr[rest, 1]
            )
            union = area_i + area_r - inter
            iou = np.where(union > 0.0, inter / np.maximum(union, 1e-12), 0.0)
            order = rest[iou <= float(iou_thresh)]

    kept.sort(key=lambda idx: -float(scores_arr[idx]))
    return kept


def decode_yolo_output(
    output: np.ndarray,
    orig_shape: Tuple[int, ...],
    scale: float,
    pad: Tuple[float, float],
    conf_thresh: float = 0.25,
    iou_thresh: float = 0.45,
    classes: Optional[Sequence[int]] = None,
    class_names: Optional[Dict[int, str]] = None,
) -> List[Detection]:
    """Decode raw YOLOv8/YOLO11 output tensor in (C, N) or (N, C) layout to Detections."""
    arr = np.asarray(output, dtype=np.float32)
    if arr.ndim == 3:
        if arr.shape[0] != 1:
            raise ValueError(
                f"decode_yolo_output expects 2D or batch-1 3D tensor, got shape {arr.shape}"
            )
        arr = arr[0]
    if arr.ndim != 2 or arr.size == 0:
        return []

    d0, d1 = arr.shape
    if d0 < 5 and d1 < 5:
        return []
    if d0 < 5:
        preds = arr
    elif d1 < 5:
        preds = arr.T
    else:
        nc_scores = arr[:, 4:]
        cn_scores = arr[4:, :]
        nc_probs_ok = bool(np.all((nc_scores >= -1e-4) & (nc_scores <= 1.0 + 1e-4)))
        cn_probs_ok = bool(np.all((cn_scores >= -1e-4) & (cn_scores <= 1.0 + 1e-4)))
        nc_box_gt1 = float(np.max(arr[:, :4])) > 1.0
        cn_box_gt1 = float(np.max(arr[:4, :])) > 1.0

        if cn_probs_ok and cn_box_gt1 and not (nc_probs_ok and nc_box_gt1):
            preds = arr.T
        elif nc_probs_ok and nc_box_gt1 and not (cn_probs_ok and cn_box_gt1):
            preds = arr
        elif cn_probs_ok and not nc_probs_ok:
            preds = arr.T
        elif nc_probs_ok and not cn_probs_ok:
            preds = arr
        elif d0 < d1:
            preds = arr.T
        else:
            preds = arr

    class_scores = preds[:, 4:]
    if class_scores.shape[1] == 0:
        return []

    cls_ids = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(len(preds)), cls_ids]
    mask = scores >= float(conf_thresh)
    if classes is not None:
        allowed = np.asarray(list(classes), dtype=np.int64)
        mask &= np.isin(cls_ids, allowed)

    if not np.any(mask):
        return []

    sel_boxes = preds[mask, :4]
    sel_scores = scores[mask]
    sel_cls = cls_ids[mask]

    orig_h = int(orig_shape[0])
    orig_w = int(orig_shape[1])
    pad_x, pad_y = float(pad[0]), float(pad[1])
    inv_scale = 1.0 / max(float(scale), 1e-12)

    cx = sel_boxes[:, 0]
    cy = sel_boxes[:, 1]
    bw = sel_boxes[:, 2]
    bh = sel_boxes[:, 3]

    x1 = np.clip((cx - 0.5 * bw - pad_x) * inv_scale, 0.0, float(orig_w))
    y1 = np.clip((cy - 0.5 * bh - pad_y) * inv_scale, 0.0, float(orig_h))
    x2 = np.clip((cx + 0.5 * bw - pad_x) * inv_scale, 0.0, float(orig_w))
    y2 = np.clip((cy + 0.5 * bh - pad_y) * inv_scale, 0.0, float(orig_h))

    valid = (x2 > x1) & (y2 > y1)
    if not np.any(valid):
        return []

    xyxy = np.stack([x1[valid], y1[valid], x2[valid], y2[valid]], axis=1)
    sel_scores = sel_scores[valid]
    sel_cls = sel_cls[valid]

    keep = nms_boxes(xyxy, sel_scores, sel_cls, iou_thresh=iou_thresh)
    names = class_names or {}
    return [
        Detection(
            bbox=xyxy[k],
            conf=float(sel_scores[k]),
            cls_id=int(sel_cls[k]),
            cls_name=str(names.get(int(sel_cls[k]), f"class_{int(sel_cls[k])}")),
        )
        for k in keep
    ]


def _offset_and_merge_slice_detections(
    slice_detections: Sequence[Tuple[List[Detection], Tuple[int, int]]],
    orig_shape: Tuple[int, int],
    iou_thresh: float,
) -> List[Detection]:
    """Shift slice-local bounding boxes back to full-frame space and merge overlaps."""
    orig_h, orig_w = orig_shape
    shifted: List[Detection] = []
    for dets, (ox, oy) in slice_detections:
        for det in dets:
            x1 = float(np.clip(det.bbox[0] + ox, 0.0, float(orig_w)))
            y1 = float(np.clip(det.bbox[1] + oy, 0.0, float(orig_h)))
            x2 = float(np.clip(det.bbox[2] + ox, 0.0, float(orig_w)))
            y2 = float(np.clip(det.bbox[3] + oy, 0.0, float(orig_h)))
            if x2 > x1 and y2 > y1:
                shifted.append(
                    Detection(
                        bbox=np.array([x1, y1, x2, y2], dtype=np.float32),
                        conf=det.conf,
                        cls_id=det.cls_id,
                        cls_name=det.cls_name,
                    )
                )
    if len(slice_detections) <= 1 or len(shifted) <= 1:
        return shifted
    boxes = np.asarray([d.bbox for d in shifted], dtype=np.float64)
    scores = np.asarray([d.conf for d in shifted], dtype=np.float64)
    cls_ids = np.asarray([d.cls_id for d in shifted], dtype=np.int64)
    keep = nms_boxes(boxes, scores, cls_ids, iou_thresh=iou_thresh)
    return [shifted[k] for k in keep]


class DetectorAdapter(ABC):
    """Abstract base class decoupling tracking from specific object detectors."""

    @property
    def class_names(self) -> Dict[int, str]:
        return {}

    @abstractmethod
    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run object detection on a single BGR/RGB image frame."""

    def detect_batch(self, frames: Sequence[np.ndarray]) -> List[List[Detection]]:
        """Run object detection on a batch of frames.

        Subclasses can override this for true batched GPU/ONNX inference.
        """
        return [self.detect(frame) for frame in frames]


class YOLODetectorAdapter(DetectorAdapter):
    """Detector adapter for YOLOv8/YOLOv11 (.pt or .onnx) via ultralytics.

    Supports lazy model loading so configuration and unit tests do not require
    weights on disk until inference is actually invoked.
    """

    def __init__(
        self,
        model_path: str | Path = "yolov8n.pt",
        conf_thresh: float = 0.25,
        iou_thresh: float = 0.45,
        classes: Optional[List[int]] = None,
        device: Optional[str] = None,
        imgsz: int = 640,
        half: bool = True,
        lazy_load: bool = True,
        preprocessor: Optional[FramePreprocessor] = None,
    ) -> None:
        self.model_path = str(model_path)
        self.conf_thresh = float(conf_thresh)
        self.iou_thresh = float(iou_thresh)
        self.classes = classes
        self.device = device
        self.imgsz = int(imgsz)
        self.half = bool(half)
        self.preprocessor = preprocessor
        self._model = None
        self._resolved_device: Optional[str] = None
        self._use_fp16: bool = False
        self._names: Dict[int, str] = {}

        if not lazy_load:
            self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not self.model_path or not self.model_path.strip():
            raise FileNotFoundError(f"YOLO weights not found: {self.model_path}")
        model_file = Path(self.model_path)
        if not model_file.is_file():
            raise FileNotFoundError(f"YOLO weights not found: {self.model_path}")

        try:
            import torch  # type: ignore
            from ultralytics import YOLO  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "ultralytics is required for YOLODetectorAdapter. "
                "Install it via `pip install ultralytics`."
            ) from exc

        cuda_ok = bool(torch.cuda.is_available())
        req_dev = (self.device or "").strip()
        if not req_dev or req_dev.lower() == "auto":
            resolved = "cuda:0" if cuda_ok else "cpu"
        elif req_dev.lower().startswith("cuda") and not cuda_ok:
            resolved = "cpu"
        else:
            resolved = req_dev

        self._resolved_device = resolved
        self._use_fp16 = bool(
            self.half and cuda_ok and resolved.lower().startswith("cuda")
        )
        self._model = YOLO(self.model_path)
        try:
            self._model.to(resolved)
        except Exception:
            pass
        names = getattr(self._model, "names", {}) or {}
        self._names = {int(k): str(v) for k, v in names.items()}

    def _predict_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "conf": self.conf_thresh,
            "iou": self.iou_thresh,
            "classes": self.classes,
            "device": self._resolved_device or self.device,
            "imgsz": self.imgsz,
            "verbose": False,
        }
        if self._use_fp16:
            kwargs["quantize"] = "fp16"
        return kwargs

    @property
    def class_names(self) -> Dict[int, str]:
        return dict(self._names)

    def _parse_result(self, result) -> List[Detection]:
        detections: List[Detection] = []
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return detections

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        names = getattr(result, "names", None) or self._names

        for box, conf, cls_id in zip(xyxy, confs, clss):
            detections.append(
                Detection(
                    bbox=box,
                    conf=float(conf),
                    cls_id=int(cls_id),
                    cls_name=str(names.get(int(cls_id), f"class_{int(cls_id)}")),
                )
            )
        return detections

    def detect(self, frame: np.ndarray) -> List[Detection]:
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: Sequence[np.ndarray]) -> List[List[Detection]]:
        if not frames:
            return []
        self._ensure_loaded()
        pred_kwargs = self._predict_kwargs()
        if self.preprocessor is None:
            results = self._model.predict(
                source=list(frames),
                **pred_kwargs,
            )
            return [self._parse_result(res) for res in results]

        slice_images: List[np.ndarray] = []
        frame_slice_meta: List[List[Tuple[int, Tuple[int, int]]]] = []
        for frame in frames:
            slices = self.preprocessor.preprocess(frame)
            meta: List[Tuple[int, Tuple[int, int]]] = []
            for sub_img, offset in slices:
                meta.append((len(slice_images), offset))
                slice_images.append(sub_img)
            frame_slice_meta.append(meta)

        if not slice_images:
            return [[] for _ in frames]

        results = self._model.predict(
            source=slice_images,
            **pred_kwargs,
        )
        parsed = [self._parse_result(res) for res in results]
        outputs: List[List[Detection]] = []
        for frame, meta in zip(frames, frame_slice_meta):
            orig_h, orig_w = frame.shape[:2]
            outputs.append(
                _offset_and_merge_slice_detections(
                    [(parsed[idx], offset) for idx, offset in meta],
                    (orig_h, orig_w),
                    iou_thresh=self.iou_thresh,
                )
            )
        return outputs


def _default_onnx_providers() -> List[str]:
    try:
        import onnxruntime as ort  # type: ignore

        available = ort.get_available_providers()
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except ImportError:
        pass
    return ["CPUExecutionProvider"]


def _parse_metadata_names(raw_names: Optional[str]) -> Dict[int, str]:
    if not raw_names:
        return {}
    text = str(raw_names).strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(parsed, dict):
            return {int(k): str(v) for k, v in parsed.items()}
        if isinstance(parsed, list):
            return {int(i): str(v) for i, v in enumerate(parsed)}
    return {}


class ONNXDetectorAdapter(DetectorAdapter):
    """Pure ONNX Runtime detector adapter for YOLOv8/YOLO11 (zero torch/ultralytics)."""

    def __init__(
        self,
        model_path: str | Path,
        conf_thresh: float = 0.25,
        iou_thresh: float = 0.45,
        classes: Optional[List[int]] = None,
        providers: Optional[List[str]] = None,
        imgsz: int = 640,
        class_names: Optional[Dict[int, str]] = None,
        lazy_load: bool = True,
        preprocessor: Optional[FramePreprocessor] = None,
    ) -> None:
        self.model_path = str(model_path)
        self.conf_thresh = float(conf_thresh)
        self.iou_thresh = float(iou_thresh)
        self.classes = list(classes) if classes is not None else None
        self.providers = (
            list(providers) if providers is not None else _default_onnx_providers()
        )
        self.imgsz = int(imgsz)
        self.preprocessor = preprocessor
        self._names: Dict[int, str] = (
            {int(k): str(v) for k, v in class_names.items()}
            if class_names is not None
            else {}
        )
        self._session = None
        self._input_name: str = "images"
        self._input_shape: List[object] = [1, 3, self.imgsz, self.imgsz]
        self._input_type: str = "tensor(float)"

        if not lazy_load:
            self._ensure_loaded()

    @property
    def class_names(self) -> Dict[int, str]:
        return dict(self._names)

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        if not self.model_path or not self.model_path.strip():
            raise FileNotFoundError(f"ONNX weights not found: {self.model_path}")
        model_file = Path(self.model_path)
        if not model_file.is_file():
            raise FileNotFoundError(f"ONNX weights not found: {self.model_path}")

        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "onnxruntime is required for ONNXDetectorAdapter."
            ) from exc

        self._session = ort.InferenceSession(str(model_file), providers=self.providers)
        inp = self._session.get_inputs()[0]
        self._input_name = str(inp.name)
        self._input_shape = list(getattr(inp, "shape", [1, 3, self.imgsz, self.imgsz]))
        self._input_type = str(getattr(inp, "type", "tensor(float)"))

        if not self._names:
            try:
                meta = self._session.get_modelmeta()
                custom = getattr(meta, "custom_metadata_map", {}) or {}
                self._names = _parse_metadata_names(custom.get("names"))
            except Exception:
                self._names = {}

    def _target_hw(self) -> Tuple[int, int]:
        if (
            len(self._input_shape) >= 4
            and isinstance(self._input_shape[2], int)
            and self._input_shape[2] > 0
            and isinstance(self._input_shape[3], int)
            and self._input_shape[3] > 0
        ):
            return int(self._input_shape[2]), int(self._input_shape[3])
        return self.imgsz, self.imgsz

    def _prepare_tensor(
        self, frame: np.ndarray, target_hw: Tuple[int, int], dtype: np.dtype
    ) -> Tuple[np.ndarray, float, Tuple[float, float]]:
        padded, scale, pad = letterbox(frame, target_hw)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        chw = np.transpose(rgb, (2, 0, 1)).astype(np.float32) / 255.0
        return np.ascontiguousarray(chw, dtype=dtype), scale, pad

    def detect(self, frame: np.ndarray) -> List[Detection]:
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: Sequence[np.ndarray]) -> List[List[Detection]]:
        if not frames:
            return []
        self._ensure_loaded()
        assert self._session is not None

        target_hw = self._target_hw()
        dtype = np.float16 if "float16" in self._input_type.lower() else np.float32

        slice_records: List[Tuple[np.ndarray, Tuple[int, int], float, Tuple[float, float], Tuple[int, int]]] = []
        frame_slice_indices: List[List[int]] = []

        for frame in frames:
            slices = (
                self.preprocessor.preprocess(frame)
                if self.preprocessor is not None
                else [(frame, (0, 0))]
            )
            indices: List[int] = []
            for sub_frame, offset in slices:
                tensor, scale, pad = self._prepare_tensor(sub_frame, target_hw, dtype)
                indices.append(len(slice_records))
                slice_records.append(
                    (tensor, sub_frame.shape[:2], scale, pad, offset)
                )
            frame_slice_indices.append(indices)

        if not slice_records:
            return [[] for _ in frames]

        batch_dim = self._input_shape[0] if self._input_shape else None
        supports_batch = (
            batch_dim is None
            or isinstance(batch_dim, str)
            or (
                isinstance(batch_dim, int)
                and (batch_dim <= 0 or batch_dim == len(slice_records))
            )
        )

        raw_outputs: List[np.ndarray] = []
        if supports_batch:
            batch_input = np.stack([rec[0] for rec in slice_records], axis=0)
            batch_out = self._session.run(None, {self._input_name: batch_input})[0]
            batch_arr = np.asarray(batch_out)
            if batch_arr.ndim == 2 and len(slice_records) == 1:
                raw_outputs = [batch_arr]
            else:
                raw_outputs = [batch_arr[i] for i in range(len(slice_records))]
        else:
            for rec in slice_records:
                single_in = np.expand_dims(rec[0], axis=0)
                single_out = self._session.run(None, {self._input_name: single_in})[0]
                single_arr = np.asarray(single_out)
                raw_outputs.append(single_arr[0] if single_arr.ndim >= 3 else single_arr)

        decoded_slices: List[List[Detection]] = []
        for out, (_, slice_hw, scale, pad, _) in zip(raw_outputs, slice_records):
            decoded_slices.append(
                decode_yolo_output(
                    out,
                    orig_shape=slice_hw,
                    scale=scale,
                    pad=pad,
                    conf_thresh=self.conf_thresh,
                    iou_thresh=self.iou_thresh,
                    classes=self.classes,
                    class_names=self._names,
                )
            )

        results: List[List[Detection]] = []
        for frame, idxs in zip(frames, frame_slice_indices):
            orig_h, orig_w = frame.shape[:2]
            slice_pairs = [
                (decoded_slices[idx], slice_records[idx][4]) for idx in idxs
            ]
            results.append(
                _offset_and_merge_slice_detections(
                    slice_pairs, (orig_h, orig_w), iou_thresh=self.iou_thresh
                )
            )
        return results


class MockDetectorAdapter(DetectorAdapter):
    """Deterministic detector adapter for unit tests, benchmarks, and offline demos."""

    DEFAULT_NAMES: Dict[int, str] = {
        0: "person",
        1: "bicycle",
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
    }

    def __init__(
        self,
        scripted_detections: Optional[Dict[int, List[Detection]]] = None,
        generator: Optional[Callable[[int, np.ndarray], List[Detection]]] = None,
        class_names: Optional[Dict[int, str]] = None,
    ) -> None:
        self._scripted = scripted_detections or {}
        self._generator = generator
        self._names = dict(class_names or self.DEFAULT_NAMES)
        self.frame_index = 0

    @property
    def class_names(self) -> Dict[int, str]:
        return dict(self._names)

    def reset(self) -> None:
        self.frame_index = 0

    def detect(self, frame: np.ndarray) -> List[Detection]:
        idx = self.frame_index
        self.frame_index += 1
        if self._generator is not None:
            return list(self._generator(idx, frame))
        return list(self._scripted.get(idx, []))

    @classmethod
    def create_synthetic_campus_scenario(
        cls,
        num_frames: int = 120,
        frame_size: Tuple[int, int] = (1280, 720),
    ) -> "MockDetectorAdapter":
        """Build a deterministic campus road scenario with normal, wrong-way, intrusion, and loitering targets."""
        scripted: Dict[int, List[Detection]] = {}
        for f in range(num_frames):
            dets: List[Detection] = []
            # Target A: normal car moving left -> right in lane (y ~ 300)
            cx_a = 120.0 + f * 6.0
            dets.append(Detection([cx_a - 40, 275, cx_a + 40, 325], 0.92, 2, "car"))

            # Target B: wrong-way e-bike/motorcycle moving right -> left (y ~ 310)
            cx_b = 1000.0 - f * 5.0
            dets.append(Detection([cx_b - 20, 290, cx_b + 20, 340], 0.85, 3, "motorcycle"))

            # Target C: loitering pedestrian near (600, 500)
            jx = 1.5 * np.sin(f * 0.2)
            dets.append(Detection([585 + jx, 460, 615 + jx, 540], 0.88, 0, "person"))

            scripted[f] = dets
        return cls(scripted_detections=scripted)


def gpu_letterbox_batch(
    frames: Sequence[np.ndarray],
    target_hw: Tuple[int, int] = (640, 640),
    device: str = "cuda:0",
    half: bool = True,
):
    """GPU zero-copy letterbox preprocessing: pinned upload -> BGR2RGB -> resize -> pad.

    Returns:
        batch_tensor: NCHW PyTorch Tensor on `device` normalized to [0, 1].
        metas: List of (orig_h, orig_w, scale, (pad_left, pad_top)) per frame.
    """
    import torch  # type: ignore
    import torch.nn.functional as F  # type: ignore

    target_h, target_w = int(target_hw[0]), int(target_hw[1])
    cuda_ok = bool(torch.cuda.is_available())
    resolved_dev = device if (cuda_ok and str(device).lower().startswith("cuda")) else "cpu"
    use_cuda = resolved_dev.startswith("cuda")
    out_dtype = torch.float16 if (half and use_cuda) else torch.float32

    if not frames:
        empty = torch.empty((0, 3, target_h, target_w), dtype=out_dtype, device=resolved_dev)
        return empty, []

    metas: List[Tuple[int, int, float, Tuple[float, float]]] = []
    # Fast vectorized batch path when all frames share identical shape
    first_shape = frames[0].shape
    same_shape = all(
        isinstance(fr, np.ndarray) and fr.shape == first_shape and fr.dtype == np.uint8
        for fr in frames
    )
    if same_shape and len(first_shape) >= 3:
        orig_h, orig_w = int(first_shape[0]), int(first_shape[1])
        scale = min(float(target_h) / float(orig_h), float(target_w) / float(orig_w))
        new_w = int(round(orig_w * scale))
        new_h = int(round(orig_h * scale))
        dw = target_w - new_w
        dh = target_h - new_h
        pad_left = int(round(dw / 2.0 - 0.1))
        pad_right = dw - pad_left
        pad_top = int(round(dh / 2.0 - 0.1))
        pad_bottom = dh - pad_top

        host_batch = np.ascontiguousarray(np.stack(frames, axis=0)[..., :3])
        t_host = torch.from_numpy(host_batch)
        if use_cuda:
            t_host = t_host.pin_memory()
        t_dev = t_host.to(resolved_dev, non_blocking=use_cuda)
        # BHWC BGR uint8 -> BCHW RGB float32 in [0, 1] directly on GPU
        t_dev = t_dev.permute(0, 3, 1, 2)[:, [2, 1, 0], :, :].to(torch.float32)
        t_dev.mul_(1.0 / 255.0)
        if (orig_w, orig_h) != (new_w, new_h):
            t_dev = F.interpolate(
                t_dev, size=(new_h, new_w), mode="bilinear", align_corners=False
            )
        if pad_left or pad_right or pad_top or pad_bottom:
            t_dev = F.pad(
                t_dev,
                (pad_left, pad_right, pad_top, pad_bottom),
                mode="constant",
                value=114.0 / 255.0,
            )
        if out_dtype == torch.float16:
            t_dev = t_dev.half()
        metas = [(orig_h, orig_w, float(scale), (float(pad_left), float(pad_top))) for _ in frames]
        return t_dev.contiguous(), metas

    tensors = []
    for fr in frames:
        arr = np.ascontiguousarray(fr[..., :3])
        orig_h, orig_w = int(arr.shape[0]), int(arr.shape[1])
        scale = min(float(target_h) / float(orig_h), float(target_w) / float(orig_w))
        new_w = int(round(orig_w * scale))
        new_h = int(round(orig_h * scale))
        dw = target_w - new_w
        dh = target_h - new_h
        pad_left = int(round(dw / 2.0 - 0.1))
        pad_right = dw - pad_left
        pad_top = int(round(dh / 2.0 - 0.1))
        pad_bottom = dh - pad_top

        t_host = torch.from_numpy(arr)
        if use_cuda and arr.dtype == np.uint8:
            t_host = t_host.pin_memory()
        t_dev = t_host.to(resolved_dev, non_blocking=use_cuda)
        t_dev = t_dev.permute(2, 0, 1)[[2, 1, 0], :, :].unsqueeze(0).to(torch.float32)
        if arr.dtype == np.uint8:
            t_dev.mul_(1.0 / 255.0)
        if (orig_w, orig_h) != (new_w, new_h):
            t_dev = F.interpolate(
                t_dev, size=(new_h, new_w), mode="bilinear", align_corners=False
            )
        if pad_left or pad_right or pad_top or pad_bottom:
            t_dev = F.pad(
                t_dev,
                (pad_left, pad_right, pad_top, pad_bottom),
                mode="constant",
                value=114.0 / 255.0,
            )
        tensors.append(t_dev)
        metas.append((orig_h, orig_w, float(scale), (float(pad_left), float(pad_top))))

    stacked = torch.cat(tensors, dim=0)
    if out_dtype == torch.float16:
        stacked = stacked.half()
    return stacked.contiguous(), metas


class TensorRTDetectorAdapter(DetectorAdapter):
    """TensorRT .engine detector adapter with CUDA zero-copy GPU preprocessing."""

    def __init__(
        self,
        model_path: str | Path,
        conf_thresh: float = 0.25,
        iou_thresh: float = 0.45,
        classes: Optional[List[int]] = None,
        device: str = "cuda:0",
        imgsz: int = 640,
        half: bool = True,
        class_names: Optional[Dict[int, str]] = None,
        lazy_load: bool = True,
        preprocessor: Optional[FramePreprocessor] = None,
    ) -> None:
        self.model_path = str(model_path)
        self.conf_thresh = float(conf_thresh)
        self.iou_thresh = float(iou_thresh)
        self.classes = list(classes) if classes is not None else None
        self.device = device or "cuda:0"
        self.imgsz = int(imgsz)
        self.half = bool(half)
        self.preprocessor = preprocessor
        self._explicit_names = dict(class_names or {})
        self._names: Dict[int, str] = dict(class_names or {})

        self._trt_logger = None
        self._runtime = None
        self._engine = None
        self._context = None
        self._input_name: str = ""
        self._output_name: str = ""
        self._input_shape: Tuple[int, ...] = (1, 3, self.imgsz, self.imgsz)
        self._output_tail_shape: Tuple[int, ...] = ()
        self._input_is_fp16: bool = self.half
        self._output_dtype = None
        self._out_buffer = None
        self._cuda_stream = None
        self._lock = threading.Lock()

        if not lazy_load:
            self._ensure_loaded()

    @property
    def class_names(self) -> Dict[int, str]:
        return dict(self._names)

    def _ensure_loaded(self) -> None:
        if self._context is not None:
            return
        if not self.model_path or not self.model_path.strip():
            raise FileNotFoundError(f"TensorRT engine not found: {self.model_path}")
        model_file = Path(self.model_path)
        if not model_file.is_file():
            raise FileNotFoundError(f"TensorRT engine not found: {self.model_path}")

        try:
            import tensorrt as trt  # type: ignore
            import torch  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "tensorrt and CUDA torch are required for TensorRTDetectorAdapter."
            ) from exc

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for TensorRTDetectorAdapter.")

        raw_bytes = model_file.read_bytes()
        engine_bytes = raw_bytes
        if len(raw_bytes) > 8:
            meta_len = int.from_bytes(raw_bytes[:4], "little")
            if 2 <= meta_len <= min(10_000_000, len(raw_bytes) - 4):
                try:
                    meta_json = json.loads(raw_bytes[4 : 4 + meta_len].decode("utf-8"))
                    if isinstance(meta_json, dict):
                        engine_bytes = raw_bytes[4 + meta_len :]
                        if not self._names and "names" in meta_json:
                            self._names = _parse_metadata_names(meta_json["names"])
                except Exception:
                    engine_bytes = raw_bytes

        self._trt_logger = trt.Logger(trt.Logger.WARNING)
        self._runtime = trt.Runtime(self._trt_logger)
        self._engine = self._runtime.deserialize_cuda_engine(engine_bytes)
        if self._engine is None:
            raise RuntimeError(f"Failed to deserialize TensorRT engine: {self.model_path}")
        self._context = self._engine.create_execution_context()

        for i in range(self._engine.num_io_tensors):
            name = self._engine.get_tensor_name(i)
            mode = self._engine.get_tensor_mode(name)
            if mode == trt.TensorIOMode.INPUT and not self._input_name:
                self._input_name = name
                self._input_shape = tuple(int(x) for x in self._engine.get_tensor_shape(name))
                dt = self._engine.get_tensor_dtype(name)
                self._input_is_fp16 = dt == trt.DataType.HALF
            elif mode == trt.TensorIOMode.OUTPUT and not self._output_name:
                self._output_name = name
                out_shape = tuple(int(x) for x in self._engine.get_tensor_shape(name))
                self._output_tail_shape = out_shape[1:]
                out_dt = self._engine.get_tensor_dtype(name)
                if out_dt == trt.DataType.HALF:
                    self._output_dtype = torch.float16
                elif out_dt == trt.DataType.INT32:
                    self._output_dtype = torch.int32
                else:
                    self._output_dtype = torch.float32

        if not self._names:
            try:
                from ultralytics.utils.plotting import class_names as _unused  # noqa: F401
            except Exception:
                pass
            pos_dims = [int(d) for d in self._output_tail_shape if int(d) > 4]
            if pos_dims:
                cand_c = min(pos_dims)
                num_cls = int(cand_c) - 4
                # COCO default mapping fallback for common 80-class YOLOv8 engines
                if num_cls == 80:
                        coco = [
                            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
                            "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
                            "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
                            "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
                            "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
                            "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
                            "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
                            "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
                            "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
                            "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
                            "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
                            "hair drier", "toothbrush",
                        ]
                        self._names = {i: n for i, n in enumerate(coco)}

    def _target_hw(self) -> Tuple[int, int]:
        if len(self._input_shape) >= 4:
            h, w = self._input_shape[2], self._input_shape[3]
            if h > 0 and w > 0:
                return (int(h), int(w))
        return (self.imgsz, self.imgsz)

    def _decode_gpu_item(
        self,
        pred_tensor,
        meta: Tuple[int, int, float, Tuple[float, float]],
    ) -> List[Detection]:
        import torch  # type: ignore

        arr = pred_tensor.float()
        if arr.ndim != 2 or arr.numel() == 0:
            return []
        d0, d1 = int(arr.shape[0]), int(arr.shape[1])
        # YOLO export format: (4 + num_classes, N) -> transpose to (N, 4 + num_classes)
        preds = arr.t() if d0 < d1 else arr
        if preds.shape[1] < 5:
            return []
        scores_mat = preds[:, 4:]
        max_scores, cls_ids = torch.max(scores_mat, dim=1)
        keep_mask = max_scores >= self.conf_thresh
        if not bool(torch.any(keep_mask)):
            return []

        sel_boxes = preds[keep_mask, :4].detach().cpu().numpy()
        sel_scores = max_scores[keep_mask].detach().cpu().numpy()
        sel_cls = cls_ids[keep_mask].detach().cpu().numpy()

        if self.classes is not None:
            cls_mask = np.isin(sel_cls, np.asarray(self.classes, dtype=np.int64))
            sel_boxes = sel_boxes[cls_mask]
            sel_scores = sel_scores[cls_mask]
            sel_cls = sel_cls[cls_mask]
        if len(sel_boxes) == 0:
            return []

        orig_h, orig_w, scale, (pad_x, pad_y) = meta
        inv_scale = 1.0 / max(float(scale), 1e-12)
        cx, cy, bw, bh = sel_boxes[:, 0], sel_boxes[:, 1], sel_boxes[:, 2], sel_boxes[:, 3]
        x1 = np.clip((cx - 0.5 * bw - pad_x) * inv_scale, 0.0, float(orig_w))
        y1 = np.clip((cy - 0.5 * bh - pad_y) * inv_scale, 0.0, float(orig_h))
        x2 = np.clip((cx + 0.5 * bw - pad_x) * inv_scale, 0.0, float(orig_w))
        y2 = np.clip((cy + 0.5 * bh - pad_y) * inv_scale, 0.0, float(orig_h))
        valid = (x2 > x1) & (y2 > y1)
        if not np.any(valid):
            return []

        xyxy = np.stack([x1[valid], y1[valid], x2[valid], y2[valid]], axis=1)
        sel_scores = sel_scores[valid]
        sel_cls = sel_cls[valid]
        keep = nms_boxes(xyxy, sel_scores, sel_cls, iou_thresh=self.iou_thresh)
        names = self._names or {}
        return [
            Detection(
                bbox=xyxy[k],
                conf=float(sel_scores[k]),
                cls_id=int(sel_cls[k]),
                cls_name=str(names.get(int(sel_cls[k]), f"class_{int(sel_cls[k])}")),
            )
            for k in keep
        ]

    def _infer_tensor_batch(
        self,
        batch_tensor,
        metas: List[Tuple[int, int, float, Tuple[float, float]]],
    ) -> List[List[Detection]]:
        import torch  # type: ignore

        n_items = int(batch_tensor.shape[0])
        if n_items == 0:
            return []
        out_list: List[List[Detection]] = [[] for _ in range(n_items)]

        has_dynamic_input = (not self._input_shape) or any(int(d) <= 0 for d in self._input_shape)
        static_b = self._input_shape[0] if self._input_shape else -1
        profile_max_b = None
        if static_b <= 0:
            try:
                prof_shapes = self._engine.get_tensor_profile_shape(self._input_name, 0)
                if prof_shapes and len(prof_shapes) >= 3 and int(prof_shapes[2][0]) > 0:
                    profile_max_b = int(prof_shapes[2][0])
            except Exception:
                profile_max_b = None
        max_chunk = (
            int(static_b)
            if isinstance(static_b, int) and static_b > 0
            else (profile_max_b or n_items)
        )

        with self._lock:
            for start in range(0, n_items, max_chunk):
                end = min(n_items, start + max_chunk)
                chunk = batch_tensor[start:end].contiguous()
                cur_b = int(chunk.shape[0])
                if static_b > 0 and cur_b < static_b:
                    pad_tail = torch.zeros(
                        (static_b - cur_b, *chunk.shape[1:]),
                        dtype=chunk.dtype,
                        device=chunk.device,
                    )
                    run_inp = torch.cat([chunk, pad_tail], dim=0).contiguous()
                else:
                    run_inp = chunk

                if has_dynamic_input:
                    self._context.set_input_shape(self._input_name, tuple(run_inp.shape))
                req_out_shape = tuple(
                    int(x) for x in self._context.get_tensor_shape(self._output_name)
                )
                if any(x <= 0 for x in req_out_shape):
                    run_b = int(run_inp.shape[0])
                    req_out_shape = (run_b, *self._output_tail_shape)

                if (
                    self._out_buffer is None
                    or tuple(self._out_buffer.shape) != req_out_shape
                    or self._out_buffer.device != run_inp.device
                ):
                    self._out_buffer = torch.empty(
                        req_out_shape,
                        dtype=self._output_dtype or torch.float32,
                        device=run_inp.device,
                    )

                if (
                    self._cuda_stream is None
                    or self._cuda_stream.device != run_inp.device
                ):
                    self._cuda_stream = torch.cuda.Stream(device=run_inp.device)
                stream = self._cuda_stream
                stream.wait_stream(torch.cuda.current_stream(run_inp.device))
                self._context.set_tensor_address(self._input_name, int(run_inp.data_ptr()))
                self._context.set_tensor_address(
                    self._output_name, int(self._out_buffer.data_ptr())
                )
                self._context.execute_async_v3(int(stream.cuda_stream))
                stream.synchronize()

                for local_idx in range(cur_b):
                    global_idx = start + local_idx
                    out_list[global_idx] = self._decode_gpu_item(
                        self._out_buffer[local_idx], metas[global_idx]
                    )
        return out_list

    def detect(self, frame: np.ndarray) -> List[Detection]:
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: Sequence[np.ndarray]) -> List[List[Detection]]:
        if not frames:
            return []
        self._ensure_loaded()

        if self.preprocessor is None:
            inp, metas = gpu_letterbox_batch(
                frames,
                target_hw=self._target_hw(),
                device=self.device,
                half=self._input_is_fp16,
            )
            return self._infer_tensor_batch(inp, metas)

        slice_frames: List[np.ndarray] = []
        slice_meta_map: List[List[Tuple[int, Tuple[int, int]]]] = []
        for frame in frames:
            slices = self.preprocessor.preprocess(frame)
            per_frame: List[Tuple[int, Tuple[int, int]]] = []
            for sub_frame, offset in slices:
                per_frame.append((len(slice_frames), offset))
                slice_frames.append(sub_frame)
            slice_meta_map.append(per_frame)

        if not slice_frames:
            return [[] for _ in frames]

        inp, metas = gpu_letterbox_batch(
            slice_frames,
            target_hw=self._target_hw(),
            device=self.device,
            half=self._input_is_fp16,
        )
        slice_dets = self._infer_tensor_batch(inp, metas)
        outputs: List[List[Detection]] = []
        for frame, per_frame in zip(frames, slice_meta_map):
            orig_h, orig_w = frame.shape[:2]
            outputs.append(
                _offset_and_merge_slice_detections(
                    [(slice_dets[idx], offset) for idx, offset in per_frame],
                    (orig_h, orig_w),
                    iou_thresh=self.iou_thresh,
                )
            )
        return outputs


def create_detector_adapter(
    detector_type: str,
    model_path: Optional[str] = None,
    conf_thresh: float = 0.25,
    iou_thresh: float = 0.45,
    classes: Optional[List[int]] = None,
    device: Optional[str] = None,
    imgsz: int = 640,
    half: bool = True,
    providers: Optional[List[str]] = None,
    lazy_load: bool = True,
    preprocessor: Optional[FramePreprocessor] = None,
) -> DetectorAdapter:
    """Factory creating Mock, ONNX, TensorRT, or YOLO detector adapters."""
    kind = str(detector_type).strip().lower()
    if kind == "mock":
        return MockDetectorAdapter.create_synthetic_campus_scenario()
    if kind in ("tensorrt", "trt"):
        if not model_path or not str(model_path).strip():
            raise ValueError("model_path is required when detector_type='tensorrt'.")
        return TensorRTDetectorAdapter(
            model_path=model_path,
            conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
            classes=classes,
            device=device or "cuda:0",
            imgsz=imgsz,
            half=half,
            lazy_load=lazy_load,
            preprocessor=preprocessor,
        )
    if kind == "onnx":
        if not model_path or not str(model_path).strip():
            raise ValueError("model_path is required when detector_type='onnx'.")
        resolved_providers = providers
        if resolved_providers is None and device is not None:
            dev_lower = str(device).strip().lower()
            if dev_lower == "cpu":
                resolved_providers = ["CPUExecutionProvider"]
            elif "cuda" in dev_lower:
                resolved_providers = _default_onnx_providers()
        return ONNXDetectorAdapter(
            model_path=model_path,
            conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
            classes=classes,
            providers=resolved_providers,
            imgsz=imgsz,
            lazy_load=lazy_load,
            preprocessor=preprocessor,
        )
    if kind in ("yolo", "yolov8", "yolo11"):
        resolved_path = model_path if model_path is not None else "yolov8n.pt"
        return YOLODetectorAdapter(
            model_path=resolved_path,
            conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
            classes=classes,
            device=device,
            imgsz=imgsz,
            half=half,
            lazy_load=lazy_load,
            preprocessor=preprocessor,
        )
    raise ValueError(
        f"Unsupported detector_type: {detector_type!r}. Expected 'mock', 'onnx', 'tensorrt', or 'yolo'."
    )
