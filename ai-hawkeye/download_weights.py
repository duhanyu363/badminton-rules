#!/usr/bin/env python3
"""Download Good-Badminton release weights into ai-hawkeye/Good-Badminton/weights."""

from __future__ import annotations

from setup_good_badminton import download_weights, ensure_repo, patch_good_badminton, print_status, sync_good_badminton_extensions


if __name__ == "__main__":
    ensure_repo(force=False)
    sync_good_badminton_extensions()
    patch_good_badminton()
    download_weights()
    print_status()
