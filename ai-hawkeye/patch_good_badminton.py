#!/usr/bin/env python3
"""Apply cross-platform runtime patches to the cloned Good-Badminton app.py."""

from __future__ import annotations

from setup_good_badminton import ensure_repo, patch_good_badminton, sync_good_badminton_extensions


if __name__ == "__main__":
    ensure_repo(force=False)
    sync_good_badminton_extensions()
    patch_good_badminton()
    print("Good-Badminton patch applied.")
