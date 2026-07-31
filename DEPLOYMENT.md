# 部署说明

本项目由两部分组成：

- 前端：`index.html` 静态站点，可部署到 GitHub Pages、Nginx、任意静态托管。
- AI 后端：`ai-hawkeye/native_api.py` Flask API，会调用 Good-Badminton、OpenCV、PyTorch、FFmpeg，不能在 GitHub Pages 上运行。

> 球速说明：当前球速是基于 `detections.jsonl` + Good-Badminton `CourtMapper` 的 2D 球场平面投影速度，不是单目视频无法恢复的真实 3D 雷达球速。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `AI_HAWKEYE_ALLOWED_ORIGINS` | 生产建议填写 | 空 | 逗号分隔 CORS 来源，例如 `https://user.github.io,https://example.com`。为空时兼容本地开发，按请求 Origin 或 `*` 返回。 |
| `AI_HAWKEYE_MAX_UPLOAD_MB` | 否 | `1024` | 上传视频大小上限（MB）。 |
| `AI_HAWKEYE_GOOD_ROOT` | 否 | `ai-hawkeye/Good-Badminton` | Good-Badminton 运行目录。Docker 中设为 `/app/ai-hawkeye/Good-Badminton`。 |
| `PORT` | 云平台提供 | `5050`（脚本参数） | Render/Railway 等平台注入的监听端口。 |

## 方案 A：GitHub Pages + Render 后端

### 1. 推送前端到 GitHub

1. 确认仓库包含 `index.html` 和 `ai-hawkeye/` 集成代码。
2. 在 GitHub 仓库 Settings → Pages 中选择分支部署静态站点。
3. 记录 Pages URL，例如：

```text
https://<user>.github.io/badminton-rules/
```

### 2. Render 创建后端服务

仓库根目录已有 `render.yaml`，Render 会执行：

```bash
python -m pip install --upgrade pip
python ai-hawkeye/setup_good_badminton.py --download-weights
python ai-hawkeye/run_good_badminton.py --host 0.0.0.0 --port $PORT
```

在 Render 环境变量中设置：

```text
AI_HAWKEYE_ALLOWED_ORIGINS=https://<user>.github.io
AI_HAWKEYE_MAX_UPLOAD_MB=1024
AI_HAWKEYE_GOOD_ROOT=ai-hawkeye/Good-Badminton
```

部署完成后访问：

```text
https://<render-service>.onrender.com/api/health
```

确认返回：

```json
{"ok": true, "ready": true}
```

### 3. 让 GitHub Pages 前端调用 Render 后端

打开前端时在 URL 后加 `api` 参数：

```text
https://<user>.github.io/badminton-rules/?api=https://<render-service>.onrender.com
```

页面会把 API 地址写入 `localStorage.AI_HAWKEYE_API_BASE`，以后可直接打开原页面。

也可在浏览器控制台手动设置：

```js
localStorage.setItem('AI_HAWKEYE_API_BASE', 'https://<render-service>.onrender.com')
```

## 方案 B：Railway / Nixpacks 后端

仓库包含：

- `nixpacks.toml`
- `railway.json`

Railway 会安装 Python、FFmpeg、Git，然后运行：

```bash
python ai-hawkeye/setup_good_badminton.py --download-weights
python ai-hawkeye/run_good_badminton.py --host 0.0.0.0 --port $PORT
```

环境变量同 Render：

```text
AI_HAWKEYE_ALLOWED_ORIGINS=https://<your-frontend-domain>
AI_HAWKEYE_MAX_UPLOAD_MB=1024
```

## 方案 C：VPS Docker Compose 同域部署

适合有域名和 VPS 的部署。Nginx 同时服务静态站点并反代 `/api/` 到后端，前端无需配置远端 API base。

### 1. 准备 `.env`

```bash
cp .env.example .env
```

示例：

```text
HTTP_PORT=8080
AI_HAWKEYE_ALLOWED_ORIGINS=https://badminton.example.com
AI_HAWKEYE_MAX_UPLOAD_MB=1024
```

### 2. 启动

```bash
docker compose up --build -d
```

访问：

```text
http://<server-ip>:8080/index.html
http://<server-ip>:8080/api/health
```

### 3. 绑定域名

将域名解析到 VPS，并把外层 Nginx/Caddy/云负载均衡转发到 `127.0.0.1:8080`。

如果直接使用本仓库 `deploy/nginx.conf`，核心配置是：

```nginx
location /api/ {
    proxy_pass http://ai-backend:5050/api/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    client_max_body_size 1024m;
}
```

## 本地验证

```powershell
cd "C:\Users\13126\badminton-rules"
python ai-hawkeye\setup_good_badminton.py --download-weights
python ai-hawkeye\run_site_with_hawkeye.py
```

打开：

```text
http://127.0.0.1:8000/index.html
```

## 上线验证清单

1. `GET /api/health`
   - `ok: true`
   - `ready: true`
   - `ffmpeg` 不为空
   - 必需权重 `yolo11s-ball.pt`、`yolo11n-pose.pt` 为 true
2. 前端 AI 页面后端状态显示“后端已就绪”。
3. 上传视频进度可到 100%。
4. 自动球场检测返回预览；失败时可手动点击四角保存。
5. 启动 AI 分析后 SSE 或轮询进度持续更新。
6. 分析完成后输出存在：
   - `detections.jsonl`
   - `metadata.json`
   - `rally_segments.json`
   - `speeds.jsonl`
   - `speed_summary.json`
   - `position_visualizations/heatmaps/match_heatmap.png`
   - `position_visualizations/scatter_plots/match_scatter.png`
   - `detect_<video_id>.mp4`
7. 浏览器 `<video>` 能播放输出 MP4，若失败检查：
   - `/api/output/...mp4` 是否返回 `video/mp4`
   - 是否有 `Accept-Ranges: bytes`
   - `/api/health` 中 `ffmpeg` 是否存在
   - 后端日志是否出现 H.264 转码失败
8. Canvas 跑动热力图和轨迹图显示。
9. 球速卡片显示最高/平均球速、击球事件列表、速度直方图、上/下半场对比。
10. 输出视频画面含球速面板、击球标签、底部速度时间线。

## 生产注意事项

- AI 分析耗时较长，CPU 部署可用但慢；生产建议 GPU 或至少高性能 CPU。
- 上传和输出视频可能很大，云平台磁盘会增长，需要定期清理 `ai-hawkeye/Good-Badminton/videos` 和 `outputs`。
- 当前任务状态保存在内存，服务重启会丢失运行中状态；已有输出可通过结果文件恢复为 completed。
- Good-Badminton fork 许可包含 CC BY-NC 4.0 声明，商业用途请确认授权。
