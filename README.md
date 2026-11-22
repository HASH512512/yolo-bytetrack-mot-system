# yolo-bytetrack-mot-system

基于 YOLO 与 ByteTrack 的可扩展多目标跟踪工程原型 Demo ，采用 Python + FastAPI 后端与 Vue 3 前端分离架构，目前完成的功能包括本地视频实时推理、WebSocket 轨迹推送、视频流预览、历史轨迹回放

## 主要特性

- **多后端检测适配**：统一 `DetectorAdapter` 接口，支持 YOLO（`.pt`）、ONNX Runtime（`.onnx`）、TensorRT（`.engine` ）及离线 `MockDetectorAdapter`。
- **改进 ByteTrack 跟踪器**：~~保留标准两阶段关联与恢复池基线，按需启用类别感知代价（Class-Aware）、NSA 自适应噪声卡尔曼滤波、DIoU+VDC+ORU 关联与按需局部 HSV 外观匹配。~~ （性能未达预期正在重写）
- **异步流水线与~~原速播放~~**：解码、批量推理、跟踪等进程互相解耦；支持固定源帧率与自适应最大速度视频播放，与自适应关键帧/跳帧切换，~~提供单帧图像回放接口。~~（回放实现有问题也在重写）
- **可视化前端工作台**：包含实时监控画布、端到端分阶段耗时折线图、~~历史轨迹逐帧回放（叠加真实视频底图）、ROI 规则开关与任务管理。~~ （逻辑混乱，重写中）

## 快速开始

### 1. 安装后端依赖

建议使用 Python 3.10+ （如需 CUDA / TensorRT 加速，请先安装对应 CUDA 版本的 PyTorch 与 TensorRT）：

```bash
pip install -r requirements.txt
```

### 2. 准备本地视频与权重文件

本仓库中暂未包含数据集与权重二进制文件。请按需将本地文件放在项目根目录的 `data/` 下：

- 视频或图像序列：`data/videos/` 或 `data/datasets/`
- 模型权重：`data/weights/`（例如 `yolov8n.pt` / `yolov8s.engine`）

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

浏览器访问 `http://localhost:5173/`

