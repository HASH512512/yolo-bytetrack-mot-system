"""Video ingestion, batch scheduling, and campus road rules engine."""

from pipeline.batch_scheduler import (
    AdaptiveKeyframeConfig,
    AdaptiveKeyframeController,
    BatchInferenceScheduler,
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
from pipeline.video_stream import FramePacket, RingBuffer, VideoStreamReader

__all__ = [
    "AdaptiveKeyframeConfig",
    "AdaptiveKeyframeController",
    "BatchInferenceScheduler",
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
]
