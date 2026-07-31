"""Video overlay for shuttlecock planar speed analysis."""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np

PROJECTION_LABEL = "Planar projected speed (not true 3D)"


def _load_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _load_speed_rows(path: str | os.PathLike[str]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            frame = int(row.get("frame") or 0)
            if frame > 0:
                rows[frame] = row
    return rows


def _speed_color(speed_kmh: float | None) -> tuple[int, int, int]:
    if speed_kmh is None:
        return (180, 180, 180)
    if speed_kmh < 150:
        return (80, 220, 80)
    if speed_kmh < 250:
        return (0, 220, 255)
    if speed_kmh < 350:
        return (0, 145, 255)
    return (60, 60, 255)


def _event_english_label(event_type: str | None) -> str:
    return {"smash": "Smash", "drive": "Drive", "drop": "Drop"}.get(event_type or "", "Hit")


def _put_text_with_bg(
    frame,
    text: str,
    origin: tuple[int, int],
    font_scale: float,
    color: tuple[int, int, int],
    thickness: int = 2,
    bg_alpha: float = 0.52,
) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    (w, h), baseline = cv2.getTextSize(text, font, font_scale, thickness)
    x, y = origin
    x = max(6, min(x, frame.shape[1] - w - 10))
    y = max(h + 8, min(y, frame.shape[0] - baseline - 8))
    overlay = frame.copy()
    cv2.rectangle(overlay, (x - 8, y - h - 8), (x + w + 8, y + baseline + 8), (8, 12, 24), -1)
    cv2.addWeighted(overlay, bg_alpha, frame, 1 - bg_alpha, 0, frame)
    cv2.putText(frame, text, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)


def _draw_speed_panel(frame, speed_kmh: float | None, frame_index: int) -> None:
    h, w = frame.shape[:2]
    panel_w = max(260, int(w * 0.16))
    panel_h = 92
    x1 = w - panel_w - 22
    y1 = 22
    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x1 + panel_w, y1 + panel_h), (5, 10, 20), -1)
    cv2.addWeighted(overlay, 0.62, frame, 0.38, 0, frame)
    color = _speed_color(speed_kmh)
    label = "--" if speed_kmh is None else f"{speed_kmh:.0f}"
    cv2.putText(frame, "SHUTTLE SPEED", (x1 + 14, y1 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 230, 240), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{label} km/h", (x1 + 14, y1 + 62), cv2.FONT_HERSHEY_SIMPLEX, 1.05, color, 2, cv2.LINE_AA)
    cv2.putText(frame, PROJECTION_LABEL, (x1 + 14, y1 + 84), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 180, 195), 1, cv2.LINE_AA)


