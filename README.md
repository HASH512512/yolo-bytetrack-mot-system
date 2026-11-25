# yolo-bytetrack-mot-system

基于 YOLO 检测器与改进 ByteTrack 的可扩展多目标跟踪（MOT）系统。项目采用 **Python / FastAPI** 异步流式后端与 **Vue 3 / TypeScript** 前端分离架构，内置轻量化推理权重，支持多源视频接入（本地视频上传、USB/内置摄像头、PC 全屏与区域采集、RTSP/HTTP 网络流、图像序列）、移动相机全局运动补偿（GMC）、实时端到端延迟监控、ROI 空间交通规则告警以及历史轨迹与视频帧精准回放。

---

## 目录

- [1. 项目当前进度与技术栈](#1-项目当前进度与技术栈)
- [2. 系统总体架构与数据流](#2-系统总体架构与数据流)
- [3. 目录结构](#3-目录结构)
- [4. 核心子系统与算法设计](#4-核心子系统与算法设计)
- [5. 数据结构与存储设计](#5-数据结构与存储设计)
- [6. 后端 HTTP 与 WebSocket 路由清单](#6-后端-http-与-websocket-路由清单)
- [7. 前端架构、视图与状态管理](#7-前端架构视图与状态管理)
- [8. 快速开始与测试验证](#8-快速开始与测试验证)

---

## 1. 项目当前进度与技术栈

### 当前完成进度

| 模块层级 | 完成状态 | 核心能力摘要 |
|---|---|---|
| 核心算法层 (`core/`) | 已完成 | `YOLODetectorAdapter` / `ONNXDetectorAdapter` / `TensorRTDetectorAdapter`（含 CUDA 锁页内存零拷贝前处理）、`ByteTracker` 两阶段关联、NSA-Kalman、DIoU + VDC + ORU、按帧单次转换缓存的 Lazy HSV、移动相机 GMC（`sparse_optflow` / `phase` / `hybrid`） |
| 流水线调度层 (`pipeline/`) | 已完成 | `RingBuffer` 背压环形缓冲、`VideoStreamReader` 多源接入（MP4 循环 / 图像序列 `seqinfo.ini` / `camera://` / `screen://` DXGI·MSS·PIL 采集 / RTSP 断线重连）、`BatchInferenceScheduler` 微批调度与自适应关键帧跳帧、`CampusRoadRulesEngine` ROI 规则判定 |
| 后端服务层 (`backend/`) | 已完成 | FastAPI 异步任务管理、26 个 REST/WS 路由、SQLite WAL 四表五索引异步落库、30 Hz MJPEG + WebSocket 双通道推流、600 帧内存 LRU + 磁盘 `data/replay_cache/` 单帧回放缓存、视频上传与流预设接口 |
| 前端工作台 (`frontend/`) | 已完成 | Pinia 单一真值状态管理、4 个核心功能页（实时监控画布 / 轨迹检索与回放 / 违章预警与任务 / 算法消融评测）、独立 SVG 分阶段延迟折线图、快速接入新视频流面板、GMC 与后端参数实时切换 |
| 权重与测试 (`weights/`, `tests/`) | 已完成 | 仓库内置 `weights/yolov8n.pt` 与 `weights/yolov8n.onnx`；覆盖核心算法、流水线与后端接口共 49 项 `pytest` 用例及 `vue-tsc` / `vite build` 验证 |

### 技术栈

- **检测与推理**：Ultralytics YOLOv8 (`.pt`)、ONNX Runtime (`.onnx`)、TensorRT 11 + PyTorch CUDA Zero-Copy Letterbox (`.engine`)
- **跟踪与视觉计算**：NumPy、SciPy（匈牙利线性指派 `linear_sum_assignment`）、OpenCV（稀疏光流 `calcOpticalFlowPyrLK`、相位相关 `phaseCorrelate`、RANSAC 仿射矩阵、HSV 直方图）
- **采集与解码**：OpenCV `VideoCapture`、`PyAV`、`dxcam` / `mss` / `Pillow ImageGrab`
- **后端与存储**：FastAPI、Uvicorn、Pydantic v2、SQLite 3（WAL 模式）
- **前端工程**：Vue 3 (`<script setup>`)、TypeScript、Pinia、Vite 6、Tailwind CSS v4

---

## 2. 系统总体架构与数据流

系统将“**解码采集 — 模型推理 — 状态跟踪与运动补偿 — 规则评估 — 编码推流/落库**”解耦为独立流水线阶段，前端通过 HTTP 控制面、WebSocket 矢量元数据面与 MJPEG 视频流面完成低延迟联动展示。

```mermaid
flowchart LR
    subgraph Input["视频源接入层 pipeline/video_stream.py"]
        V1["本地视频/上传 MP4"]
        V2["USB 摄像头 camera://0"]
        V3["PC 桌面/区域采集 screen://0"]
        V4["网络流 RTSP / HTTP / 图像序列"]
    end

    subgraph Engine["流式调度与核心引擎 pipeline/ + core/"]
        RB["RingBuffer 环形缓冲 (oldest/block 背压)"]
        AD["AdaptiveKeyframeController 自适应跳帧与补帧"]
        DET["DetectorAdapter (YOLO .pt / ONNX / TensorRT)"]
        GMC["GlobalMotionCompensator (光流 / 相位 / Hybrid)"]
        BT["ByteTracker (NSA-Kalman + DIoU/VDC/ORU)"]
        RE["CampusRoadRulesEngine (Homography 测速 + ROI)"]
    end

    subgraph Service["FastAPI 服务与存储 backend/"]
        ENC["异步 JPEG 编码线程"]
        CACHE["回放双层缓存 (内存 600帧 LRU + 磁盘快照)"]
        DB["SQLite WAL 写队列线程 (5 Hz 降采样落库)"]
        API["REST API + MJPEG + WS"]
    end

    subgraph UI["Vue 3 前端工作台 frontend/src/"]
        STORE["Pinia motStore (单一真值状态表)"]
        MON["LiveMonitoringView (MJPEG 底图 + Canvas 矢量框)"]
        CHART["LatencyChart.vue (分阶段耗时折线图)"]
        REP["TrackRetrievalView (历史帧底图 + 轨迹回放)"]
    end

    Input --> RB --> AD
    AD -->|关键帧微批| DET --> GMC --> BT
    AD -->|非关键帧外推| BT
    BT --> RE
    RE --> ENC --> API
    RE --> CACHE --> API
    RE --> DB --> API
    API -->|MJPEG & WS 30Hz| STORE
    STORE --> MON
    STORE --> CHART
    API -->|单帧 JPEG & 轨迹查询| REP
```

### 并发与解耦设计要点

1. **采集与计算解耦**：`VideoStreamReader` 在独立后台线程持续读取帧并写入有界 `RingBuffer`（图像序列模式带预取线程池与容量上限内存缓存），按 `playback_mode` 区分**固定源帧率播放 (`native`)** 与**无上限吞吐压测 (`uncapped`)**。
2. **推理与序列化解耦**：JPEG 压缩由 `TaskStreamRunner` 内独立 `_jpeg_thread` 按 `1/30 s` 节流异步编码；WebSocket 仅发送轻量级 `TrackFrame` JSON 矢量坐标；SQLite 轨迹写入通过 `_db_queue` 在专门写线程内以 `5 Hz`（可配 `downsample_fps`）批量事务提交，避免阻塞主推理线程。
3. **单路互斥与启停严格联动**：前端 `motStore.singleStreamExclusive` 默认为 `true`，开启新任务时自动停止其他运行中任务防止双路争抢 GPU；任务停止时同步清空前端 Canvas、断开 WS 并清理后端实时帧状态，防止残留幽灵检测框。

---

## 3. 目录结构

```text
yolo-bytetrack-mot-system/
├── weights/                             # 仓库内置便携推理权重
│   ├── yolov8n.pt                       # PyTorch YOLOv8n 权重（开箱即用）
│   └── yolov8n.onnx                     # ONNX YOLOv8n 权重
├── core/                                # 检测适配器、相机补偿与 ByteTrack 核心算法
│   ├── __init__.py
│   ├── detector_adapter.py              # DetectorAdapter 抽象与 YOLO/ONNX/TensorRT/Mock 实现、gpu_letterbox_batch
│   ├── gmc.py                           # GlobalMotionCompensator 与 warp_kalman_state
│   ├── byte_tracker.py                  # ByteTracker 主控、ByteTrackerConfig（支持 7 项模块开关）
│   ├── kalman_filter.py                 # 8 状态恒速卡尔曼滤波器与 NSA-Kalman 噪声自适应扩展
│   ├── matching.py                      # IoU/DIoU、类别惩罚、VDC 方向代价、Lazy HSV 与线性指派
│   └── trajectory_pool.py               # STrack 生命周期管理、TrajectoryPool 与 ORU 遮挡重更新
├── pipeline/                            # 流式采集、调度协调与业务规则层
│   ├── __init__.py
│   ├── video_stream.py                  # VideoStreamReader、RingBuffer、URI 解析与多端采集适配
│   ├── batch_scheduler.py               # BatchInferenceScheduler 微批调度与 AdaptiveKeyframeController
│   ├── multi_stream.py                  # MultiStreamBatchCoordinator 多路共享检测协调器
│   └── rules_engine.py                  # HomographyMapper 单应映射测速与 CampusRoadRulesEngine
├── backend/                             # FastAPI 后端与 SQLite 持久化层
│   ├── __init__.py
│   ├── schemas.py                       # Pydantic v2 请求/响应与配置数据模型
│   ├── db.py                            # SQLite WAL 连接管理、四表五索引建表与 CRUD
│   ├── stream_engine.py                 # TaskStreamRunner、StreamManager 与回放缓存管理
│   └── server.py                        # FastAPI 路由注册、静态资产扫描、启动种子初始化与 WS 服务
├── frontend/                            # Vue 3 + Vite + TypeScript + Tailwind v4 工作台
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts                   # /api 与 /ws 反向代理至 127.0.0.1:8000
│   └── src/
│       ├── App.vue                      # 顶栏导航（4 个视图页签）与全局任务下拉/WS 指示
│       ├── types/index.ts               # 全量前后端对应 TypeScript 接口定义
│       ├── stores/motStore.ts           # Pinia 全局状态（单一真值映射、WS 生命周期与 API 调用）
│       └── components/
│           ├── LiveMonitoringView.vue   # 多画面视频监控、矢量叠加画布、流接入栏与参数设置抽屉
│           ├── LatencyChart.vue         # 60 帧滚动窗口分阶段耗时（Decode/Detect/Track/Rules/P95）SVG 折线图
│           ├── TrackRetrievalView.vue   # 历史轨迹检索与等比视频底图逐帧回放舞台
│           ├── TaskViolationView.vue    # 任务创建（多源接入）、ROI 电子围栏配置与违章事件处理
│           └── AblationMetricsView.vue  # ByteTrack 消融对比表与系统压测数据展示页
├── evaluation/                          # 离线算法消融对比与基准表格生成脚本
│   ├── ablation_experiments.py          # MOT 评测指标与 7 组模块消融套件
│   └── make_reproduction_table.py       # Markdown / JSON 评测报告格式化输出
├── tests/                               # 单元测试与集成测试（49 passed）
│   ├── test_core.py
│   ├── test_pipeline.py
│   └── test_backend.py
├── requirements.txt
└── README.md
```

---

## 4. 核心子系统与算法设计

### 4.1 多后端检测适配器 (`core/detector_adapter.py`)

所有检测后端统一继承 `DetectorAdapter`（核心方法 `detect(frame)` 与 `detect_batch(frames)`）：

| 适配器类 | `detector_type` | 支持权重格式 | 核心技术特点 |
|---|---|---|---|
| `YOLODetectorAdapter` | `yolo` | `.pt` | 支持 PyTorch CUDA / FP16 半精度与原生 Batch 推理 |
| `ONNXDetectorAdapter` | `onnx` | `.onnx` | 无 PyTorch 运行时依赖，自动检测 CUDA / CPU ExecutionProvider 与动态/静态 Batch |
| `TensorRTDetectorAdapter` | `tensorrt` | `.engine` | 配合 `gpu_letterbox_batch`（锁页内存 `pin_memory` → GPU 端 BGR2RGB + `F.interpolate` + `F.pad`）与独立非默认 `torch.cuda.Stream` 执行 `execute_async_v3`，并在 GPU 完成置信度阈值筛选后再回传 CPU NMS |
| `MockDetectorAdapter` | `mock` | 无需权重 | 内置确定性校园交通场景生成器，用于无硬件/无视频环境下的端到端功能测试 |

### 4.2 移动相机全局运动补偿 (`core/gmc.py`) 与改进 ByteTrack (`core/byte_tracker.py`)

为解决航拍无人机平移晃动及 FPS 游戏/云台快速甩镜造成的全局背景偏移，在标准 ByteTrack 前置了全局运动补偿模块 `GlobalMotionCompensator`：

1. **三类可切换运动估计模式**（通过 `StreamConfig.gmc_method` 配置）：
   - `sparse_optflow`：灰度降采样（`downscale=2`）后，自动对当前检测框 `bboxes` 区域生成前景屏蔽掩码，仅在背景区域提取 Shi-Tomasi 角点并计算金字塔 LK 稀疏光流（`calcOpticalFlowPyrLK`），再经 RANSAC 估计 2×3 部分仿射矩阵 `H`。
   - `phase`：对降采样灰度帧施加二维 Hann 窗执行傅里叶相位相关（`phaseCorrelate`），在稀疏特征点大面积丢失或严重运动模糊时仍能估计全局亚像素平移向量。
   - `hybrid`：优先执行 `sparse_optflow`，当有效背景内点数量不足（`< 8`）或拟合失效时无缝自动降级至 `phase`。
2. **卡尔曼状态空间与协方差传播（`warp_kalman_state`）**：
   - 在每帧 `multi_predict` 与匹配前，利用 `H` 将活跃轨与恢复池轨迹的 8 维状态均值 `[cx, cy, a, h, vx, vy, va, vh]` 及其协方差矩阵 `P` 按雅可比传播 `P' = J * P * J^T` 对齐到当前帧坐标系，按 `sqrt(|det(R)|)` 调整尺度 `h`/`vh`；
   - 当帧间全局位移 `hypot(tx, ty) >= flick_thresh_px`（默认 `40 px`）时，适度放大位置与速度先验方差，提高剧烈甩镜后的再关联成功率。
3. **跟踪器消融开关矩阵**（`ByteTrackerConfig`）：
   - `use_nsa`：按检测置信度动态缩放观测噪声协方差矩阵；
   - `use_diou`：以距离交并比（DIoU）替代纯 IoU 计算空间代价值；
   - `use_class_aware`：跨类别匹配置加惩罚 `class_mismatch_penalty=0.5` 抑制误配；
   - `use_vdc`：引入轨迹历史运动方向与候选位移向量余弦一致性约束（`vdc_weight=0.2`）；
   - `use_oru`：失配轨迹被恢复池重激活后，按间隙线性重放观测更新滤波器速度项；
   - `use_lazy_appearance`：仅在存在竞争歧义或待恢复目标时，按帧执行单次降采样（最大宽/高 `640px`）整图 BGR→HSV 转换并缓存各框上下半区直方图（默认在实时配置中关闭以保最高 FPS）。

### 4.3 多源接入与单帧回放缓存 (`pipeline/video_stream.py`, `backend/stream_engine.py`)

- **支持的 `video_source` URI 协议与解析**：
  - `demo`：内置 180 帧合成校园道路场景；
  - `camera://0` 或数字 `"0"`：本地 USB/内置摄像头（Windows 下优先使用 `cv2.CAP_DSHOW`，失败自动回退）；
  - `screen://0` 或 `screen://0?x=320&y=180&w=1280&h=720`：PC 全屏或指定屏幕区域实时采集（采集后端按 `dxcam` → `mss` → `PIL.ImageGrab` 自动探测降级）；
  - `rtsp://` / `rtsps://` / `rtmp://` / `http://` / `https://`：网络流接入，内置断线指数退避自动重连；
  - 本地视频文件路径（`.mp4/.avi/.mov/.mkv/.webm`）或图像序列目录（自动解析同级 `seqinfo.ini` 的 `imWidth`/`imHeight`/`frameRate`）。
- **单帧回放双层缓存机制**：
  - 每次 `TaskStreamRunner` 按 `downsample_fps` 持久化轨迹点时，将对应图像编码为最大宽度 `1280px` 的 JPEG 并保留原始分辨率，同步写入 **600 帧内存 LRU (`OrderedDict`)** 与 **磁盘目录 `data/replay_cache/task_{task_id}/{frame_id}.jpg`（含 `meta.json`）**；任务删除时同步清理对应磁盘缓存目录。

---

## 5. 数据结构与存储设计

### 5.1 核心 API / 内存数据结构 (`backend/schemas.py`)

| 数据模型 | 主要字段与说明 |
|---|---|
| `StreamConfig` | `video_source`、`detector_type` (`yolo`/`onnx`/`tensorrt`/`mock`)、`model_path`、`device` (`cuda:0`/`cpu`)、`half_precision`、`batch_size` (1–32)、`imgsz`、`conf_threshold`、`iou_threshold`、`fps` (0=不限速)、`playback_mode` (`native`/`uncapped`)、`enable_roi_rules`（默认 `false`）、`use_nsa`/`use_diou`/`use_recovery_pool`/`use_class_aware`/`use_vdc`/`use_oru`/`use_lazy_appearance`/`use_gmc`、`gmc_method` (`none`/`sparse_optflow`/`phase`/`hybrid`)、`adaptive_skip`、`max_skip_stride`、`downsample_fps` |
| `TrackItem` | `track_id`、`bbox` `[x1, y1, x2, y2]`、`class_id`、`class_name`、`conf`、`speed` (km/h，基于卡尔曼速度或帧号差+单应映射及 EMA 平滑)、`direction` `[dx, dy]`、`state` (`tracked`/`lost`)、`trail`（最近 25 个历史中心点） |
| `SystemMetrics` | `fps`、`p95_latency_ms`、`avg_latency_ms`、`drop_count`、`queue_size`、`detect_skip_ratio`、`gmc_shift_px`、`is_demo_simulation`、`active_streams`、`stage_latency`（`PerStageLatency`: `decode_ms` / `detect_ms` / `track_ms` / `rules_ms` / `total_ms`） |
| `TrackFrame` | `task_id`、`frame_id`、`timestamp`、`frame_width`、`frame_height`、`is_keyframe`、`is_demo_simulation`、`tracks: TrackItem[]`、`active_violations: ViolationEvent[]`、`metrics: SystemMetrics` |
| `ZoneRead` / `ZoneCreate` | `id`、`task_id`、`name`、`zone_type` (`intrusion`/`lane`/`loitering`)、`polygon` `[[x, y], ...]`、`direction_vector`、`speed_limit`、`dwell_limit_sec`、`created_at` |
| `ViolationEvent` | `id`、`task_id`、`track_id`、`violation_type` (`wrong_way`/`intrusion`/`loitering`，兼容规范化 `wrong-way`)、`timestamp`、`frame_id`、`bbox`、`zone_id`、`acknowledged`、`details` |
| `TrackQueryResponse` | `task_id`、`summaries: TrackSummary[]`（含 `first_frame`/`last_frame`/`point_count`/`avg_speed`/`max_speed`/`violation_count`）、`points: TrackPoint[]`（`frame_id`/`timestamp`/`cx`/`cy`/`w`/`h`/`speed`/`class_name`/`confidence`） |

**WebSocket `/ws/stream/{task_id}` 推送帧 JSON 示例**：

```json
{
  "task_id": 1,
  "frame_id": 120,
  "timestamp": 1764070400.25,
  "frame_width": 1920,
  "frame_height": 1080,
  "is_keyframe": true,
  "is_demo_simulation": false,
  "tracks": [
    {
      "track_id": 7,
      "bbox": [412.5, 318.0, 486.2, 510.4],
      "class_id": 0,
      "class_name": "person",
      "conf": 0.87,
      "speed": 4.6,
      "direction": [0.92, -0.39],
      "state": "tracked",
      "trail": [[430.0, 412.0], [449.3, 414.2]]
    }
  ],
  "active_violations": [],
  "metrics": {
    "fps": 30.0,
    "p95_latency_ms": 14.8,
    "avg_latency_ms": 11.2,
    "drop_count": 0,
    "queue_size": 2,
    "detect_skip_ratio": 0.42,
    "gmc_shift_px": 1.35,
    "is_demo_simulation": false,
    "active_streams": 1,
    "stage_latency": {
      "decode_ms": 1.4,
      "detect_ms": 7.9,
      "track_ms": 1.6,
      "rules_ms": 0.3,
      "total_ms": 11.2
    }
  }
}
```

### 5.2 数据库设计 (`backend/db.py`)

默认路径为 `data/mot_system.db`，采用线程本地连接池（自动回收失效线程句柄）与 SQLite 高并发配置：
- `PRAGMA journal_mode = WAL;`（读写并发不互斥）
- `PRAGMA synchronous = NORMAL;`
- `PRAGMA foreign_keys = ON;`（删除任务自动级联清理区域、轨迹与违章记录）

```mermaid
erDiagram
    tasks ||--o{ zones : "has (ON DELETE CASCADE)"
    tasks ||--o{ trajectories : "records (ON DELETE CASCADE)"
    tasks ||--o{ violations : "triggers (ON DELETE CASCADE)"

    tasks {
        INTEGER id PK
        TEXT name
        TEXT video_source
        TEXT status
        TEXT config "StreamConfig JSON"
        TEXT created_at
        TEXT updated_at
    }
    zones {
        INTEGER id PK
        INTEGER task_id FK
        TEXT name
        TEXT zone_type
        TEXT polygon "JSON [[x,y],...]"
        TEXT direction_vector "JSON [dx,dy]"
        REAL speed_limit
        REAL dwell_limit_sec
        TEXT created_at
    }
    trajectories {
        INTEGER id PK
        INTEGER task_id FK
        INTEGER track_id
        INTEGER frame_id
        REAL timestamp
        REAL x
        REAL y
        REAL w
        REAL h
        TEXT class_name
        REAL confidence
        REAL speed
        TEXT direction "JSON [dx,dy]"
        TEXT created_at
    }
    violations {
        INTEGER id PK
        INTEGER task_id FK
        INTEGER track_id
        TEXT violation_type
        REAL timestamp
        INTEGER frame_id
        TEXT bbox "JSON [x1,y1,x2,y2]"
        INTEGER zone_id
        INTEGER acknowledged
        TEXT details "JSON"
        TEXT created_at
    }
```

**核心复合索引设计**：
- `idx_trajectories_track` (`trajectories(task_id, track_id)`)
- `idx_trajectories_frame` (`trajectories(task_id, frame_id)`)
- `idx_trajectories_task_track_ts` (`trajectories(task_id, track_id, timestamp)`) — 加速历史轨迹与时间区间回放查询
- `idx_violations_task` (`violations(task_id, acknowledged)`)
- `idx_zones_task` (`zones(task_id)`)

---

## 6. 后端 HTTP 与 WebSocket 路由清单

全部路由定义于 `backend/server.py`（共 26 个接口，默认端口 `8000`）：

### 健康检查与任务管理 / 视频推流

| 方法 | 路由 | 功能说明 | 关键参数 / 响应要点 |
|---|---|---|---|
| `GET` | `/api/health` | 服务健康检查 | 返回服务状态、设备/数据库就绪信息 |
| `GET` | `/api/tasks` | 查询全部任务列表 | 返回 `List[TaskRead]`（按 `id DESC` 排序） |
| `POST` | `/api/tasks` | 新建跟踪任务 | 请求体 `TaskCreate`；返回 `201 TaskRead`（初始 `idle`） |
| `GET` | `/api/tasks/{task_id}` | 获取单个任务详情 | 返回 `TaskRead` 或 `404` |
| `PUT` | `/api/tasks/{task_id}` | 更新任务与运行参数 | 请求体 `TaskUpdate`；若传入 `config` 且任务正在运行则自动平滑重启流 |
| `DELETE` | `/api/tasks/{task_id}` | 删除任务及缓存 | 停止流水线、级联删库并清理 `data/replay_cache/task_{id}` |
| `POST` | `/api/tasks/{task_id}/start` | 启动流推理任务 | 校验本地源/权重，失败设状态为 `error` 并返回 `400` |
| `POST` | `/api/tasks/{task_id}/stop` | 停止流推理任务 | 停止线程、断开订阅、清空实时画面缓存并返回 `stopped` |
| `GET` | `/api/tasks/{task_id}/video_feed` | MJPEG 实时视频底图流 | 返回 `multipart/x-mixed-replace; boundary=frame` 长连接 |
| `GET` | `/api/tasks/{task_id}/frame/{frame_id}` | 历史单帧回放图像 | 返回 `image/jpeg`，并在响应头附带原始分辨率 `X-Frame-Width`、`X-Frame-Height` |

### ROI 区域、违章报警与轨迹查询

| 方法 | 路由 | 功能说明 | 关键参数 / 响应要点 |
|---|---|---|---|
| `GET` | `/api/zones` | 查询 ROI 电子围栏列表 | 可选 Query：`task_id` |
| `POST` | `/api/zones` | 新建 ROI 区域/车道规则 | 请求体 `ZoneCreate`（多边形顶点、`zone_type` 等），返回 `201 ZoneRead` |
| `GET` | `/api/zones/{zone_id}` | 查询单个 ROI 区域 | 返回 `ZoneRead` 或 `404` |
| `DELETE` | `/api/zones/{zone_id}` | 删除 ROI 区域 | 返回 `{"status": "deleted", "zone_id": ...}` |
| `GET` | `/api/violations` | 查询违章告警记录 | Query：`task_id`、`violation_type`、`acknowledged`、`limit` (1–1000) |
| `POST` | `/api/violations/{violation_id}/acknowledge` | 确认处理单条违章 | 将 `acknowledged` 置为 `true` 并返回 `ViolationEvent` |
| `DELETE` | `/api/violations/{violation_id}` | 删除违章记录 | 返回 `{"status": "deleted", "violation_id": ...}` |
| `GET` | `/api/tracks` | 历史轨迹聚合与采样点检索 | Query：必填 `task_id`，可选 `track_id`、`class_name`、`start_ts`、`end_ts`、`limit` (1–10000) |

### 视频上传、流预设、系统指标与评测

| 方法 | 路由 | 功能说明 | 关键参数 / 响应要点 |
|---|---|---|---|
| `GET` | `/api/local-assets` | 扫描可用本地资产 | 返回 `{videos, datasets, weights}`（含内置 `weights/` 与 `data/`） |
| `GET` | `/api/assets` | `/api/local-assets` 别名 | 同上 |
| `POST` | `/api/videos/upload` | 上传本地视频文件 | `multipart/form-data` 字段 `file`（支持 `.mp4/.avi/.mov/.mkv/.webm`），保存至 `data/uploads/` 并返回 `{path, filename, size_bytes}` |
| `GET` | `/api/stream_presets` | 获取常用采集流模板与本地资源 | 返回 `{presets, weights, videos, datasets}`，预设含 `camera://0`、`screen://0`、中心区域裁剪与 RTSP 模板 |
| `GET` | `/api/metrics` | 获取实时性能指标 | 可选 Query：`task_id`；返回 `SystemMetrics` |
| `GET` | `/api/benchmarks` | 读取本地压测基准清单 | 探测 `benchmarks/manifest.json`（若不存在返回 `{available: false}`） |
| `GET` | `/api/ablation` | 获取算法消融对比结果 | 优先加载 `benchmarks/experiments/` 实测 JSON，否则运行 `evaluation/ablation_experiments.py` 合成套件 |
| `WS` | `/ws/stream/{task_id}` | 实时轨迹与性能指标推送 | 推送 `TrackFrame` JSON（支持心跳文本 `"ping"` → `"pong"`） |

---

## 7. 前端架构、视图与状态管理

### 7.1 视图路由与代理架构

前端采用单页应用架构（无独立 Vue Router），通过 `App.vue` 顶部导航切换 `motStore.activeTab` 控制 4 个核心工作视图；开发模式下 `vite.config.ts` 将所有 `/api` 与 `/ws` 请求反向代理至 `http://127.0.0.1:8000` / `ws://127.0.0.1:8000`。

| 页签状态 (`activeTab`) | 对应组件 | 核心交互与能力 |
|---|---|---|
| `monitoring`（实时监控与画布） | `LiveMonitoringView.vue` + `LatencyChart.vue` | 顶部任务启停栏与**快速接入新视频流**面板（上传 MP4 / 摄像头 / 桌面采集 / 窗口裁剪 / RTSP）、`1x1` / `2x1` / `2x2` 分屏、MJPEG 底图与 Canvas 矢量框/ID/速度/轨迹叠加、60 帧分阶段耗时 SVG 折线图、GMC 与推理参数抽屉 |
| `tracks`（轨迹检索与回放） | `TrackRetrievalView.vue` | 左侧任务/Track ID/类别过滤与轨迹摘要卡片；右侧等比回放舞台（依据 `X-Frame-Width`/`X-Frame-Height` 锁定长宽比，底层 `<img>` 请求 `/api/tasks/{id}/frame/{fid}` 与透明 `<canvas>` 逐帧同步绘制轨迹与速度标注） |
| `violations`（违章预警与任务） | `TaskViolationView.vue` | 多源接入类型创建任务弹窗（本地上传 MP4 / 本机摄像头 / PC 桌面屏幕采集 / RTSP 网络流 / 本地路径，支持开启 GMC）、ROI 围栏增删及违章报警确认列表 |
| `ablation`（算法消融评测） | `AblationMetricsView.vue` | ByteTrack 各增量模块（`Official Baseline` → `Full Enhanced`）MOTA / IDF1 / IDSW / Track FPS / P95 对比表格与系统基准展示 |

### 7.2 Pinia 状态管理 (`frontend/src/stores/motStore.ts`)

`useMotStore` 采用以 `taskId` 为主键的**单一真值映射表**管理实时连接与画布数据，彻底消除单路视图与多分屏视图的状态冲突：

- **核心 Map 状态**：
  - `framesByTask: Record<number, TrackFrame>` — 各任务最新接收到的 `TrackFrame`（含指标与违规列表）；
  - `zonesByTask: Record<number, Zone[]>` — 各任务 ROI 区域缓存；
  - `wsConnectedByTask: Record<number, boolean>` 与 `_wsByTask: Record<number, WebSocket>` — 统一的 WebSocket 实例与在线状态追踪；
  - `singleStreamExclusive: boolean`（默认 `true`）— 启动新任务时自动停止其他运行中任务。
- **兼容性计算属性（Getters）**：通过 `activeTaskId` 自动派生 `currentFrame`、`wsConnected`、`zones`，并向上暴露 `multiFrames`、`multiZones`、`multiWsStatus`，确保任务 `stopTask` 或 `setActiveTask` 切换时立即释放旧连接并清空旧帧，不会出现“画面停止但推理框残留”的现象。

---

## 8. 快速开始与测试验证

### 8.1 安装后端依赖

建议使用 Python 3.10+ 虚拟环境（如需 CUDA / TensorRT 加速，请安装对应 CUDA 版本的 PyTorch 与 TensorRT）：

```bash
pip install -r requirements.txt
```

### 8.2 启动后端与前端

```bash
# 终端 1：启动 FastAPI 后端（默认监听 127.0.0.1:8000）
uvicorn backend.server:app --host 127.0.0.1 --port 8000
```

```bash
# 终端 2：安装前端依赖并启动 Vite 开发服务器（监听 127.0.0.1:5173）
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器访问 `http://localhost:5173/` 即可使用。仓库已内置 `weights/yolov8n.pt` 与 `weights/yolov8n.onnx`；若本地没有预置公开数据集，可在界面顶部直接**上传 `.mp4` 视频**或一键启动 **`camera://0`（摄像头）** / **`screen://0`（PC 桌面采集）** 进行实时跟踪演示。

### 8.3 常用视频源 URI 参考

| 场景 | `video_source` 示例 | 推荐 GMC 模式 |
|---|---|---|
| 离线合成演示场景 | `demo` | 关闭 |
| 本地 / 网页上传视频文件 | `data/videos/sample.mp4` 或 `data/uploads/xxx.mp4` | 固定机位关闭 / 手持开启 `sparse_optflow` |
| 本机 USB / 笔记本摄像头 | `camera://0` | 固定关闭 / 手持开启 `sparse_optflow` |
| PC 全屏 / 游戏中心窗口实时采集 | `screen://0` 或 `screen://0?x=320&y=180&w=1280&h=720` | 开启 `phase` 或 `hybrid`（抗高速转视角） |
| 无人机航拍图像序列目录 | `data/datasets/VisDrone/.../uav0000137_00458_v` | 开启 `sparse_optflow` 或 `hybrid` |
| RTSP 网络监控流 | `rtsp://user:pass@192.168.1.64:554/stream1` | 固定球机关闭 / 移动云台开启 |

### 8.4 自动化测试与构建检查

```bash
# 运行全部后端、流水线与核心算法单元/集成测试
pytest -q

# 前端 TypeScript 严格类型检查与生产构建
cd frontend
npx vue-tsc --noEmit
npm run build
```
