# Good-Badminton AI Hawkeye Native Integration

This folder integrates the real Good-Badminton AI badminton analysis pipeline into the `badminton-rules` website with a native API and native page UI.

Integration mode: **native page + Flask API + pure Python Good-Badminton module calls**.

- Website: `http://127.0.0.1:8000/index.html`
- Native AI API: `http://127.0.0.1:5050/api/*`
- Page: click `AI视频分析` in the website navigation.
- No iframe, no page jump, no Good-Badminton upstream Web UI dependency.

> License note: `qwpyyx/Good-Badminton` is a derivative of `yo-WASSUP/Good-Badminton`. The fork states that its added/modified files are CC BY-NC 4.0, so this integration is for personal/research/non-commercial use unless you obtain permission.

## Quick start on Windows PowerShell

```powershell
cd "C:\Users\13126\badminton-rules"
python ai-hawkeye\setup_good_badminton.py --download-weights
python ai-hawkeye\run_site_with_hawkeye.py
```

Open:

```text
http://127.0.0.1:8000/index.html
```

Then click `AI视频分析` and upload a badminton video directly on the page.

## What setup does

1. Clones or updates `https://github.com/qwpyyx/Good-Badminton` into:

```text
ai-hawkeye/Good-Badminton
```

2. Creates Good-Badminton virtualenv:

```text
ai-hawkeye/Good-Badminton/.venv
```

3. Installs the integration requirements.
4. Downloads public release weights into:

```text
ai-hawkeye/Good-Badminton/weights
```

## Native API files

```text
ai-hawkeye/
├── native_api.py          # Flask API used by badminton-rules/index.html
├── analysis_runner.py     # Pure Python Good-Badminton analysis subprocess
├── good_badminton_ext/    # Extension modules synced into the Good-Badminton clone
├── run_good_badminton.py  # Starts native API by default; upstream WebUI is debug-only
└── run_site_with_hawkeye.py
```

`analysis_runner.py` imports Good-Badminton directly:

```python
from badminton_analysis.system import load_runtime_dependencies, BadmintonAnalysisSystem
from badminton_analysis.visualization.player_positions_zh import analyze_player_positions

load_runtime_dependencies()
system = BadmintonAnalysisSystem(
    video_path=str(video_path),
    template_path=str(template_path),
    output_dir=str(output_dir),
    show_display=False,
    ball_model_path=str(weights_dir / "yolo11s-ball.pt"),
    pose_family="yolo-pose",
    yolo_pose_model=str(weights_dir / "yolo11n-pose.pt"),
    language="zh",
)
system.process_video()

from badminton_analysis.analysis.shuttle_speed import calculate_shuttle_speeds
from badminton_analysis.visualization.shuttle_speed_overlay import annotate_video_with_shuttle_speeds

calculate_shuttle_speeds(
    detections_path=system.detections_path,
    metadata_path=system.metadata_path,
    output_jsonl_path=str(output_dir / "speeds.jsonl"),
    summary_path=str(output_dir / "speed_summary.json"),
    rally_segments_path=str(output_dir / "rally_segments.json"),
)
annotate_video_with_shuttle_speeds(
    input_video_path=system.output_video_path,
    output_video_path=str(output_dir / "detect_video_speed.mp4"),
    speeds_jsonl_path=str(output_dir / "speeds.jsonl"),
    summary_json_path=str(output_dir / "speed_summary.json"),
    fps=system.fps,
)

analyze_player_positions(system.detections_path, output_dir=str(output_dir / "position_visualizations"), fps=system.fps)
```

That is the Gradio/WebUI-free analysis path. The speed extensions are stored under `ai-hawkeye/good_badminton_ext/` and copied into the cloned Good-Badminton package by `setup_good_badminton.py` and by `analysis_runner.py` as a safety check.

## API design

### `GET /api/health`

Checks Good-Badminton checkout, virtualenv, model weights, and FFmpeg availability.

### `POST /api/upload`

`multipart/form-data` with `file=<video>`.

Returns:

```json
{
  "ok": true,
  "video_id": "match-a1b2c3d4",
  "filename": "match-a1b2c3d4.mp4",
  "original_filename": "match.mp4",
  "size_mb": 58.2,
  "next": "detect_court"
}
```

The browser shows real upload progress via `XMLHttpRequest.upload.onprogress`.