def _collect_active_hit_labels(frame_index: int, hits: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
    duration = max(1, int(fps))
    active = []
    for hit in hits:
        hit_frame = int(hit.get("frame") or 0)
        age = frame_index - hit_frame
        if 0 <= age <= duration:
            item = dict(hit)
            item["alpha"] = 1.0 - (age / duration)
            active.append(item)
    return active


def _draw_hit_labels(frame, active_hits: list[dict[str, Any]]) -> None:
    for hit in active_hits[-4:]:
        image = hit.get("image")
        if not isinstance(image, list) or len(image) != 2:
            continue
        speed = float(hit.get("speed_kmh") or 0)
        label = f"{_event_english_label(hit.get('type'))}: {speed:.0f} km/h"
        color = _speed_color(speed)
        alpha = float(hit.get("alpha", 1.0))
        x, y = int(image[0]), int(image[1])
        overlay = frame.copy()
        cv2.circle(overlay, (x, y), 12, color, 2, cv2.LINE_AA)
        cv2.addWeighted(overlay, max(0.15, alpha), frame, 1 - max(0.15, alpha), 0, frame)
        _put_text_with_bg(frame, label, (x + 16, y - 16), 0.64, color, 2, bg_alpha=0.35 + 0.25 * alpha)


def _rally_end_messages(summary: dict[str, Any], fps: float) -> dict[int, dict[str, Any]]:
    messages: dict[int, dict[str, Any]] = {}
    for rally in summary.get("rallies", []):
        try:
            end_frame = int(rally.get("end_frame"))
        except (TypeError, ValueError):
            continue
        messages[end_frame] = rally
    return messages


def _draw_rally_summary(frame, frame_index: int, rally_messages: dict[int, dict[str, Any]], fps: float) -> None:
    duration = max(1, int(fps * 2))
    active = None
    active_end = None
    for end_frame, rally in rally_messages.items():
        if 0 <= frame_index - end_frame <= duration:
            active = rally
            active_end = end_frame
            break
    if not active:
        return
    alpha = 1.0 - ((frame_index - active_end) / duration)
    text1 = f"Rally {active.get('id')}: max {float(active.get('max_speed_kmh') or 0):.0f} km/h"
    text2 = f"avg {float(active.get('avg_speed_kmh') or 0):.0f} km/h · hits {int(active.get('hit_count') or 0)}"
    x = 30
    y = 105
    _put_text_with_bg(frame, text1, (x, y), 0.76, (255, 230, 120), 2, bg_alpha=0.35 + alpha * 0.25)
    _put_text_with_bg(frame, text2, (x, y + 38), 0.62, (230, 235, 245), 1, bg_alpha=0.28 + alpha * 0.20)


def _timeline_points(rows_by_frame: dict[int, dict[str, Any]], total_frames: int, max_speed: float) -> list[tuple[int, float]]:
    points = []
    for frame_idx in sorted(rows_by_frame):
        row = rows_by_frame[frame_idx]
        speed = ((row.get("speed") or {}).get("smoothed_kmh"))
        if speed is None:
            continue
        try:
            points.append((frame_idx, min(float(speed), max_speed)))
        except (TypeError, ValueError):
            continue
    return points


def _draw_timeline(frame, frame_index: int, total_frames: int, points: list[tuple[int, float]], hits: list[dict[str, Any]], max_speed: float) -> None:
    if total_frames <= 0:
        return
    h, w = frame.shape[:2]
    timeline_h = max(70, int(h * 0.075))
    left = 60
    right = w - 60
    top = h - timeline_h - 18
    bottom = h - 18
    overlay = frame.copy()
    cv2.rectangle(overlay, (left - 14, top - 10), (right + 14, bottom + 8), (5, 8, 16), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    cv2.rectangle(frame, (left, top), (right, bottom), (90, 95, 110), 1)

    if points:
        sampled = points
        if len(points) > (right - left):
            step = max(1, len(points) // max(1, (right - left)))
            sampled = points[::step]
        prev = None
        for frame_no, speed in sampled:
            x = int(left + frame_no / total_frames * (right - left))
            y = int(bottom - (speed / max_speed) * (bottom - top))
            if prev:
                cv2.line(frame, prev, (x, y), _speed_color(speed), 1, cv2.LINE_AA)
            prev = (x, y)

    for hit in hits:
        hit_frame = int(hit.get("frame") or 0)
        if hit_frame <= 0:
            continue
        x = int(left + hit_frame / total_frames * (right - left))
        y = top + 7
        cv2.circle(frame, (x, y), 3, _speed_color(float(hit.get("speed_kmh") or 0)), -1, cv2.LINE_AA)

    cursor_x = int(left + frame_index / total_frames * (right - left))
    cv2.line(frame, (cursor_x, top), (cursor_x, bottom), (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, "Speed timeline", (left, top - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (210, 220, 235), 1, cv2.LINE_AA)


def _open_video_writer(path: str, fps: float, frame_size: tuple[int, int]):
    for codec in ("avc1", "H264", "mp4v"):
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(path, fourcc, fps, frame_size)
        if writer.isOpened():
            return writer
        writer.release()
    raise RuntimeError(f"Unable to create speed overlay video: {path}")


def annotate_video_with_shuttle_speeds(
    input_video_path: str | os.PathLike[str],
    output_video_path: str | os.PathLike[str],
    speeds_jsonl_path: str | os.PathLike[str],
    summary_json_path: str | os.PathLike[str],
    fps: float | None = None,
) -> str:
    """Render shuttle speed overlays onto an existing Good-Badminton output video."""
    input_video_path = str(input_video_path)
    output_video_path = str(output_video_path)
    speeds_jsonl_path = str(speeds_jsonl_path)
    summary_json_path = str(summary_json_path)

    rows_by_frame = _load_speed_rows(speeds_jsonl_path)
    summary = _load_json(summary_json_path)
    hits = summary.get("hits") or []

    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video for speed overlay: {input_video_path}")

    source_fps = float(fps or cap.get(cv2.CAP_PROP_FPS) or summary.get("fps") or 30)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError("Unable to read source video dimensions")

    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    writer = _open_video_writer(output_video_path, source_fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Unable to create speed overlay video: {output_video_path}")

    max_speed = max(500.0, float(summary.get("max_speed_kmh") or 0) * 1.08)
    timeline = _timeline_points(rows_by_frame, total_frames, max_speed)
    rally_messages = _rally_end_messages(summary, source_fps)

    frame_index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_index += 1
            row = rows_by_frame.get(frame_index)
            speed = None
            if row:
                speed = (row.get("speed") or {}).get("smoothed_kmh")
                if speed is not None:
                    speed = float(speed)
            _draw_speed_panel(frame, speed, frame_index)
            _draw_hit_labels(frame, _collect_active_hit_labels(frame_index, hits, source_fps))
            _draw_rally_summary(frame, frame_index, rally_messages, source_fps)
            _draw_timeline(frame, frame_index, total_frames, timeline, hits, max_speed)
            writer.write(frame)
    finally:
        cap.release()
        writer.release()

    if not os.path.exists(output_video_path) or os.path.getsize(output_video_path) <= 0:
        raise RuntimeError("Speed overlay output was not created")
    return output_video_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Overlay shuttle speed data onto a Good-Badminton output video")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--speeds", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()
    print(annotate_video_with_shuttle_speeds(args.input, args.output, args.speeds, args.summary))
