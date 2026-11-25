"""Video ingestion, batch scheduling, and campus road rules engine."""

from pipeline.batch_scheduler import (
    AdaptiveKeyframeConfig,
    AdaptiveKeyframeController,
    BatchInferenceScheduler,
    FrameResult,
    PipelineResult,
    StageLatency,
)
from pipeline.multi_stream import MultiStreamBatchCoordinator, StreamSpec
from pipeline.rules_engine import (
    CampusRoadRulesEngine,
    GeoFenceZone,
    HomographyMapper,
    LaneConfig,
    LoiteringConfig,
    Violation,
    point_in_polygon,
)
from pipeline.video_stream import (
    FramePacket,
    RingBuffer,
    VideoStreamReader,
    detect_source_native_fps,
    is_live_video_source,
    parse_camera_source,
    parse_screen_source,
)

__all__ = [
    "AdaptiveKeyframeConfig",
    "AdaptiveKeyframeController",
    "BatchInferenceScheduler",
    "FrameResult",
    "MultiStreamBatchCoordinator",
    "PipelineResult",
    "StageLatency",
    "StreamSpec",
    "CampusRoadRulesEngine",
    "GeoFenceZone",
    "HomographyMapper",
    "LaneConfig",
    "LoiteringConfig",
    "Violation",
    "point_in_polygon",
    "FramePacket",
    "RingBuffer",
    "VideoStreamReader",
    "detect_source_native_fps",
    "is_live_video_source",
    "parse_camera_source",
    "parse_screen_source",
]