### `POST /api/videos/<video_id>/court/detect`

Calls Good-Badminton's actual court detection helpers:

- `court_detect.auto_extract_template(video_path)`
- `court_detect.detect_court(template_path, save_dir)`

Returns court corners, ROI, preview image URL, and `manual_required` if auto detection fails.

### `POST /api/videos/<video_id>/court/annotate`

Saves manual court corners when auto detection is not reliable:

```json
{
  "corners": [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
}
```

Corner order: top-left, top-right, bottom-right, bottom-left.

### `POST /api/analysis`

Starts long-running analysis asynchronously and returns immediately:

```json
{"ok": true, "job_id": "match-a1b2c3d4"}
```

The API starts `analysis_runner.py` as a subprocess inside Good-Badminton's virtualenv.

### `GET /api/jobs/<job_id>/events`

Server-Sent Events stream for analysis progress. The frontend uses `EventSource` and falls back to polling if SSE disconnects.

Event names:

```text
queued
running
progress
log
completed
error
```

### `GET /api/jobs/<job_id>`

Polling fallback for current job status.

### `GET /api/jobs/<job_id>/result`

Returns result file URLs.

### `GET /api/jobs/<job_id>/heatmap-data`

Parses `detections.jsonl` and returns true court-coordinate samples for Canvas rendering:

```json
{
  "court": {"width": 6.1, "length": 13.4, "unit": "m"},
  "samples": [
    {"player": "upper", "frame": 123, "time_sec": 4.1, "x": 2.8, "y": 4.9, "speed": 1.2},
    {"player": "lower", "frame": 123, "time_sec": 4.1, "x": 3.4, "y": 9.8, "speed": 1.4}
  ],
  "trajectories": {
    "upper": [{"x": 2.8, "y": 4.9, "frame": 123}],
    "lower": [{"x": 3.4, "y": 9.8, "frame": 123}]
  },
  "stats": {
    "upper": {"samples": 520, "distance_m": 88.4, "avg_speed_mps": 1.7, "max_speed_mps": 5.1},
    "lower": {"samples": 544, "distance_m": 94.2, "avg_speed_mps": 1.8, "max_speed_mps": 5.5}
  }
}
```

### `GET /api/jobs/<job_id>/speed-data`

Parses `speeds.jsonl` and `speed_summary.json` for frontend speed widgets:

```json
{
  "ok": true,
  "note": "平面投影速度（非真实3D速度）",
  "frames": [
    {"frame": 10, "time_sec": 0.33, "speed_kmh": 185.4, "level": "yellow", "image": [812, 344], "court": [2.8, 5.1]}
  ],
  "hits": [
    {"frame": 123, "time_sec": 4.1, "speed_kmh": 285.4, "type": "smash", "label": "杀球", "player": "lower"}
  ],
  "histogram": {"labels": ["0-50", "50-100"], "counts": [12, 34]},
  "players": {
    "upper": {"max_speed_kmh": 211.2, "avg_hit_speed_kmh": 132.3, "hit_count": 19},
    "lower": {"max_speed_kmh": 285.4, "avg_hit_speed_kmh": 151.8, "hit_count": 23}
  }
}
```

## Shuttle speed output format

`outputs/<video_id>/speeds.jsonl` is frame-aligned. Each line:

```json
{
  "schema_version": "1.0",
  "frame": 123,
  "time_sec": 4.1,
  "shuttlecock": {
    "image": [812.0, 344.0],
    "court": [2.83, 5.21],
    "interpolated": false,
    "valid": true
  },
  "speed": {
    "mps": 43.2,
    "kmh": 155.5,
    "smoothed_kmh": 149.8,
    "outlier": false,
    "level": "yellow"
  },
  "event": {
    "hit": true,
    "type": "drive",
    "label": "平抽",
    "player": "upper",
    "rally_id": 2,
    "speed_kmh": 172.4
  }
}
```

`outputs/<video_id>/speed_summary.json` contains match/rally/player summaries and the note that speed is a 2D court-plane projection, not true 3D speed.

## Data flow

