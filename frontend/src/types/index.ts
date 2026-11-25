export type TaskStatus = 'idle' | 'running' | 'paused' | 'stopped' | 'error';
export type ViolationType = 'wrong_way' | 'wrong-way' | 'intrusion' | 'loitering';
export type ZoneType = 'intrusion' | 'lane' | 'loitering';
export type DetectorType = 'mock' | 'yolo' | 'onnx' | 'tensorrt';
export type ActiveTab = 'monitoring' | 'tracks' | 'tasks' | 'violations' | 'ablation';

export interface StreamConfig {
  video_source: string;
  detector_type?: DetectorType | string;
  model_path?: string | null;
  device?: string;
  half_precision?: boolean;
  batch_size?: number;
  conf_threshold?: number;
  iou_threshold?: number;
  imgsz?: number;
  fps: number;
  playback_mode?: 'native' | 'uncapped' | string;
  enable_roi_rules?: boolean;
  use_nsa: boolean;
  use_diou: boolean;
  use_recovery_pool: boolean;
  use_class_aware?: boolean;
  use_vdc?: boolean;
  use_oru?: boolean;
  use_lazy_appearance?: boolean;
  adaptive_skip?: boolean;
  max_skip_stride?: number;
  downsample_fps: number;
}

export interface Task {
  id: number;
  name: string;
  video_source: string;
  status: TaskStatus;
  config: StreamConfig;
  created_at: string;
  updated_at: string;
}

export interface Zone {
  id: number;
  task_id: number;
  name: string;
  zone_type: ZoneType;
  polygon: [number, number][];
  direction_vector?: [number, number] | null;
  speed_limit?: number | null;
  dwell_limit_sec?: number | null;
  created_at: string;
}

export interface TrackItem {
  track_id: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2]
  class_id: number;
  class_name: string;
  conf: number;
  speed: number;
  direction?: [number, number] | null;
  state: string;
  trail?: [number, number][];
}

export interface PerStageLatency {
  decode_ms: number;
  detect_ms: number;
  track_ms: number;
  rules_ms: number;
  total_ms: number;
}

export interface SystemMetrics {
  fps: number;
  p95_latency_ms: number;
  avg_latency_ms: number;
  drop_count: number;
  queue_size: number;
  stage_latency: PerStageLatency;
  detect_skip_ratio?: number;
  is_demo_simulation?: boolean;
  active_streams?: number;
}

export interface ViolationEvent {
  id?: number;
  task_id: number;
  track_id: number;
  violation_type: ViolationType;
  timestamp: number;
  frame_id: number;
  bbox: [number, number, number, number];
  zone_id?: number | null;
  acknowledged: boolean;
  details?: Record<string, any>;
}

export interface TrackFrame {
  task_id: number;
  frame_id: number;
  timestamp: number;
  tracks: TrackItem[];
  active_violations: ViolationEvent[];
  metrics: SystemMetrics;
  frame_width?: number;
  frame_height?: number;
  is_keyframe?: boolean;
  is_demo_simulation?: boolean;
}

export interface AblationResult {
  method: string;
  mota: number;
  idf1: number;
  id_switches: number;
  fps: number;
  p95_latency_ms: number;
  mostly_tracked: number;
  mostly_lost: number;
  data_kind?: string;
  exp_id?: string | null;
  details?: Record<string, any>;
}

export interface TrackSummary {
  track_id: number;
  class_name: string;
  first_frame: number;
  last_frame: number;
  start_ts: number;
  end_ts: number;
  point_count: number;
  avg_speed: number;
  max_speed: number;
  violation_count: number;
}

export interface TrackPoint {
  frame_id: number;
  timestamp: number;
  cx: number;
  cy: number;
  w: number;
  h: number;
  speed: number;
  class_name: string;
  confidence: number;
}

export interface TrackQueryResponse {
  task_id: number;
  summaries: TrackSummary[];
  points: TrackPoint[];
}

export interface LocalAssetsResponse {
  videos: string[];
  datasets: string[];
  weights: string[];
}

export interface BenchmarkTable {
  title?: string;
  headers: string[];
  rows: (string | number)[][];
  notes?: string;
}

export interface BenchmarkExperiment {
  data_kind?: string;
  lock_file?: string;
  tables_file?: string;
  created_at?: string;
  git_commit?: string;
  git_dirty?: boolean;
  dataset?: Record<string, any>;
  params?: Record<string, any>;
  table_ids?: string[];
  flat_metrics?: Record<string, any>;
  tables?: Record<string, BenchmarkTable>;
  notes?: string;
  [key: string]: any;
}

export interface BenchmarksResponse {
  available: boolean;
  experiments: Record<string, BenchmarkExperiment>;
}
