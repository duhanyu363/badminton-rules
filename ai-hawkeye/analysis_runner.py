#!/usr/bin/env python3
"""Headless Good-Badminton analysis runner for the native API.

This script deliberately bypasses Good-Badminton's Flask/Web UI. It imports the
real Python analysis modules and emits newline-delimited JSON progress events to
stdout so the parent API process can stream them to the browser via SSE.
"""

from __future__ import annotations

import sys, subprocess

print("analysis_runner bootstrap python:", sys.executable, flush=True)
site_root = Path(__file__).resolve().parents[1]
venv_site_packages = site_root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if venv_site_packages.exists() and str(venv_site_packages) not in sys.path:
    sys.path.insert(0, str(venv_site_packages))
print("analysis_runner bootstrap sys.path:", sys.path, flush=True)
try:
    import cv2
except ImportError as exc:
    print(f"cv2 import failed in analysis_runner: {exc}", flush=True)
    raise
print("cv2 imported successfully, version:", cv2.__version__, flush=True)

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


def emit(event: str, **payload: Any) -> None:
    message = {"event": event, **payload}
    print(json.dumps(message, ensure_ascii=False, separators=(",", ":")), flush=True)


def describe_path(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    info: dict[str, Any] = {
        "path": str(path),
        "resolved": str(resolved),
        "exists": resolved.exists(),
    }
    if resolved.exists():
        info["is_file"] = resolved.is_file()
        info["is_dir"] = resolved.is_dir()
        if resolved.is_file():
            info["size_bytes"] = resolved.stat().st_size
    return info


def redacted_environment() -> dict[str, str]:
    sensitive_markers = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASS", "CREDENTIAL", "AUTH", "COOKIE")
    result: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        result[key] = "<redacted>" if any(marker in key.upper() for marker in sensitive_markers) else value
    return result


def prepend_path(*paths: Path) -> None:
    existing = os.environ.get("PATH", "")
    prefix = os.pathsep.join(str(path) for path in paths if path)
    if prefix:
        os.environ["PATH"] = prefix + (os.pathsep + existing if existing else "")


def sync_extensions(good_root: Path) -> None:
    """Ensure local Good-Badminton extension modules are present in the clone."""
    extension_root = Path(__file__).resolve().parent / "good_badminton_ext"
    if not extension_root.exists() or not good_root.exists():
        return
    for source in extension_root.rglob("*"):
        if source.is_dir() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        target = good_root / source.relative_to(extension_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


_opencv_install_attempted = False


def import_cv2_for_runtime():
    global _opencv_install_attempted
    emit("log", message=f"Python executable: {sys.executable}")
    emit("log", message=f"Python sys.path: {json.dumps(sys.path, ensure_ascii=False)}")
    try:
        import cv2 as cv2
    except ImportError as exc:
        if _opencv_install_attempted:
            emit("error", progress=0, message=f"OpenCV import failed after install attempt: {exc}", python=sys.executable, sys_path=sys.path)
            return None
        emit("log", message=f"OpenCV import failed before reinstall: {exc}")
        _opencv_install_attempted = True
        import importlib

        subprocess.check_call([
            sys.executable,
            "-m",
            "pip",
            "install",
            "numpy",
            "opencv-python-headless",
            "opencv-contrib-python-headless",
        ])
        importlib.invalidate_caches()
        import cv2 as cv2
    emit("log", message=f"OpenCV imported: version={getattr(cv2, '__version__', 'unknown')} file={getattr(cv2, '__file__', 'unknown')}")
    return cv2


def read_video_info(video_path: Path) -> tuple[int, float]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    finally:
        cap.release()
    return max(total_frames, 0), fps


def resolve_ffmpeg(good_root: Path | None = None) -> str | None:
    """Find an FFmpeg executable for browser-compatible MP4 transcode."""
    candidates: list[str | Path | None] = [shutil.which("ffmpeg")]
    if good_root is not None:
        candidates.extend([
            good_root / ".local" / "bin" / "ffmpeg.exe",
            good_root / ".local" / "bin" / "ffmpeg",
        ])
    candidates.extend([
        Path.home() / ".local" / "bin" / "ffmpeg.exe",
        Path.home() / ".local" / "bin" / "ffmpeg",
    ])
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


def transcode_browser_mp4(video_path: Path, good_root: Path | None = None) -> bool:
    """Re-encode an output video to browser-friendly H.264/yuv420p MP4."""
    if not video_path.exists() or video_path.stat().st_size <= 0:
        return False

    ffmpeg = resolve_ffmpeg(good_root)
    if not ffmpeg:
        emit("log", message="未找到 FFmpeg，无法转码为浏览器兼容 H.264；视频可能显示 0:00")
        return False

    temp_path = video_path.with_name(video_path.stem + "_browser" + video_path.suffix)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-level",
        "4.0",
        "-tag:v",
        "avc1",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    try:
        emit("log", message=f"正在转码浏览器兼容 MP4: {video_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
            temp_path.replace(video_path)
            emit("log", message="视频已转码为 H.264/yuv420p/faststart，可被浏览器 video 标签播放")
            return True
        stderr_tail = (result.stderr or result.stdout or "")[-800:]
        emit("log", message=f"FFmpeg 转码失败，保留原始视频: {stderr_tail}")
    except Exception as exc:  # pragma: no cover - environment-specific fallback
        emit("log", message=f"FFmpeg 转码异常，保留原始视频: {exc}")
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Good-Badminton analysis without Web UI")
    parser.add_argument("--good-root", required=True, type=Path)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--video-path", required=True, type=Path)
    parser.add_argument("--template-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--weights-dir", required=True, type=Path)
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--pose-family", default="yolo-pose", choices=["yolo-pose", "rtmpose", "rtmo"])
    parser.add_argument("--pose-mode", default="balanced", choices=["lightweight", "balanced", "performance"])
    parser.add_argument("--audio", choices=["true", "false"], default="false")
    args = parser.parse_args()

    good_root = args.good_root.resolve()
    if not good_root.exists():
        emit("error", progress=0, message=f"Good-Badminton 不存在: {good_root}")
        return 2

    os.chdir(str(good_root))
    sync_extensions(good_root)
    sys.path.insert(0, str(good_root))
    prepend_path(good_root / ".local" / "bin", Path.home() / ".local" / "bin")

    ball_model = args.weights_dir / "yolo11s-ball.pt"
    yolo_pose_model = args.weights_dir / "yolo11n-pose.pt"
    for required in (args.video_path, args.template_path, ball_model, yolo_pose_model):
        if not required.exists():
            emit("error", progress=0, message=f"缺少必需文件: {required}")
            return 2

    emit("running", progress=1, message="正在加载 Good-Badminton 运行依赖与模型...")
    emit(
        "log",
        message="analysis startup debug",
        good_root=str(good_root),
        video_path=describe_path(args.video_path),
        template_path=describe_path(args.template_path),
        output_dir=describe_path(args.output_dir),
        weights_dir=describe_path(args.weights_dir),
        ball_model=describe_path(ball_model),
        yolo_pose_model=describe_path(yolo_pose_model),
        cwd=os.getcwd(),
        python_executable=sys.executable,
        sys_path=sys.path,
        environment=redacted_environment(),
    )
    if import_cv2_for_runtime() is None:
        return 1

    try:
        from badminton_analysis.system import BadmintonAnalysisSystem, load_runtime_dependencies

        load_runtime_dependencies()
        total_frames, fps = read_video_info(args.video_path)
    except Exception as exc:
        import traceback

        emit("error", progress=0, message=str(exc), traceback=traceback.format_exc())
        return 1

    class ProgressBadmintonAnalysisSystem(BadmintonAnalysisSystem):
        def __init__(self, *system_args: Any, **system_kwargs: Any) -> None:
            super().__init__(*system_args, **system_kwargs)
            self._progress_total_frames = max(total_frames, 1)
            self._last_emit_time = 0.0

        def _process_frame(self, frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count):
            processed_frame, next_detect_count = super()._process_frame(
                frame,
                template_gray,
                corners,
                roi_corners,
                frame_count,
                out,
                detect_frame_count,
            )
            now = time.monotonic()
            if frame_count == 1 or frame_count == self._progress_total_frames or now - self._last_emit_time >= 1.0:
                pct = min(94, max(2, int(frame_count / self._progress_total_frames * 94)))
                emit(
                    "progress",
                    progress=pct,
                    message=f"AI 分析中：{frame_count}/{self._progress_total_frames} 帧",
                    frame=int(frame_count),
                    total_frames=int(self._progress_total_frames),
                    detected_frames=int(next_detect_count),
                )
                self._last_emit_time = now
            return processed_frame, next_detect_count

    try:
        system = ProgressBadmintonAnalysisSystem(
            str(args.video_path),
            show_display=False,
            show_skeletons=True,
            show_player_trajectories=True,
            show_court_trajectory=True,
            show_shuttlecock_trajectory=True,
            show_player_stats=True,
            show_performance_stats=True,
            save_images=False,
            language=args.language,
            output_dir=str(args.output_dir),
            ball_model_path=str(ball_model),
            template_path=str(args.template_path),
            pose_mode=args.pose_mode,
            pose_family=args.pose_family,
            yolo_pose_model=str(yolo_pose_model),
            show_pose_roi=True,
        )
        system.keep_audio = args.audio == "true"
        emit("progress", progress=2, message="模型加载完成，开始逐帧分析...", total_frames=total_frames, fps=fps)
        system.process_video()

        emit("progress", progress=95, message="正在计算羽毛球平面投影速度...")
        from badminton_analysis.analysis.shuttle_speed import calculate_shuttle_speeds
        from badminton_analysis.visualization.shuttle_speed_overlay import annotate_video_with_shuttle_speeds

        speeds_path = args.output_dir / "speeds.jsonl"
        speed_summary_path = args.output_dir / "speed_summary.json"
        calculate_shuttle_speeds(
            detections_path=system.detections_path,
            metadata_path=system.metadata_path,
            output_jsonl_path=str(speeds_path),
            summary_path=str(speed_summary_path),
            rally_segments_path=str(args.output_dir / "rally_segments.json"),
        )

        emit("progress", progress=96, message="正在叠加球速标注层...")
        speed_video_path = args.output_dir / f"detect_{args.video_id}_speed.mp4"
        annotate_video_with_shuttle_speeds(
            input_video_path=system.output_video_path,
            output_video_path=str(speed_video_path),
            speeds_jsonl_path=str(speeds_path),
            summary_json_path=str(speed_summary_path),
            fps=system.fps,
        )
        if speed_video_path.exists() and speed_video_path.stat().st_size > 0:
            Path(system.output_video_path).unlink(missing_ok=True)
            speed_video_path.replace(system.output_video_path)

        emit("progress", progress=97, message="正在生成位置热力图、轨迹图与统计数据...")
        if args.language == "en":
            from badminton_analysis.visualization.player_positions_en import analyze_player_positions
        else:
            from badminton_analysis.visualization.player_positions_zh import analyze_player_positions

        analyze_player_positions(
            system.detections_path,
            output_dir=str(args.output_dir / "position_visualizations"),
            fps=system.fps,
        )

        emit("progress", progress=99, message="正在整理浏览器可播放的视频输出...")
        transcode_browser_mp4(Path(system.output_video_path), good_root=good_root)

        emit(
            "completed",
            progress=100,
            message="分析完成",
            video_id=args.video_id,
            detections=str(Path(system.detections_path).resolve()),
            output_video=str(Path(system.output_video_path).resolve()),
            fps=system.fps,
        )
        return 0
    except Exception as exc:
        import traceback

        emit("error", progress=0, message=str(exc), traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
