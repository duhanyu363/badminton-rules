#!/usr/bin/env python3
"""Headless court detection for the native AI Hawkeye API."""

import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from badminton_analysis.court.mapper import CourtMapper, compute_expanded_roi
from badminton_analysis.court.detector import auto_detect_court_corners, render_auto_court_preview


def detect_court(template_path, save_dir):
    image = cv2.imread(template_path)
    if image is None:
        return {"success": False, "error": "无法读取模板图"}

    h, w = image.shape[:2]
    fixed_size = (1080, 720)
    base_image = cv2.resize(image, fixed_size)

    auto_corners, _line_mask, auto_debug = auto_detect_court_corners(base_image)

    preview_path = os.path.join(save_dir, "auto_court_preview.png")
    annotations_path = os.path.join(save_dir, "court_annotations.txt")

    if auto_corners:
        auto_roi_corners = compute_expanded_roi(auto_corners, base_image.shape)
        preview = render_auto_court_preview(base_image, auto_corners, auto_roi_corners, auto_debug)
        cv2.imwrite(preview_path, preview)

        scale_x = w / fixed_size[0]
        scale_y = h / fixed_size[1]
        corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_corners]
        roi_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_roi_corners]

        court_mapper = CourtMapper(auto_corners)
        _, mid_height = court_mapper.draw_court_overlay(base_image)
        mid_height = int(mid_height * scale_y)

        with open(annotations_path, "w", encoding="utf-8") as file:
            file.write(f"corners={corners}\n")
            file.write(f"roi_corners={roi_corners}\n")
            file.write(f"mid_height={mid_height}\n")

        return {
            "success": True,
            "corners": corners,
            "roi_corners": roi_corners,
            "mid_height": mid_height,
            "preview_path": preview_path,
            "annotations_path": annotations_path,
        }

    preview = render_auto_court_preview(base_image, None, None, auto_debug)
    cv2.imwrite(preview_path, preview)
    return {
        "success": False,
        "error": "自动检测失败，需要手动标注球场",
        "preview_path": preview_path,
    }


def auto_extract_template(video_path):
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return None

    positions = np.linspace(int(total * 0.15), int(total * 0.60), 10, dtype=int)
    best_frame, best_score = None, -1

    for pos in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        h, _w = gray.shape
        lower = edges[h // 3 :, :]
        score = np.sum(lower > 0) / lower.size

        if score > best_score:
            best_score = score
            best_frame = frame.copy()

    cap.release()

    if best_frame is not None:
        project_root = os.path.dirname(os.path.abspath(__file__))
        save_path = os.path.join(project_root, "templates", f'_auto_{os.path.basename(video_path).rsplit(".", 1)[0]}.png')
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cv2.imwrite(save_path, best_frame)
        return save_path

    return None


if __name__ == "__main__":
    from badminton_analysis.system import load_runtime_dependencies

    load_runtime_dependencies()

    path = sys.argv[1]
    save_dir = sys.argv[2]
    os.makedirs(save_dir, exist_ok=True)

    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
        print("从视频提取模板帧...")
        template = auto_extract_template(path)
        if not template:
            print("ERROR: 提取模板失败")
            sys.exit(1)
        print(f"模板已提取: {template}")
    else:
        template = path

    result = detect_court(template, save_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
