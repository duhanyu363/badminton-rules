#!/usr/bin/env python3
"""Setup Good-Badminton runtime for badminton-rules native AI Hawkeye.

This script performs reproducible local setup:
- clone/update qwpyyx/Good-Badminton
- create .venv
- install Python dependencies used by the native API and analysis runner
- patch upstream app.py for optional Web UI debugging on Windows/cross-platform
- optionally download public release weights
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlretrieve

INTEGRATION_DIR = Path(__file__).resolve().parent
SITE_ROOT = INTEGRATION_DIR.parent
GOOD_REPO = INTEGRATION_DIR / "Good-Badminton"
EXTENSIONS_DIR = INTEGRATION_DIR / "good_badminton_ext"
GOOD_URL = "https://github.com/qwpyyx/Good-Badminton.git"
RELEASE_BASE = "https://github.com/yo-WASSUP/Good-Badminton/releases/download/v0.1.0"

WEIGHTS = {
    "yolo11s-ball.pt": f"{RELEASE_BASE}/yolo11s-ball.pt",
    "yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx": f"{RELEASE_BASE}/yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx",
    "yolo11n-pose.pt": f"{RELEASE_BASE}/yolo11n-pose.pt",
    "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.onnx": f"{RELEASE_BASE}/rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.onnx",
    "rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.onnx": f"{RELEASE_BASE}/rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.onnx",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-weights", action="store_true", help="Download public model weights")
    parser.add_argument("--skip-install", action="store_true", help="Skip pip install")
    parser.add_argument("--force", action="store_true", help="Force git pull even if repo exists")
    args = parser.parse_args()

    ensure_repo(force=args.force)
    sync_good_badminton_extensions()
    patch_good_badminton()
    ensure_venv()
    if not args.skip_install:
        install_requirements()
    if args.download_weights:
        download_weights()
    print_status()
    return 0


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True)


def ensure_repo(force: bool = False) -> None:
    if GOOD_REPO.exists():
        if force:
            run(["git", "pull", "--ff-only"], cwd=GOOD_REPO)
        return
    run(["git", "clone", GOOD_URL, str(GOOD_REPO)], cwd=INTEGRATION_DIR)


def sync_good_badminton_extensions() -> None:
    """Copy local extension modules into the cloned Good-Badminton package."""
    if not EXTENSIONS_DIR.exists() or not GOOD_REPO.exists():
        return
    for source in EXTENSIONS_DIR.rglob("*"):
        if source.is_dir() or "__pycache__" in source.parts or source.suffix == ".pyc":
            continue
        relative = source.relative_to(EXTENSIONS_DIR)
        target = GOOD_REPO / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"[extension] {relative}")


def python_exe() -> Path:
    if platform.system().lower().startswith("win"):
        return GOOD_REPO / ".venv" / "Scripts" / "python.exe"
    return GOOD_REPO / ".venv" / "bin" / "python3"


def ensure_venv() -> None:
    exe = python_exe()
    if exe.exists():
        return
    run([sys.executable, "-m", "venv", str(GOOD_REPO / ".venv")])


def install_requirements() -> None:
    exe = python_exe()
    run([str(exe), "-m", "pip", "install", "--upgrade", "pip"], cwd=GOOD_REPO)
    # Use integration requirements for the native Flask API and Good-Badminton runtime pins.
    run([str(exe), "-m", "pip", "install", "-r", str(INTEGRATION_DIR / "requirements.txt")], cwd=GOOD_REPO)


def patch_good_badminton() -> None:
    """Patch app.py so subprocess Python/FFmpeg paths work on Windows and Linux."""
    app_py = GOOD_REPO / "app.py"
    if not app_py.exists():
        return
    text = app_py.read_text(encoding="utf-8")
    if "# BEGIN badminton-rules cross-platform patch" in text:
        return

    text = text.replace(
        "_venv_python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python3')",
        """# BEGIN badminton-rules cross-platform patch\nif os.name == 'nt':\n    _venv_python = str(PROJECT_ROOT / '.venv' / 'Scripts' / 'python.exe')\nelse:\n    _venv_python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python3')\n\ndef _prepend_path(env, *paths):\n    existing = env.get('PATH', '')\n    prefix = os.pathsep.join(str(p) for p in paths if p)\n    env['PATH'] = prefix + (os.pathsep + existing if existing else '')\n    return env\n\ndef _ffmpeg_bin():\n    for candidate in (shutil.which('ffmpeg'), str(PROJECT_ROOT / '.local' / 'bin' / 'ffmpeg'), os.path.expanduser('~/.local/bin/ffmpeg')):\n        if candidate and os.path.exists(candidate):\n            return candidate\n    return 'ffmpeg'\n# END badminton-rules cross-platform patch""",
    )
    text = text.replace(
        "env['PATH'] = f\"{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}\"",
        "_prepend_path(env, os.path.expanduser('~/.local/bin'), PROJECT_ROOT / '.local' / 'bin')",
    )
    text = text.replace(
        "env['PATH'] = f\"{PROJECT_ROOT / '.local' / 'bin'}:{env.get('PATH', '')}\"",
        "_prepend_path(env, PROJECT_ROOT / '.local' / 'bin', os.path.expanduser('~/.local/bin'))",
    )
    text = text.replace(
        "ffmpeg_bin = os.path.expanduser('~/.local/bin/ffmpeg')",
        "ffmpeg_bin = _ffmpeg_bin()",
    )
    app_py.write_text(text, encoding="utf-8")


def download_weights() -> None:
    weights_dir = INTEGRATION_DIR / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    mirror_dir = GOOD_REPO / "weights"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for filename, url in WEIGHTS.items():
        target = weights_dir / filename
        if target.exists() and target.stat().st_size > 0:
            print(f"[ok] {target}")
        else:
            print(f"[download] {filename}")
            urlretrieve(url, target)
            print(f"[saved] {target}")
        mirror_target = mirror_dir / filename
        if mirror_target.resolve() != target.resolve():
            if not mirror_target.exists() or mirror_target.stat().st_size != target.stat().st_size:
                shutil.copy2(target, mirror_target)
                print(f"[mirrored] {mirror_target}")


def print_status() -> None:
    print("\nGood-Badminton integration status")
    print("repo:", GOOD_REPO)
    print("python:", python_exe(), "exists=", python_exe().exists())
    print("ffmpeg:", shutil.which("ffmpeg") or "NOT FOUND on PATH")
    weights_dir = INTEGRATION_DIR / "weights"
    print("weights dir:", weights_dir)
    print("mirror dir:", GOOD_REPO / "weights")
    for filename in WEIGHTS:
        p = weights_dir / filename
        print(("FOUND  " if p.exists() else "MISSING"), p)
    print("\nRun backend:")
    print("python ai-hawkeye\\run_good_badminton.py --host 127.0.0.1 --port 5050")


if __name__ == "__main__":
    raise SystemExit(main())
