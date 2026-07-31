#!/usr/bin/env python3
"""Run the native AI Hawkeye API and static badminton-rules website together."""

from __future__ import annotations

import argparse
import http.server
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = Path(__file__).resolve().parent


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print("[site]", format % args)


def run_static_server(port: int) -> socketserver.TCPServer:
    handler = lambda *args, **kwargs: QuietHandler(*args, directory=str(SITE_ROOT), **kwargs)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-port", type=int, default=8000)
    parser.add_argument("--ai-port", type=int, default=5050)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    backend_cmd = [sys.executable, str(INTEGRATION_DIR / "run_good_badminton.py"), "--host", "127.0.0.1", "--port", str(args.ai_port)]
    backend = subprocess.Popen(backend_cmd, cwd=str(SITE_ROOT))
    site = run_static_server(args.site_port)
    url = f"http://127.0.0.1:{args.site_port}/index.html"
    print(f"Website: {url}")
    print(f"Native AI API: http://127.0.0.1:{args.ai_port}")
    if not args.no_browser:
        time.sleep(1)
        webbrowser.open(url)
    try:
        return backend.wait()
    except KeyboardInterrupt:
        backend.terminate()
        site.shutdown()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
