#!/usr/bin/env python3
"""Native API for badminton-rules AI Hawkeye integration.

The browser UI in ../index.html talks to this API directly. The API reuses the
real Good-Badminton Python modules for court detection and analysis, but it does
not serve or embed Good-Badminton's upstream Web UI.
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, Response, jsonify, make_response, request, send_file, stream_with_context
from werkzeug.utils import secure_filename

INTEGRATION_DIR = Path(__file__).resolve().parent
SITE_ROOT = INTEGRATION_DIR.parent


def _resolve_runtime_path(env_name: str, default: Path) -> Path:
    """Resolve runtime paths on local machines and Render.

    Relative env-var values are accepted in two common forms:
    - repo-root relative: ``ai-hawkeye/Good-Badminton``
    - integration-dir relative: ``Good-Badminton`` or ``weights``

    This avoids Render resolving ``ai-hawkeye/Good-Badminton`` as
    ``ai-hawkeye/ai-hawkeye/Good-Badminton``.
    """
    value = os.environ.get(env_name)
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()

    repo_relative = (SITE_ROOT / path).resolve()
    integration_relative = (INTEGRATION_DIR / path).resolve()

    if repo_relative.exists():
        return repo_relative
    if integration_relative.exists():
        return integration_relative
    if path.parts and path.parts[0] == "ai-hawkeye":
        return repo_relative
    return integration_relative


def _upload_limit_bytes(default_mb: int = 1024) -> int:
    value = os.environ.get("AI_HAWKEYE_MAX_UPLOAD_MB")
    if not value:
        return default_mb * 1024 * 1024
    try:
        mb = max(1, int(float(value)))
    except (TypeError, ValueError):
        mb = default_mb
    return mb * 1024 * 1024


GOOD_ROOT = _resolve_runtime_path("AI_HAWKEYE_GOOD_ROOT", INTEGRATION_DIR / "Good-Badminton")
VIDEOS_DIR = GOOD_ROOT / "videos"
OUTPUTS_DIR = GOOD_ROOT / "outputs"
TEMPLATES_DIR = GOOD_ROOT / "templates"
WEIGHTS_DIR = GOOD_ROOT / "weights"
EXTENSIONS_DIR = INTEGRATION_DIR / "good_badminton_ext"
RUNNER = INTEGRATION_DIR / "analysis_runner.py"

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_UPLOAD_BYTES = _upload_limit_bytes()
ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.environ.get("AI_HAWKEYE_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
COURT_WIDTH_M = 6.1
COURT_LENGTH_M = 13.4
REQUIRED_WEIGHTS = [
    "yolo11s-ball.pt",
    "yolo11n-pose.pt",
]
OPTIONAL_WEIGHTS = [
    "yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx",
    "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.onnx",
    "rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.onnx",
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

_startup_lock = threading.Lock()
_startup_complete = False


def ensure_runtime_opencv() -> None:
    try:
        import cv2

        print(f"[startup] cv2 imported successfully, version: {cv2.__version__}", flush=True)
        return
    except Exception as exc:
        print(f"[startup] OpenCV runtime import failed before install: {exc}", file=sys.stderr, flush=True)

    try:
        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "numpy==1.26.4",
            "opencv-python-headless==4.11.0.86",
        ])
        import cv2

        print(f"[startup] cv2 imported successfully after install, version: {cv2.__version__}", flush=True)
    except Exception as exc:
        print(f"[startup] OpenCV runtime install failed: {exc}", file=sys.stderr, flush=True)


def opencv_status() -> dict[str, Any]:
    try:
        import cv2

        return {"ok": True, "version": str(getattr(cv2, "__version__", "unknown")), "file": str(getattr(cv2, "__file__", "unknown"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "python": str(python_exe())}


def initialize_startup() -> None:
    global _startup_complete
    with _startup_lock:
        if _startup_complete:
            return
        ensure_runtime_dirs()
        ensure_runtime_opencv()
        sync_good_badminton_extensions()
        ensure_startup_weights()
        _startup_complete = True


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.RLock()


@app.after_request
def add_cors_headers(response):
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if ALLOWED_ORIGINS:
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", origin)
            response.headers["Vary"] = "Origin"
    else:
        response.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/<path:_path>", methods=["OPTIONS"])
def api_options(_path: str):
    return ("", 204)


def ensure_runtime_dirs() -> None:
    if not GOOD_ROOT.exists():
        return
    for directory in (VIDEOS_DIR, OUTPUTS_DIR, TEMPLATES_DIR, WEIGHTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def python_exe() -> Path:
    return Path(sys.executable).resolve()


def sync_good_badminton_extensions() -> None:
    if not EXTENSIONS_DIR.exists() or not GOOD_ROOT.exists():
        return
    for source in EXTENSIONS_DIR.rglob("*"):
        if source.is_dir() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        target = GOOD_ROOT / source.relative_to(EXTENSIONS_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def weight_file_ready(filename: str) -> bool:
    path = WEIGHTS_DIR / filename
    return path.exists() and path.stat().st_size > 0


def missing_required_weights() -> list[str]:
    return [filename for filename in REQUIRED_WEIGHTS if not weight_file_ready(filename)]


def runtime_ready() -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not GOOD_ROOT.exists():
        missing.append("Good-Badminton 仓库未安装")
    for filename in missing_required_weights():
        missing.append(f"缺少权重文件 {filename}")
    if not RUNNER.exists():
        missing.append("缺少 analysis_runner.py")
    return not missing, missing


def copy_default_weights(filenames: Iterable[str]) -> None:
    default_weights_dir = INTEGRATION_DIR / "Good-Badminton" / "weights"
    if default_weights_dir.resolve() == WEIGHTS_DIR.resolve() or not default_weights_dir.exists() or not GOOD_ROOT.exists():
        return
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        source = default_weights_dir / filename
        target = WEIGHTS_DIR / filename
        if not source.exists() or source.stat().st_size <= 0 or weight_file_ready(filename):
            continue
        shutil.copy2(source, target)
        print(f"[startup] copied weight {source} -> {target}", flush=True)


def download_missing_weights_directly(filenames: Iterable[str]) -> None:
    if not GOOD_ROOT.exists():
        print("[startup] Good-Badminton not found; cannot download weights directly", file=sys.stderr, flush=True)
        return
    try:
        from setup_good_badminton import WEIGHTS
        from urllib.request import urlretrieve
    except Exception as exc:
        print(f"[startup] cannot load weight download metadata: {exc}", file=sys.stderr, flush=True)
        return

    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        if weight_file_ready(filename):
            continue
        url = WEIGHTS.get(filename)
        if not url:
            print(f"[startup] no download URL configured for {filename}", file=sys.stderr, flush=True)
            continue
        target = WEIGHTS_DIR / filename
        temp_target = target.with_name(f".{target.name}.download")
        try:
            if temp_target.exists():
                temp_target.unlink()
            print(f"[startup] downloading weight {filename}", flush=True)
            urlretrieve(url, temp_target)
            if temp_target.stat().st_size <= 0:
                raise RuntimeError("downloaded file is empty")
            temp_target.replace(target)
            print(f"[startup] saved weight {target}", flush=True)
        except Exception as exc:
            if temp_target.exists():
                temp_target.unlink()
            print(f"[startup] failed to download {filename}: {exc}", file=sys.stderr, flush=True)


def ensure_startup_weights() -> None:
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    missing = missing_required_weights()
    if not missing:
        return

    print(f"[startup] missing required weights: {', '.join(missing)}", flush=True)
    download_script = INTEGRATION_DIR / "download_weights.py"
    if download_script.exists():
        try:
            subprocess.run([sys.executable, str(download_script)], cwd=str(INTEGRATION_DIR), check=True, text=True)
        except Exception as exc:
            print(f"[startup] download_weights.py failed: {exc}", file=sys.stderr, flush=True)

    missing = missing_required_weights()
    copy_default_weights(missing)
    missing = missing_required_weights()
    if missing:
        download_missing_weights_directly(missing)


def resolve_ffmpeg() -> str | None:
    """Find FFmpeg from PATH, local runtime folders, or imageio-ffmpeg."""
    candidates: list[str | Path | None] = [
        shutil.which("ffmpeg"),
        GOOD_ROOT / ".local" / "bin" / "ffmpeg.exe",
        GOOD_ROOT / ".local" / "bin" / "ffmpeg",
        Path.home() / ".local" / "bin" / "ffmpeg.exe",
        Path.home() / ".local" / "bin" / "ffmpeg",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return str(exe)
    except Exception:
        pass
    return None


def sanitize_video_id(filename: str) -> str:
    stem = Path(filename).stem or "video"
    stem = secure_filename(stem) or "video"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip(".-_") or "video"
    return f"{stem[:42]}-{uuid.uuid4().hex[:8]}"


def validate_video_id(video_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", video_id or ""):
        raise ValueError("video_id 非法")
    return video_id


def video_path_for(video_id: str) -> Path | None:
    validate_video_id(video_id)
    for ext in ALLOWED_EXTENSIONS:
        candidate = VIDEOS_DIR / f"{video_id}{ext}"
        if candidate.exists():
            return candidate
    matches = sorted(VIDEOS_DIR.glob(f"{video_id}.*"))
    for match in matches:
        if match.suffix.lower() in ALLOWED_EXTENSIONS:
            return match
    return None


def output_dir_for(video_id: str) -> Path:
    validate_video_id(video_id)
    return OUTPUTS_DIR / video_id


def template_path_for(video_id: str) -> Path | None:
    validate_video_id(video_id)
    primary = TEMPLATES_DIR / f"_auto_{video_id}.png"
    if primary.exists():
        return primary
    matches = sorted(TEMPLATES_DIR.glob(f"_auto_{video_id}.*"))
    return matches[0] if matches else None


def safe_output_path(video_id: str, subpath: str) -> Path:
    base = output_dir_for(video_id).resolve()
    candidate = (base / subpath).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValueError("非法输出路径")
    return candidate


def json_response_error(message: str, status: int = 400, **extra: Any):
    return jsonify({"ok": False, "error": message, **extra}), status


def import_good_modules() -> None:
    sync_good_badminton_extensions()
    good_root = str(GOOD_ROOT)
    if good_root not in sys.path:
        sys.path.insert(0, good_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_parts = [part for part in existing_pythonpath.split(os.pathsep) if part]
    if good_root not in pythonpath_parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([good_root, *pythonpath_parts])
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    if GOOD_ROOT.exists():
        os.chdir(str(GOOD_ROOT))


def append_job_event(job: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    now = time.time()
    normalized = {
        "event": event,
        "time": now,
        "status": payload.get("status", job.get("status", event)),
        **payload,
    }
    with _jobs_lock:
        job["events"].append(normalized)
        if len(job["events"]) > 600:
            job["events"] = job["events"][-600:]
        job["event_queue"].put(normalized)


def update_job(job_id: str, event: str, **payload: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if "status" in payload:
            job["status"] = payload["status"]
        elif event in {"running", "progress", "completed", "error"}:
            job["status"] = event if event != "progress" else "running"
        if "progress" in payload:
            job["progress"] = int(payload["progress"])
        if "message" in payload:
            job["message"] = str(payload["message"])
        if "result" in payload:
            job["result"] = payload["result"]
        if "error" in payload:
            job["error"] = payload["error"]
        append_job_event(job, event, payload)


def output_video_for(video_id: str) -> Path | None:
    """Return the best browser-facing output video for a job."""
    out_dir = output_dir_for(video_id)
    preferred = [
        out_dir / f"detect_{video_id}_browser.mp4",
        out_dir / f"detect_{video_id}.mp4",
    ]
    for candidate in preferred:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate

    browser_variants = sorted(out_dir.glob("detect_*_browser.mp4"))
    if browser_variants:
        return browser_variants[0]
    candidates = sorted(out_dir.glob("detect_*.mp4"))
    return candidates[0] if candidates else None


def public_result(video_id: str) -> dict[str, Any]:
    out_dir = output_dir_for(video_id)
    output_video_path = output_video_for(video_id)
    output_video = f"/api/output/{video_id}/{output_video_path.name}" if output_video_path else None

    pos_dir = out_dir / "position_visualizations"
    heatmap_png = pos_dir / "heatmaps" / "match_heatmap.png"
    scatter_png = pos_dir / "scatter_plots" / "match_scatter.png"
    detections = out_dir / "detections.jsonl"
    speeds = out_dir / "speeds.jsonl"
    speed_summary = out_dir / "speed_summary.json"
    metadata = out_dir / "metadata.json"
    rally_file = out_dir / "rally_segments.json"
    rally_count = 0
    if rally_file.exists():
        try:
            rally_count = len(json.loads(rally_file.read_text(encoding="utf-8")).get("rallies", []))
        except Exception:
            rally_count = 0

    return {
        "video_id": video_id,
        "output_video": output_video,
        "detections": f"/api/output/{video_id}/detections.jsonl" if detections.exists() else None,
        "metadata": f"/api/output/{video_id}/metadata.json" if metadata.exists() else None,
        "rallies": f"/api/output/{video_id}/rally_segments.json" if rally_file.exists() else None,
        "rally_count": rally_count,
        "heatmap_png": f"/api/output/{video_id}/position_visualizations/heatmaps/match_heatmap.png" if heatmap_png.exists() else None,
        "scatter_png": f"/api/output/{video_id}/position_visualizations/scatter_plots/match_scatter.png" if scatter_png.exists() else None,
        "heatmap_data": f"/api/jobs/{video_id}/heatmap-data" if detections.exists() else None,
        "speeds": f"/api/output/{video_id}/speeds.jsonl" if speeds.exists() else None,
        "speed_summary": f"/api/output/{video_id}/speed_summary.json" if speed_summary.exists() else None,
        "speed_data": f"/api/jobs/{video_id}/speed-data" if speeds.exists() and speed_summary.exists() else None,
    }


@app.route("/api/health")
def api_health():
    ready, missing = runtime_ready()
    weights = {
        filename: weight_file_ready(filename)
        for filename in REQUIRED_WEIGHTS + OPTIONAL_WEIGHTS
    }
    return jsonify(
        {
            "ok": True,
            "ready": ready,
            "missing": missing,
            "site_root": str(SITE_ROOT),
            "integration_dir": str(INTEGRATION_DIR),
            "good_root": str(GOOD_ROOT),
            "python": str(python_exe()),
            "opencv": opencv_status(),
            "ffmpeg": resolve_ffmpeg(),
            "allowed_origins": list(ALLOWED_ORIGINS),
            "max_upload_mb": round(MAX_UPLOAD_BYTES / 1024 / 1024, 2),
            "weights": weights,
        }
    )


@app.route("/api/videos")
def api_videos():
    ensure_runtime_dirs()
    videos = []
    for path in sorted(VIDEOS_DIR.iterdir() if VIDEOS_DIR.exists() else []):
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        video_id = path.stem
        out_dir = output_dir_for(video_id)
        videos.append(
            {
                "video_id": video_id,
                "filename": path.name,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
                "has_court": (out_dir / "court_annotations.txt").exists(),
                "has_result": (out_dir / "detections.jsonl").exists(),
            }
        )
    return jsonify({"ok": True, "videos": videos})


@app.route("/upload", methods=["POST"])
@app.route("/api/upload", methods=["POST"])
def api_upload():
    ready, missing = runtime_ready()
    if not ready:
        return json_response_error("AI 后端尚未完成安装", 503, missing=missing)

    ensure_runtime_dirs()
    file = request.files.get("file")
    if file is None or not file.filename:
        return json_response_error("没有收到视频文件")

    original_name = file.filename
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return json_response_error("仅支持 MP4 / MOV / AVI / MKV / WEBM 视频")

    video_id = sanitize_video_id(original_name)
    filename = f"{video_id}{ext}"
    save_path = VIDEOS_DIR / filename
    file.save(str(save_path))
    out_dir = output_dir_for(video_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    return jsonify(
        {
            "ok": True,
            "video_id": video_id,
            "filename": filename,
            "original_filename": original_name,
            "size_mb": round(save_path.stat().st_size / 1024 / 1024, 2),
            "next": "detect_court",
        }
    )


@app.route("/api/videos/<video_id>/court/detect", methods=["POST"])
def api_detect_court(video_id: str):
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    ready, missing = runtime_ready()
    if not ready:
        return json_response_error("AI 后端尚未完成安装", 503, missing=missing)

    video_path = video_path_for(video_id)
    if video_path is None:
        return json_response_error("视频不存在", 404)

    out_dir = output_dir_for(video_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import_good_modules()
        from court_detect import auto_extract_template, detect_court

        template = auto_extract_template(str(video_path))
        if not template:
            return json_response_error("无法从视频提取球场模板帧", 500, manual_required=True)

        result = detect_court(template, str(out_dir))
        preview_name = "auto_court_preview.png"
        api_result = dict(result)
        for key in ("preview_path", "annotations_path"):
            if key in api_result and api_result[key]:
                api_result[key] = str(api_result[key])
        response = {
            "ok": bool(result.get("success")),
            "video_id": video_id,
            "template": f"/api/template/{video_id}",
            "preview": f"/api/output/{video_id}/{preview_name}",
            "manual_required": not bool(result.get("success")),
            **api_result,
        }
        if result.get("success"):
            response["message"] = "自动球场检测完成"
        else:
            response["message"] = result.get("error", "自动检测失败，请手动标注")
        return jsonify(response)
    except Exception as exc:
        return json_response_error(str(exc), 500, manual_required=True)


@app.route("/api/videos/<video_id>/court/annotate", methods=["POST"])
def api_manual_annotate(video_id: str):
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    data = request.get_json(silent=True) or {}
    corners = data.get("corners")
    if not isinstance(corners, list) or len(corners) != 4:
        return json_response_error("需要按左上、右上、右下、左下提供 4 个角点")

    try:
        normalized: list[tuple[int, int]] = []
        for point in corners:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError
            normalized.append((int(round(float(point[0]))), int(round(float(point[1])))))
    except Exception:
        return json_response_error("角点格式无效")

    template_path = template_path_for(video_id)
    if template_path is None:
        return json_response_error("找不到模板图，请先执行自动球场检测", 404)

    try:
        import_good_modules()
        import cv2
        import numpy as np
        from badminton_analysis.court.mapper import CourtMapper, compute_expanded_roi

        image = cv2.imread(str(template_path))
        if image is None:
            return json_response_error("无法读取模板图", 500)

        out_dir = output_dir_for(video_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        roi_corners = compute_expanded_roi(normalized, image.shape)
        mapper = CourtMapper(normalized)
        overlay, mid_height = mapper.draw_court_overlay(image)
        cv2.rectangle(overlay, roi_corners[0], roi_corners[1], (255, 0, 0), 3)
        for idx, point in enumerate(normalized, start=1):
            cv2.circle(overlay, point, 8, (0, 255, 255), -1)
            cv2.putText(overlay, str(idx), (point[0] + 12, point[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        preview_path = out_dir / "manual_court_preview.png"
        max_width = 1400
        if overlay.shape[1] > max_width:
            scale = max_width / overlay.shape[1]
            overlay = cv2.resize(overlay, (max_width, int(overlay.shape[0] * scale)))
        cv2.imwrite(str(preview_path), overlay)

        annotations_path = out_dir / "court_annotations.txt"
        annotations_path.write_text(
            f"corners={normalized}\nroi_corners={roi_corners}\nmid_height={int(mid_height)}\n",
            encoding="utf-8",
        )

        return jsonify(
            {
                "ok": True,
                "video_id": video_id,
                "corners": normalized,
                "roi_corners": roi_corners,
                "mid_height": int(mid_height),
                "preview": f"/api/output/{video_id}/manual_court_preview.png",
                "message": "手动球场标注已保存",
            }
        )
    except Exception as exc:
        return json_response_error(str(exc), 500)


@app.route("/api/analysis", methods=["POST"])
def api_start_analysis():
    data = request.get_json(silent=True) or {}
    video_id = data.get("video_id")
    if not video_id:
        return json_response_error("缺少 video_id")
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    ready, missing = runtime_ready()
    if not ready:
        return json_response_error("AI 后端尚未完成安装", 503, missing=missing)

    video_path = video_path_for(video_id)
    if video_path is None:
        return json_response_error("视频不存在", 404)

    template_path = template_path_for(video_id)
    if template_path is None:
        return json_response_error("请先完成自动球场检测或手动标注", 400)

    out_dir = output_dir_for(video_id)
    annotations = out_dir / "court_annotations.txt"
    if not annotations.exists():
        return json_response_error("缺少球场标注，请先检测或手动标注球场", 400)

    with _jobs_lock:
        existing = _jobs.get(video_id)
        if existing and existing.get("status") in {"queued", "running"}:
            return jsonify({"ok": True, "job_id": video_id, "message": "分析任务已在运行"})

        job = {
            "job_id": video_id,
            "video_id": video_id,
            "status": "queued",
            "progress": 0,
            "message": "任务已排队",
            "events": [],
            "event_queue": queue.Queue(),
            "created_at": time.time(),
            "result": None,
            "error": None,
        }
        _jobs[video_id] = job

    append_job_event(job, "queued", {"progress": 0, "message": "任务已排队", "status": "queued"})

    language = data.get("language", "zh") if data.get("language") in {"zh", "en"} else "zh"
    pose_family = data.get("pose_family", "yolo-pose")
    if pose_family not in {"yolo-pose", "rtmpose", "rtmo"}:
        pose_family = "yolo-pose"
    pose_mode = data.get("pose_mode", "balanced")
    if pose_mode not in {"lightweight", "balanced", "performance"}:
        pose_mode = "balanced"

    thread = threading.Thread(
        target=run_analysis_job,
        args=(video_id, video_path, template_path, out_dir, language, pose_family, pose_mode),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "job_id": video_id})


def run_analysis_job(
    job_id: str,
    video_path: Path,
    template_path: Path,
    out_dir: Path,
    language: str,
    pose_family: str,
    pose_mode: str,
) -> None:
    cmd = [
        str(python_exe()),
        str(RUNNER),
        "--good-root",
        str(GOOD_ROOT),
        "--video-id",
        job_id,
        "--video-path",
        str(video_path),
        "--template-path",
        str(template_path),
        "--output-dir",
        str(out_dir),
        "--weights-dir",
        str(WEIGHTS_DIR),
        "--language",
        language,
        "--pose-family",
        pose_family,
        "--pose-mode",
        pose_mode,
        "--audio",
        "false",
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["YOLO_CONFIG_DIR"] = str(INTEGRATION_DIR / ".yolo-config")
    env["PYTHONPATH"] = os.pathsep.join([str(GOOD_ROOT), env.get("PYTHONPATH", "")])
    env["PATH"] = os.pathsep.join(
        [str(GOOD_ROOT / ".local" / "bin"), str(Path.home() / ".local" / "bin"), env.get("PATH", "")]
    )

    update_job(job_id, "running", status="running", progress=1, message="正在启动 AI 分析子进程...")
    proc = subprocess.Popen(
        cmd,
        cwd=str(GOOD_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["proc"] = proc

    assert proc.stdout is not None
    last_progress = 1
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            update_job(job_id, "log", message=line)
            continue

        event_name = event.pop("event", "progress")
        if "progress" in event:
            last_progress = int(event["progress"])
        if event_name == "completed":
            result = public_result(job_id)
            update_job(job_id, "completed", status="completed", progress=100, message=event.get("message", "分析完成"), result=result)
        elif event_name == "error":
            update_job(job_id, "error", status="error", progress=last_progress, message=event.get("message", "分析失败"), error=event)
        elif event_name == "running":
            update_job(job_id, "running", status="running", progress=event.get("progress", last_progress), message=event.get("message", "正在运行"))
        else:
            progress = event.pop("progress", last_progress)
            message = event.pop("message", line)
            update_job(job_id, event_name, status="running", progress=progress, message=message, **event)

    return_code = proc.wait()
    with _jobs_lock:
        job = _jobs.get(job_id)
        final_status = job.get("status") if job else None
    if return_code == 0:
        if final_status != "completed":
            update_job(job_id, "completed", status="completed", progress=100, message="分析完成", result=public_result(job_id))
    else:
        if final_status != "error":
            update_job(job_id, "error", status="error", progress=last_progress, message=f"分析失败，退出码 {return_code}", error={"returncode": return_code})


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id: str):
    try:
        validate_video_id(job_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return jsonify(
                {
                    "ok": True,
                    "job_id": job_id,
                    "video_id": job.get("video_id"),
                    "status": job.get("status"),
                    "progress": job.get("progress", 0),
                    "message": job.get("message", ""),
                    "result": job.get("result"),
                    "error": job.get("error"),
                }
            )

    result = public_result(job_id)
    if result.get("detections"):
        return jsonify({"ok": True, "job_id": job_id, "video_id": job_id, "status": "completed", "progress": 100, "message": "已有分析结果", "result": result})
    return json_response_error("任务不存在", 404)


@app.route("/api/jobs/<job_id>/events")
def api_job_events(job_id: str):
    try:
        validate_video_id(job_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return json_response_error("任务不存在", 404)
        existing_events = list(job.get("events", []))
        event_queue = job["event_queue"]

    def format_event(item: dict[str, Any]) -> str:
        event_name = item.get("event", "message")
        data = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_name}\ndata: {data}\n\n"

    @stream_with_context
    def generate():
        for item in existing_events:
            yield format_event(item)
        while True:
            try:
                item = event_queue.get(timeout=15)
                yield format_event(item)
                if item.get("event") in {"completed", "error"}:
                    break
            except queue.Empty:
                with _jobs_lock:
                    current = _jobs.get(job_id, {})
                    status = current.get("status")
                yield ": keep-alive\n\n"
                if status in {"completed", "error"}:
                    break

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/jobs/<job_id>/result")
def api_job_result(job_id: str):
    try:
        validate_video_id(job_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)
    result = public_result(job_id)
    if not result.get("detections"):
        return json_response_error("结果尚未生成", 404)
    return jsonify({"ok": True, "result": result})


def load_metadata(video_id: str) -> dict[str, Any]:
    metadata_path = output_dir_for(video_id) / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def valid_court_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    if x < -1.5 or x > COURT_WIDTH_M + 1.5 or y < -1.5 or y > COURT_LENGTH_M + 1.5:
        return None
    return (x, y)


def calculate_stats(points: list[dict[str, Any]], fps: float) -> dict[str, Any]:
    total_distance = 0.0
    max_speed = 0.0
    speed_sum = 0.0
    speed_count = 0
    previous: dict[str, Any] | None = None

    for item in points:
        speed = item.get("speed")
        if isinstance(speed, (int, float)) and math.isfinite(float(speed)) and float(speed) >= 0:
            capped = min(float(speed), 12.0)
            speed_sum += capped
            speed_count += 1
            max_speed = max(max_speed, capped)

        if previous is not None:
            dx = item["x"] - previous["x"]
            dy = item["y"] - previous["y"]
            dist = math.hypot(dx, dy)
            dt = None
            if item.get("time_sec") is not None and previous.get("time_sec") is not None:
                dt = float(item["time_sec"]) - float(previous["time_sec"])
            elif fps > 0 and item.get("frame") is not None and previous.get("frame") is not None:
                dt = (int(item["frame"]) - int(previous["frame"])) / fps
            plausible = True
            if dt and dt > 0:
                plausible = dist / dt <= 10.0
                max_speed = max(max_speed, min(dist / dt, 12.0))
            if 0.02 <= dist <= 8.0 and plausible:
                total_distance += dist
        previous = item

    duration = 0.0
    if len(points) >= 2:
        first, last = points[0], points[-1]
        if first.get("time_sec") is not None and last.get("time_sec") is not None:
            duration = max(0.0, float(last["time_sec"]) - float(first["time_sec"]))
        elif fps > 0 and first.get("frame") is not None and last.get("frame") is not None:
            duration = max(0.0, (int(last["frame"]) - int(first["frame"])) / fps)

    avg_speed = speed_sum / speed_count if speed_count else (total_distance / duration if duration > 0 else 0.0)
    return {
        "samples": len(points),
        "distance_m": round(total_distance, 2),
        "duration_sec": round(duration, 2),
        "avg_speed_mps": round(avg_speed, 2),
        "max_speed_mps": round(max_speed, 2),
    }


def downsample(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return points
    step = math.ceil(len(points) / max_points)
    return points[::step]


@app.route("/api/jobs/<job_id>/heatmap-data")
def api_heatmap_data(job_id: str):
    try:
        validate_video_id(job_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    detections_path = output_dir_for(job_id) / "detections.jsonl"
    if not detections_path.exists():
        return json_response_error("detections.jsonl 尚未生成", 404)

    metadata = load_metadata(job_id)
    fps = float(metadata.get("video", {}).get("fps") or 0)
    max_points = int(request.args.get("max_points", "24000"))
    max_points = max(1000, min(max_points, 100000))

    by_player: dict[str, list[dict[str, Any]]] = {"upper": [], "lower": []}
    total_records = 0
    with detections_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            total_records += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            players = record.get("players") or {}
            for player in ("upper", "lower"):
                player_data = players.get(player) or {}
                point = valid_court_point(player_data.get("court"))
                if point is None:
                    continue
                speed = player_data.get("speed")
                by_player[player].append(
                    {
                        "player": player,
                        "frame": record.get("frame"),
                        "time_sec": record.get("time_sec"),
                        "x": round(point[0], 3),
                        "y": round(point[1], 3),
                        "speed": round(float(speed), 3) if isinstance(speed, (int, float)) and math.isfinite(float(speed)) else None,
                    }
                )

    all_points = by_player["upper"] + by_player["lower"]
    per_player_limit = max(500, max_points // 2)
    sampled_upper = downsample(by_player["upper"], per_player_limit)
    sampled_lower = downsample(by_player["lower"], per_player_limit)
    samples = sampled_upper + sampled_lower
    samples.sort(key=lambda item: (item.get("frame") or 0, item["player"]))

    return jsonify(
        {
            "ok": True,
            "video_id": job_id,
            "court": {"width": COURT_WIDTH_M, "length": COURT_LENGTH_M, "unit": "m"},
            "sampling": {
                "total_records": total_records,
                "total_points": len(all_points),
                "returned_points": len(samples),
                "max_points": max_points,
            },
            "samples": samples,
            "trajectories": {"upper": sampled_upper, "lower": sampled_lower},
            "stats": {
                "upper": calculate_stats(by_player["upper"], fps),
                "lower": calculate_stats(by_player["lower"], fps),
            },
            "metadata": metadata,
        }
    )


def build_speed_histogram(frames: list[dict[str, Any]]) -> dict[str, Any]:
    bins = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
    counts = [0 for _ in range(len(bins) - 1)]
    for frame in frames:
        speed = frame.get("speed_kmh")
        if not isinstance(speed, (int, float)):
            continue
        value = float(speed)
        for idx in range(len(bins) - 1):
            if bins[idx] <= value < bins[idx + 1]:
                counts[idx] += 1
                break
        else:
            if value >= bins[-1]:
                counts[-1] += 1
    labels = [f"{bins[idx]}-{bins[idx + 1]}" for idx in range(len(bins) - 1)]
    return {"bins": bins, "labels": labels, "counts": counts}


@app.route("/api/jobs/<job_id>/speed-data")
def api_speed_data(job_id: str):
    try:
        validate_video_id(job_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)

    speeds_path = output_dir_for(job_id) / "speeds.jsonl"
    summary_path = output_dir_for(job_id) / "speed_summary.json"
    if not speeds_path.exists() or not summary_path.exists():
        return json_response_error("球速数据尚未生成", 404)

    max_points = int(request.args.get("max_points", "30000"))
    max_points = max(1000, min(max_points, 120000))
    frames: list[dict[str, Any]] = []
    with speeds_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            speed = row.get("speed") or {}
            shuttle = row.get("shuttlecock") or {}
            frames.append(
                {
                    "frame": row.get("frame"),
                    "time_sec": row.get("time_sec"),
                    "speed_kmh": speed.get("smoothed_kmh"),
                    "raw_speed_kmh": speed.get("kmh"),
                    "level": speed.get("level"),
                    "outlier": speed.get("outlier", False),
                    "image": shuttle.get("image"),
                    "court": shuttle.get("court"),
                    "interpolated": shuttle.get("interpolated", False),
                    "event": row.get("event") or {"hit": False},
                }
            )
    sampled_frames = downsample(frames, max_points)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return jsonify(
        {
            "ok": True,
            "video_id": job_id,
            "note": "平面投影速度（非真实3D速度）",
            "frames": sampled_frames,
            "hits": summary.get("hits", []),
            "rallies": summary.get("rallies", []),
            "players": summary.get("players", {}),
            "summary": summary,
            "histogram": build_speed_histogram(frames),
            "sampling": {"total_frames": len(frames), "returned_frames": len(sampled_frames), "max_points": max_points},
        }
    )


@app.route("/api/template/<video_id>")
def api_template(video_id: str):
    try:
        path = template_path_for(video_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)
    if path is None or not path.exists():
        return json_response_error("模板不存在", 404)
    return send_file(str(path), conditional=True)


@app.route("/api/output/<video_id>/<path:subpath>")
def api_output(video_id: str, subpath: str):
    try:
        path = safe_output_path(video_id, subpath)
    except ValueError as exc:
        return json_response_error(str(exc), 400)
    if not path.exists() or not path.is_file():
        return json_response_error("文件不存在", 404)
    mimetype = mimetypes.guess_type(path.name)[0]
    if path.suffix.lower() == ".jsonl":
        mimetype = "application/x-ndjson"
    if path.suffix.lower() == ".mp4":
        mimetype = "video/mp4"
    response = make_response(send_file(str(path), mimetype=mimetype, conditional=True, etag=True, max_age=0))
    if path.suffix.lower() == ".mp4":
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Disposition"] = f'inline; filename="{path.name}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/api/videos/<video_id>", methods=["DELETE"])
def api_delete_video(video_id: str):
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response_error(str(exc), 400)
    with _jobs_lock:
        job = _jobs.get(video_id)
        proc = job.get("proc") if job else None
        if proc and proc.poll() is None:
            return json_response_error("分析任务仍在运行，不能删除", 409)
    video_path = video_path_for(video_id)
    if video_path and video_path.exists():
        video_path.unlink()
    out_dir = output_dir_for(video_id)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    template = template_path_for(video_id)
    if template and template.exists():
        template.unlink()
    with _jobs_lock:
        _jobs.pop(video_id, None)
    return jsonify({"ok": True})


@app.route("/")
def index():
    return jsonify({"ok": True, "service": "badminton-rules native AI Hawkeye API", "health": "/api/health"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native AI Hawkeye API for badminton-rules")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5050, type=int)
    return parser.parse_args()


initialize_startup()


if __name__ == "__main__":
    args = parse_args()
    print(f"Native AI Hawkeye API: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
