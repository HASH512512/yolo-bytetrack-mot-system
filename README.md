# yolo-bytetrack-mot-system

基于 YOLO 检测器与改进 ByteTrack 的可扩展多目标跟踪（MOT）工程原型系统，采用 Python / FastAPI 后端与 Vue 3 / TypeScript 前端分离架构，支持本地视频/图像序列实时推理、WebSocket 轨迹推送、MJPEG 视频流预览、历史轨迹回放及 ROI 规则告警。

## 目录结构

```text
yolo-bytetrack-mot-system/
├── core/          # 跟踪与检测核心模块（ByteTrack、NSA Kalman、DIoU/VDC/ORU、Lazy HSV、YOLO/ONNX/TensorRT 适配器）
├── pipeline/      # 流式处理调度层（环形缓冲读取、自适应关键帧跳帧、微批调度、多路协调与规则引擎）
├── backend/       # FastAPI 服务层（任务与区域 CRUD、SQLite WAL 存储、MJPEG 与 WebSocket 推流）
├── frontend/      # Vue 3 + Vite + Pinia + Tailwind 前端工作台（实时画布、耗时监控、轨迹回放、违章管理、消融对比）
├── evaluation/    # 算法消融对比基线与合成诊断评测脚本
├── tests/         # 核心算法、流水线调度与后端接口单元测试
└── requirements.txt
```

## 主要特性

- **多后端检测适配**：统一 `DetectorAdapter` 接口，支持 PyTorch YOLO（`.pt`）、ONNX Runtime（`.onnx`）、TensorRT（`.engine` + CUDA 零拷贝预处理）及离线 `MockDetectorAdapter`。
- **改进 ByteTrack 跟踪器**：保留标准两阶段关联与恢复池基线，按需启用类别感知代价（Class-Aware）、NSA 自适应噪声卡尔曼滤波、DIoU+VDC+ORU 关联与按需局部 HSV 外观匹配。
- **异步流水线与原速播放**：解码、批量推理、跟踪与 JPEG/DB 落库解耦；支持固定源帧率播放与自适应关键帧跳帧切换，提供单帧图像回放接口。
- **可视化前端工作台**：包含实时监控画布、端到端分阶段耗时折线图、历史轨迹逐帧回放（叠加真实视频底图）、ROI 规则开关与任务管理。

## 快速开始

### 1. 安装后端依赖

建议使用 Python 3.10+ 虚拟环境（如需 CUDA / TensorRT 加速，请先安装对应 CUDA 版本的 PyTorch 与 TensorRT）：

```bash
pip install -r requirements.txt
```

### 2. 准备本地视频与权重文件

本仓库不包含数据集与权重二进制文件。请按需将本地文件放在项目根目录的 `data/` 下（不会随仓库提交）：

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

浏览器访问 `http://localhost:5173/`（Vite 已配置将 `/api` 与 `/ws` 代理到 `127.0.0.1:8000`）。

## 运行测试

```bash
pytest -q
```
