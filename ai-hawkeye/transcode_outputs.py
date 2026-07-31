#!/usr/bin/env python3
"""Transcode Good-Badminton output videos to browser-compatible MP4.

Use this when the browser video element shows 0:00 or refuses to play an already
created detect_<video>.mp4. The script uses FFmpeg from PATH, Good-Badminton's
.local/bin, or imageio-ffmpeg if installed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

INTEGRATION_DIR = Path(__file__).resolve().parent
GOOD_ROOT = INTEGRATION_DIR / "Good-Badminton"
OUTPUTS_DIR = GOOD_ROOT / "outputs"


def resolve_ffmpeg() -> str | None:
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


def transcode(input_path: Path, output_path: Path | None = None) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found. Install FFmpeg or run setup to install imageio-ffmpeg.")
    output_path = output_path or input_path.with_name(input_path.stem + "_browser.mp4")
    temp_path = output_path.with_suffix(".tmp.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size <= 0:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg failed")[-2000:])
    temp_path.replace(output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcode Good-Badminton MP4 to browser-compatible H.264")
    parser.add_argument("video", nargs="?", help="Path to detect_<video>.mp4. If omitted, transcode all outputs/**/detect_*.mp4 in place.")
    parser.add_argument("--in-place", action="store_true", help="Replace the input file. Default for batch mode.")
    args = parser.parse_args()

    if args.video:
        input_path = Path(args.video).resolve()
        output_path = input_path if args.in_place else input_path.with_name(input_path.stem + "_browser.mp4")
        print(f"Transcoding {input_path} -> {output_path}")
        print(transcode(input_path, output_path))
        return 0

    if not OUTPUTS_DIR.exists():
        print(f"No outputs directory: {OUTPUTS_DIR}")
        return 2
    videos = sorted(OUTPUTS_DIR.glob("**/detect_*.mp4"))
    if not videos:
        print("No detect_*.mp4 files found.")
        return 0
    for input_path in videos:
        print(f"Transcoding in-place: {input_path}")
        transcode(input_path, input_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
