#!/usr/bin/env python3
"""无头模式球场检测 - 不弹 GUI，自动检测边界"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np
import json

from badminton_analysis.court.mapper import compute_expanded_roi, CourtMapper
from badminton_analysis.court.detector import auto_detect_court_corners, render_auto_court_preview


def detect_court(template_path, save_dir):
    """自动检测球场边界，保存预览图和标注文件"""
    image = cv2.imread(template_path)
    if image is None:
        return {'success': False, 'error': '无法读取模板图'}

    h, w = image.shape[:2]
    fixed_size = (1080, 720)
    base_image = cv2.resize(image, fixed_size)

    auto_corners, _line_mask, auto_debug = auto_detect_court_corners(base_image)

    preview_path = os.path.join(save_dir, 'auto_court_preview.png')
    annotations_path = os.path.join(save_dir, 'court_annotations.txt')

    if auto_corners:
        auto_roi_corners = compute_expanded_roi(auto_corners, base_image.shape)
        preview = render_auto_court_preview(base_image, auto_corners, auto_roi_corners, auto_debug)
        cv2.imwrite(preview_path, preview)

        scale_x = w / fixed_size[0]
        scale_y = h / fixed_size[1]
        corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_corners]
        roi_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_roi_corners]

        cm = CourtMapper(auto_corners)
        _, mid_height = cm.draw_court_overlay(base_image)
        mid_height = int(mid_height * scale_y)

        with open(annotations_path, 'w') as f:
            f.write(f"corners={corners}\n")
            f.write(f"roi_corners={roi_corners}\n")
            f.write(f"mid_height={mid_height}\n")

        return {
            'success': True,
            'corners': corners,
            'roi_corners': roi_corners,
            'mid_height': mid_height,
            'preview_path': preview_path,
            'annotations_path': annotations_path,
        }
    else:
        auto_corners = [
            (int(fixed_size[0] * 0.24), int(fixed_size[1] * 0.40)),
            (int(fixed_size[0] * 0.76), int(fixed_size[1] * 0.40)),
            (int(fixed_size[0] * 0.90), int(fixed_size[1] * 0.94)),
            (int(fixed_size[0] * 0.10), int(fixed_size[1] * 0.94)),
        ]
        auto_roi_corners = compute_expanded_roi(auto_corners, base_image.shape)
        preview = render_auto_court_preview(base_image, auto_corners, auto_roi_corners, auto_debug)
        cv2.imwrite(preview_path, preview)

        scale_x = w / fixed_size[0]
        scale_y = h / fixed_size[1]
        corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_corners]
        roi_corners = [(int(x * scale_x), int(y * scale_y)) for x, y in auto_roi_corners]

        cm = CourtMapper(auto_corners)
        _, mid_height = cm.draw_court_overlay(base_image)
        mid_height = int(mid_height * scale_y)

        with open(annotations_path, 'w') as f:
            f.write(f"corners={corners}\n")
            f.write(f"roi_corners={roi_corners}\n")
            f.write(f"mid_height={mid_height}\n")

        return {
            'success': True,
            'fallback': True,
            'message': '自动检测失败，已使用默认无头球场标注',
            'corners': corners,
            'roi_corners': roi_corners,
            'mid_height': mid_height,
            'preview_path': preview_path,
            'annotations_path': annotations_path,
        }


def auto_extract_template(video_path):
    """从视频中采样多帧，选球场特征最强的一帧作为模板"""
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return None

    # 从 15%-60% 采样 10 帧
    positions = np.linspace(int(total * 0.15), int(total * 0.60), 10, dtype=int)
    best_frame, best_score = None, -1

    for pos in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if not ret:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        h, w = gray.shape
        # 重点看下半部分（球场区域）
        lower = edges[h//3:, :]
        score = np.sum(lower > 0) / lower.size

        if score > best_score:
            best_score = score
            best_frame = frame.copy()

    cap.release()

    if best_frame is not None:
        project_root = os.path.dirname(os.path.abspath(__file__))
        save_path = os.path.join(project_root, 'templates',
                                 f'_auto_{os.path.basename(video_path).rsplit(".", 1)[0]}.png')
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cv2.imwrite(save_path, best_frame)
        return save_path

    return None


if __name__ == '__main__':
    # CLI mode: python court_detect.py <video_or_template> <save_dir>
    from badminton_analysis.system import load_runtime_dependencies
    load_runtime_dependencies()

    path = sys.argv[1]
    save_dir = sys.argv[2]
    os.makedirs(save_dir, exist_ok=True)

    # 判断是视频还是图片
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm'):
        print('从视频提取模板帧...')
        template = auto_extract_template(path)
        if not template:
            print('ERROR: 提取模板失败')
            sys.exit(1)
        print(f'模板已提取: {template}')
    else:
        template = path

    result = detect_court(template, save_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