```text
Raw uploaded video
  ↓
ai-hawkeye/Good-Badminton/videos/<video_id>.<ext>
  ↓
Template frame extraction + court corner detection/manual annotation
  ↓
Good-Badminton CourtMapper: image pixels → court meters (6.1m × 13.4m)
  ↓
BadmintonAnalysisSystem: pose detection + shuttlecock detection + player tracking
  ↓
PlayerTracker writes outputs/<video_id>/detections.jsonl
  ↓
shuttle_speed.py maps shuttlecock.image through CourtMapper and writes speeds.jsonl + speed_summary.json
  ↓
shuttle_speed_overlay.py renders speed panel, hit labels, rally speed stats, and bottom speed timeline onto detect_<video>.mp4
  ↓
native_api.py parses players.upper/lower.court for heatmaps and speeds.jsonl for speed widgets
  ↓
index.html Canvas draws heatmap/trajectory, speed gauge, hit list, histogram, and speed comparison
```

## Court coordinate mapping

Good-Badminton maps court corners to a meter-based rectangle:

```text
[0, 0]       top-left
[6.1, 0]     top-right
[6.1, 13.4]  bottom-right
[0, 13.4]    bottom-left
```

Frontend Canvas mapping:

```js
canvasX = courtLeft + (xMeters / 6.1) * courtPixelWidth;
canvasY = courtTop  + (yMeters / 13.4) * courtPixelHeight;
```

The heatmap is therefore drawn from real court coordinates, not from image pixels or a static PNG.

## Model weights

Release source:

```text
https://github.com/yo-WASSUP/Good-Badminton/releases/tag/v0.1.0
```

Files:

```text
weights/yolo11s-ball.pt
weights/yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx
weights/yolo11n-pose.pt
weights/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.onnx
weights/rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.onnx
```

## FFmpeg

Good-Badminton uses FFmpeg for audio/video post-processing and browser-compatible MP4 output.

Windows options:

```powershell
winget install Gyan.FFmpeg
```

or:

```powershell
choco install ffmpeg
```

Verify:

```powershell
ffmpeg -version
```

## GPU / CPU

The provided requirements default to CPU PyTorch for maximum install compatibility. CPU works but is slow.

For NVIDIA GPU, install a CUDA build of PyTorch inside Good-Badminton's venv, for example CUDA 12.1:

```powershell
cd "C:\Users\13126\badminton-rules\ai-hawkeye\Good-Badminton"
.\.venv\Scripts\python -m pip uninstall -y torch torchvision
.\.venv\Scripts\python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
```

Recommended GPU: 6GB+ VRAM. CPU is usable but can take many minutes for short 4K clips.

## Manual run commands

Native API only:

```powershell
python ai-hawkeye\run_good_badminton.py --host 127.0.0.1 --port 5050
```

Original Good-Badminton Web UI for debugging only:

```powershell
python ai-hawkeye\run_good_badminton.py --upstream-webui --host 127.0.0.1 --port 5050
```

Website only:

```powershell
python -m http.server 8000
```

## Production deployment notes

GitHub Pages cannot run Python. For public deployment, host the static site and the native API on a server/VPS/GPU host.

Nginx should reverse proxy `/api/` to `127.0.0.1:5050`, allow large uploads, and disable buffering for SSE:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:5050/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    client_max_body_size 1024m;
}
```

## Verification checklist

- [ ] `http://127.0.0.1:5050/api/health` returns JSON and `ready: true`.
- [ ] `AI视频分析` page has no iframe.
- [ ] Upload progress reaches 100%.
- [ ] Automatic court detection returns a preview or manual annotation canvas appears.
- [ ] Manual annotation saves `court_annotations.txt` when needed.
- [ ] Analysis progress updates through SSE or polling.
- [ ] Canvas heatmap and trajectory render from `/api/jobs/<job_id>/heatmap-data`.
- [ ] Speed gauge, hit list, histogram, and player speed comparison render from `/api/jobs/<job_id>/speed-data`.
- [ ] Output video contains shuttle speed panel, hit labels, rally stats, and bottom speed timeline.
- [ ] Good-Badminton output video and original PNG visualizations are available.
- [ ] `outputs/<video_id>/detections.jsonl` exists.
- [ ] `outputs/<video_id>/speeds.jsonl` and `outputs/<video_id>/speed_summary.json` exist.

## Generated directories ignored by git

```text
ai-hawkeye/Good-Badminton/
ai-hawkeye/downloads/
ai-hawkeye/.venv/
ai-hawkeye/.yolo-config/
```
