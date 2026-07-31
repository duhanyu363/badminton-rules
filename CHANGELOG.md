# CHANGELOG

## 2026-07-31

### 恢复上下文

- 找到实际网站项目：`C:\Users\13126\badminton-rules`。
- 确认另有独立 Python 原型目录：`D:\document\badminton-video-analysis-system`，但实际要上线的是 `badminton-rules` + `ai-hawkeye`。
- 读取并梳理了：
  - 网站入口 `index.html`、旧规则页 `Badminton rule.html`。
  - AI 后端 `ai-hawkeye/native_api.py`。
  - 分析子进程 `ai-hawkeye/analysis_runner.py`。
  - Good-Badminton 扩展 `ai-hawkeye/good_badminton_ext/`。
  - 依赖、Nginx、运行脚本和 Good-Badminton 关键分析文件。

### 当前已实现功能

- 原生 AI 视频分析页面：上传、球场检测/手动标注、异步分析、SSE/轮询进度、结果展示。
- Flask API：健康检查、上传、球场检测、手动标注、启动分析、任务状态、SSE、结果查询、热力图数据、球速数据、输出文件服务。
- Good-Badminton headless 调用：`analysis_runner.py` 直接调用 Good-Badminton Python 模块，不依赖 upstream Web UI。
- 热力图：
  - `/api/jobs/<job_id>/heatmap-data` 从 `detections.jsonl` 的 `players.upper/lower.court` 米制坐标生成 Canvas 数据。
  - 前端 Canvas 渲染跑动热力图和轨迹图。
  - Good-Badminton 继续输出原始 PNG 热力图和散点图。
- 球速检测：
  - `shuttle_speed.py` 基于 `detections.jsonl` + Good-Badminton `CourtMapper` 计算 2D 球场平面投影速度。
  - 输出 `speeds.jsonl` 和 `speed_summary.json`。
  - 前端展示最高/平均球速、击球事件列表、速度直方图、上/下半场推断对比。
- 视频播放修复：
  - `/api/output` 返回 HTTP URL、`video/mp4`、`Accept-Ranges`、inline 响应。
  - `analysis_runner.py` 使用 FFmpeg 转 H.264/yuv420p/baseline/faststart，避免浏览器显示 0:00。

### 本次修改

- `index.html`
  - 增加 AI API 地址解析：支持 `?api=https://backend.example.com`、`?ai_api=...`、`window.AI_HAWKEYE_API_BASE`、`localStorage.AI_HAWKEYE_API_BASE`、localhost 自动端口和同域 `/api`。
  - 为输出视频增加 `error` 事件日志，提示检查 `/api/output`、CORS/Range 和 H.264 转码。
  - 删除重复的 `renderAiLinks()` 定义。
- `ai-hawkeye/native_api.py`
  - 增加环境变量：`AI_HAWKEYE_ALLOWED_ORIGINS`、`AI_HAWKEYE_MAX_UPLOAD_MB`、`AI_HAWKEYE_GOOD_ROOT`。
  - CORS 支持生产白名单；未配置时保持本地开发兼容。
  - `/api/health` 增加 `imageio-ffmpeg` fallback 检测结果和上传限制/允许来源信息。
- `ai-hawkeye/good_badminton_ext/badminton_analysis/visualization/shuttle_speed_overlay.py`
  - 视频叠加层 writer 改为优先 `avc1`/`H264`，失败回退 `mp4v`。
- `ai-hawkeye/good_badminton_ext/badminton_analysis/analysis/shuttle_speed.py`
  - `speed_summary.json` 增加 `data_quality`：总帧、有效映射帧、有效比例、插值帧、离群帧、速度样本帧。
- 新增部署文件：
  - `.dockerignore`
  - `.env.example`
  - `Dockerfile`
  - `docker-compose.yml`
  - `deploy/nginx.conf`
  - `render.yaml`
  - `nixpacks.toml`
  - `railway.json`
  - `DEPLOYMENT.md`

### 部署说明

- GitHub Pages 只能部署静态前端，AI 后端必须部署到 Render/Railway/VPS/GPU 主机。
- GitHub Pages + Render/Railway：
  - 后端设置 `AI_HAWKEYE_ALLOWED_ORIGINS=https://<your-pages-domain>`。
  - 前端打开 `https://<pages-url>/?api=https://<backend-url>`。
- VPS Docker Compose：
  - `docker compose up --build -d`
  - Nginx 同域服务静态前端，并将 `/api/` 反代到后端。

### 上线验证重点

- `/api/health` 返回 `ready: true` 且 `ffmpeg` 不为空。
- 上传、球场检测/手动标注、AI 分析进度正常。
- 输出文件存在：`detections.jsonl`、`metadata.json`、`speeds.jsonl`、`speed_summary.json`、PNG 热力图/散点图、`detect_<video_id>.mp4`。
- 浏览器能播放输出视频。
- 输出视频含球速面板、击球标签和底部速度时间线。
- 前端跑动热力图、轨迹图、速度仪表盘、击球列表、速度直方图和上/下半场对比均显示。

### 已知限制

- 球速为 2D 球场平面投影速度，不是真实 3D 雷达球速。
- 击球事件识别是启发式规则。
- 任务状态仍为内存状态；服务重启会丢失运行中任务状态，但已有输出可恢复为 completed。
- CPU 可运行但较慢，生产建议 GPU 或高性能 CPU。
