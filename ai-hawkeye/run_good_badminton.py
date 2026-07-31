#!/usr/bin/env python3
"""Run the native AI Hawkeye API for badminton-rules."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

INTEGRATION_DIR = Path(__file__).resolve().parent
GOOD_REPO = INTEGRATION_DIR / "Good-Badminton"


def python_exe() -> Path:
    if platform.system().lower().startswith("win"):
        return GOOD_REPO / ".venv" / "Scripts" / "python.exe"
    return GOOD_REPO / ".venv" / "bin" / "python3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run badminton-rules native AI Hawkeye API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument(
        "--upstream-webui",
        action="store_true",
        help="Run Good-Badminton's original Flask Web UI instead of the native API (debug only)",
    )
    args = parser.parse_args()

    exe = python_exe()
    if not GOOD_REPO.exists():
        print("Good-Badminton not found. Run:")
        print("python ai-hawkeye\\setup_good_badminton.py --download-weights")
        return 2
    if not exe.exists():
        print("Good-Badminton venv not found. Run setup first.")
        return 2

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    if args.upstream_webui:
        app_py = GOOD_REPO / "app.py"
        if not app_py.exists():
            print("Good-Badminton app.py not found. Run setup first.")
            return 2
        print(f"Starting original Good-Badminton Web UI at http://{args.host}:{args.port}")
        return subprocess.call([str(exe), str(app_py), "--host", args.host, "--port", str(args.port)], cwd=str(GOOD_REPO), env=env)

    native_api = INTEGRATION_DIR / "native_api.py"
    print(f"Starting native AI Hawkeye API at http://{args.host}:{args.port}")
    return subprocess.call([str(exe), str(native_api), "--host", args.host, "--port", str(args.port)], cwd=str(INTEGRATION_DIR), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
