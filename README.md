# yolo-bytetrack-mot-system

基于 YOLO 与改进 ByteTrack 的可扩展多目标跟踪系统，采用 Python + FastAPI 后端与 Vue 3 前端分离架构，支持本地视频上传与多路实时流推理、移动相机全局运动补偿（GMC）、WebSocket 轨迹推送、MJPEG 视频流预览以及历史轨迹与视频帧精准回放。

## 主要特性

- **开箱即用内置权重**：仓库内置 `weights/yolov8n.pt` 与 `weights/yolov8n.onnx` 轻量级权重，克隆后即可直接运行本地推理与界面演示；同时也支持扫描并加载 `data/weights/` 下的自定义 `.pt`、`.onnx` 与 `.engine` 模型权重。
- **多后端检测适配**：通过统一的 `DetectorAdapter` 接口解耦检测与跟踪，支持 Ultralytics YOLO（`.pt`）、ONNX Runtime（`.onnx`）、TensorRT（`.engine`）及离线 `MockDetectorAdapter`。
- **改进 ByteTrack 与移动相机补偿（GMC）**：保留标准两阶段关联与遮挡恢复池基线，按需开启类别感知代价（Class-Aware）、NSA 自适应噪声卡尔曼滤波、DIoU + 速度方向一致性（VDC）+ 观测重更新（ORU）与按需局部 HSV 外观匹配（默认关闭以保持高吞吐实时性）；同时支持全局运动补偿（`sparse_optflow` 稀疏光流 / `phase` 相位相关 / `hybrid` 混合模式）应对无人机航拍与快速晃动镜头。
- **多源视频流接入与异步流水线**：支持本地 `.mp4/.avi/.mov/.mkv/.webm` 视频上传（`POST /api/videos/upload`）与循环回放、本地图像序列目录、USB/内置摄像头（`camera://0`）、PC 全屏与中心区域实时采集（`screen://0`、`screen://0?x=320&y=180&w=1280&h=720`）以及 RTSP/RTMP/HTTP 网络流；解码、微批推理、跟踪与规则判定相互解耦，支持原速（`native`）与无上限压测（`uncapped`）播放节奏及自适应关键帧跳帧。
- **精准轨迹与单帧回放缓存**：轨迹落库时同步写入内存 LRU（上限 600 帧）与本地快照缓存（`data/replay_cache/`），即使任务已停止或来自摄像头/屏幕/上传视频流，仍可通过 `GET /api/tasks/{task_id}/frame/{frame_id}` 获取对应历史帧与真实分辨率完成叠加回放。
- **可视化前端工作台**：支持单路（`1x1`）/ 双路（`2x1`）/ 四路（`2x2`）监控视图、MJPEG 底图与矢量框动态对齐、端到端分阶段耗时折线图、GMC 位移监控、一键流接入控制面板及历史轨迹逐帧回放。

## 快速开始

### 1. 安装后端依赖

建议使用 Python 3.10+（如需 CUDA 或 TensorRT 加速，请先安装对应 CUDA 版本的 PyTorch 与 TensorRT）：

```bash
pip install -r requirements.txt
```

### 2. 权重与视频资源

- **内置权重**：仓库已自带 `weights/yolov8n.pt` 与 `weights/yolov8n.onnx`，启动后即可选择使用。
- **自定义资源**：可将额外权重放入 `data/weights/`，将本地测试视频或数据集放入 `data/videos/`、`data/datasets/`，或直接通过界面上传本地视频及一键接入摄像头/屏幕采集。

### 3. 启动后端服务

```bash
uvicorn backend.server:app --host 127.0.0.1 --port 8000
```

### 4. 启动前端开发服务

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

浏览器访问 `http://localhost:5173/` 即可使用。

## 核心接口概览

- `GET /api/tasks` / `POST /api/tasks` / `POST /api/tasks/{id}/start` / `POST /api/tasks/{id}/stop`：跟踪任务管理与启停控制
- `POST /api/videos/upload`：上传本地视频至 `data/uploads/` 并返回相对路径
- `GET /api/stream_presets` & `GET /api/assets`：获取摄像头/屏幕采集/RTSP 预设及内置权重与本地资产列表
- `GET /api/tasks/{id}/video_feed` & `WS /ws/stream/{id}`：实时 MJPEG 视频流与轨迹/性能指标 WebSocket 推送
- `GET /api/tracks` & `GET /api/tasks/{id}/frame/{frame_id}`：历史轨迹检索与高对齐单帧回放图像接口
