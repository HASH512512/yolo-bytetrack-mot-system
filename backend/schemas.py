"""Pydantic v2 schemas for MOT & Campus Traffic Rule System."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class ViolationType(str, Enum):
    WRONG_WAY = "wrong_way"
    INTRUSION = "intrusion"
    LOITERING = "loitering"


class ZoneType(str, Enum):
    INTRUSION = "intrusion"
    LANE = "lane"
    LOITERING = "loitering"


class DetectorType(str, Enum):
    YOLO = "yolo"
    ONNX = "onnx"
    TENSORRT = "tensorrt"
    MOCK = "mock"


class StreamConfig(BaseModel):
    """Configuration for video stream, detector and tracker."""
    video_source: str = Field(default="demo", description="File path, RTSP URL, device index or 'demo'")
    detector_type: DetectorType = Field(default=DetectorType.MOCK)
    model_path: Optional[str] = Field(default=None, description="Local path to .pt, .onnx or .engine weights")
    device: str = Field(default="cuda:0", description="Inference device ('cuda:0' or 'cpu')")
    half_precision: bool = Field(default=True, description="Enable CUDA FP16 half precision")
    batch_size: int = Field(default=8, ge=1, le=32, description="Inference micro-batch size")
    imgsz: int = Field(default=640, ge=32, description="Detector input image size")
    conf_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    fps: int = Field(default=0, ge=0, le=1000, description="0 = dynamic uncapped (supports 300+ FPS); >0 = target FPS cap")
    playback_mode: str = Field(default="native", description="'native' = fixed original video speed; 'uncapped' = max GPU throughput")
    enable_roi_rules: bool = Field(default=False, description="Enable ROI spatial rules evaluation & ROI guard (default False)")
    use_nsa: bool = Field(default=True, description="Enable Noise-Scale-Adaptive Kalman filter")
    use_diou: bool = Field(default=True, description="Enable DIoU distance matching")
    use_recovery_pool: bool = Field(default=True, description="Enable short-term occlusion recovery pool")
    use_class_aware: bool = Field(default=True, description="Enable class-aware matching penalty")
    use_vdc: bool = Field(default=True, description="Enable velocity direction consistency cost")
    use_oru: bool = Field(default=True, description="Enable observation-centric re-update on track recovery")
    use_lazy_appearance: bool = Field(default=True, description="Enable lazy on-demand HSV appearance gating")
    adaptive_skip: bool = Field(default=False, description="Enable adaptive keyframe skipping")
    max_skip_stride: int = Field(default=2, ge=1, le=16, description="Maximum adaptive keyframe skip stride")
    downsample_fps: float = Field(default=5.0, description="WebSocket push rate (Hz)")


class TaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    video_source: str = Field(default="demo")
    config: Optional[StreamConfig] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    video_source: Optional[str] = None
    status: Optional[TaskStatus] = None
    config: Optional[StreamConfig] = None


class TaskRead(BaseModel):
    id: int
    name: str
    video_source: str
    status: TaskStatus
    config: StreamConfig
    created_at: str
    updated_at: str


class ZoneCreate(BaseModel):
    task_id: int
    name: str = Field(..., min_length=1, max_length=100)
    zone_type: ZoneType
    polygon: List[List[float]] = Field(..., description="[[x, y], ...]")
    direction_vector: Optional[List[float]] = Field(default=None, description="[dx, dy] normalized vector for lane")
    speed_limit: Optional[float] = Field(default=None, description="Speed limit in m/s")
    dwell_limit_sec: Optional[float] = Field(default=None, description="Max dwell seconds before loitering")


class ZoneRead(BaseModel):
    id: int
    task_id: int
    name: str
    zone_type: ZoneType
    polygon: List[List[float]]
    direction_vector: Optional[List[float]] = None
    speed_limit: Optional[float] = None
    dwell_limit_sec: Optional[float] = None
    created_at: str


class TrackItem(BaseModel):
    """Single tracked object state in downsampled stream."""
    track_id: int
    bbox: List[float] = Field(..., description="[x1, y1, x2, y2]")
    class_id: int = 0
    class_name: str = "vehicle"
    conf: float = 0.90
    speed: float = 0.0
    direction: Optional[List[float]] = None
    state: str = "tracked"
    trail: Optional[List[List[float]]] = None


class PerStageLatency(BaseModel):
    """Latency breakdown per pipeline stage (milliseconds)."""
    decode_ms: float = 0.0
    detect_ms: float = 0.0
    track_ms: float = 0.0
    rules_ms: float = 0.0
    total_ms: float = 0.0


class SystemMetrics(BaseModel):
    """System runtime performance metrics."""
    fps: float = 0.0
    p95_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    drop_count: int = 0
    queue_size: int = 0
    detect_skip_ratio: float = 0.0
    is_demo_simulation: bool = False
    active_streams: int = 0
    stage_latency: PerStageLatency = Field(default_factory=PerStageLatency)


class ViolationEvent(BaseModel):
    """Violation alert schema."""
    id: Optional[int] = None
    task_id: int
    track_id: int
    violation_type: ViolationType
    timestamp: float
    frame_id: int
    bbox: List[float] = Field(..., description="[x1, y1, x2, y2]")
    zone_id: Optional[int] = None
    acknowledged: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("violation_type", mode="before")
    @classmethod
    def _normalize_violation_type(cls, value: Any) -> Any:
        if isinstance(value, str) and value == "wrong-way":
            return "wrong_way"
        return value


class TrackFrame(BaseModel):
    """Downsampled real-time trajectory frame pushed to WebSocket (5Hz)."""
    task_id: int
    frame_id: int
    timestamp: float
    frame_width: int = 1280
    frame_height: int = 720
    is_keyframe: bool = True
    is_demo_simulation: bool = False
    tracks: List[TrackItem] = Field(default_factory=list)
    active_violations: List[ViolationEvent] = Field(default_factory=list)
    metrics: SystemMetrics = Field(default_factory=SystemMetrics)


class AblationResult(BaseModel):
    """Ablation experiment evaluation metrics."""
    method: str
    mota: float
    idf1: float
    id_switches: int
    fps: float
    p95_latency_ms: float
    mostly_tracked: float
    mostly_lost: float
    data_kind: str = "synthetic"
    exp_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class TrackPoint(BaseModel):
    """Single trajectory sample point for historical track retrieval/replay."""
    frame_id: int
    timestamp: float
    cx: float
    cy: float
    w: float
    h: float
    speed: float
    class_name: str
    confidence: float


class TrackSummary(BaseModel):
    """Aggregated summary metadata for a persisted trajectory."""
    track_id: int
    class_name: str
    first_frame: int
    last_frame: int
    start_ts: float
    end_ts: float
    point_count: int
    avg_speed: float
    max_speed: float
    violation_count: int = 0


class TrackQueryResponse(BaseModel):
    """Response payload for /api/tracks."""
    task_id: int
    summaries: List[TrackSummary] = Field(default_factory=list)
    points: List[TrackPoint] = Field(default_factory=list)


class LocalAssetsResponse(BaseModel):
    """Response payload listing locally staged videos, datasets, and weights."""
    videos: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    weights: List[str] = Field(default_factory=list)
