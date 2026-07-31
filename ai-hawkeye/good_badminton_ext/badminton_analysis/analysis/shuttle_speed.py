"""Shuttlecock planar speed analysis for Good-Badminton outputs.

This module reuses Good-Badminton's existing court homography. It reads
``detections.jsonl`` (with ``shuttlecock.image`` pixel points), maps each point
through ``badminton_analysis.court.mapper.CourtMapper``, and writes a
frame-aligned ``speeds.jsonl`` plus match/rally summary.

Important: the resulting speed is a 2D court-plane projection speed, not true
3D shuttle speed.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
COURT_WIDTH_M = 6.1
COURT_LENGTH_M = 13.4
PROJECTION_NOTE = "Planar projected shuttlecock speed from 2D video, not true 3D speed."


@dataclass
class SpeedRow:
    frame: int
    time_sec: float | None
    image: list[float] | None
    court: list[float] | None = None
    interpolated: bool = False
    valid: bool = False
    speed_mps: float | None = None
    speed_kmh: float | None = None
    smoothed_kmh: float | None = None
    outlier: bool = False
    level: str = "none"
    rally_id: int | None = None
    event: dict[str, Any] | None = None


def _read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _safe_float_pair(value: Any, zero_is_none: bool = False) -> list[float] | None:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    if zero_is_none and x == 0.0 and y == 0.0:
        return None
    return [x, y]


def _load_rows(detections_path: str | os.PathLike[str], fps: float) -> list[SpeedRow]:
    rows: list[SpeedRow] = []
    with open(detections_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            frame = int(record.get("frame") or 0)
            if frame <= 0:
                continue
            time_sec = record.get("time_sec")
            if time_sec is None and fps > 0:
                time_sec = frame / fps
            image = _safe_float_pair((record.get("shuttlecock") or {}).get("image"), zero_is_none=True)
            rows.append(SpeedRow(frame=frame, time_sec=float(time_sec) if time_sec is not None else None, image=image))
    rows.sort(key=lambda item: item.frame)
    return rows


def _load_rallies(path: str | os.PathLike[str] | None) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    try:
        payload = _read_json(path)
    except Exception:
        return []
    rallies = payload.get("rallies") or []
    result = []
    for rally in rallies:
        try:
            result.append(
                {
                    "id": int(rally.get("id")),
                    "start_frame": int(rally.get("start_frame")),
                    "end_frame": int(rally.get("end_frame")),
                    "start_sec": float(rally.get("start_sec", 0)),
                    "end_sec": float(rally.get("end_sec", 0)),
                }
            )
        except (TypeError, ValueError):
            continue
    return result


def _rally_id_for_frame(frame: int, rallies: list[dict[str, Any]]) -> int | None:
    for rally in rallies:
        if rally["start_frame"] <= frame <= rally["end_frame"]:
            return rally["id"]
    return None


def _map_points_to_court(rows: list[SpeedRow], corners: list[Any], court_margin_m: float) -> None:
    from badminton_analysis.court.mapper import CourtMapper

    mapper = CourtMapper(corners)
    for row in rows:
        if row.image is None:
            continue
        try:
            court = mapper.image_to_court(row.image)
        except Exception:
            continue
        court_pair = _safe_float_pair(court)
        if court_pair is None:
            continue
        x, y = court_pair
        row.court = [round(x, 4), round(y, 4)]
        row.valid = -court_margin_m <= x <= COURT_WIDTH_M + court_margin_m and -court_margin_m <= y <= COURT_LENGTH_M + court_margin_m


def _interpolate_short_gaps(rows: list[SpeedRow], max_gap: int, fps: float) -> None:
    valid_indices = [idx for idx, row in enumerate(rows) if row.valid and row.court is not None]
    for left_idx, right_idx in zip(valid_indices, valid_indices[1:]):
        missing_count = right_idx - left_idx - 1
        if missing_count <= 0 or missing_count > max_gap:
            continue
        left = rows[left_idx]
        right = rows[right_idx]
        frame_delta = right.frame - left.frame
        if frame_delta <= 0 or frame_delta > max_gap + 1:
            continue
        for idx in range(left_idx + 1, right_idx):
            row = rows[idx]
            if row.court is not None:
                continue
            ratio = (row.frame - left.frame) / frame_delta
            x = left.court[0] + (right.court[0] - left.court[0]) * ratio
            y = left.court[1] + (right.court[1] - left.court[1]) * ratio
            if left.image and right.image:
                ix = left.image[0] + (right.image[0] - left.image[0]) * ratio
                iy = left.image[1] + (right.image[1] - left.image[1]) * ratio
                row.image = [round(ix, 2), round(iy, 2)]
            row.court = [round(x, 4), round(y, 4)]
            row.valid = True
            row.interpolated = True
            if row.time_sec is None and fps > 0:
                row.time_sec = row.frame / fps


def _speed_level(speed_kmh: float | None) -> str:
    if speed_kmh is None:
        return "none"
    if speed_kmh < 150:
        return "green"
    if speed_kmh < 250:
        return "yellow"
    if speed_kmh < 350:
        return "orange"
    return "red"


def _hit_type(speed_kmh: float) -> tuple[str, str]:
    if speed_kmh >= 250:
        return "smash", "杀球"
    if speed_kmh >= 120:
        return "drive", "平抽"
    return "drop", "吊球"


def _infer_player(court: list[float] | None) -> str | None:
    if not court:
        return None
    return "upper" if float(court[1]) < COURT_LENGTH_M / 2 else "lower"


def _calculate_raw_speeds(rows: list[SpeedRow], fps: float, max_speed_kmh: float) -> None:
    previous: SpeedRow | None = None
    for row in rows:
        if not row.valid or row.court is None:
            continue
        if previous is None:
            previous = row
            continue
        frame_delta = row.frame - previous.frame
        if frame_delta <= 0:
            previous = row
            continue
        if row.time_sec is not None and previous.time_sec is not None:
            dt = row.time_sec - previous.time_sec
        else:
            dt = frame_delta / fps if fps > 0 else 0
        if dt <= 0:
            previous = row
            continue
        distance_m = math.hypot(row.court[0] - previous.court[0], row.court[1] - previous.court[1])
        speed_mps = distance_m / dt
        speed_kmh = speed_mps * 3.6
        row.speed_mps = round(speed_mps, 4)
        row.speed_kmh = round(speed_kmh, 3)
        row.outlier = speed_kmh > max_speed_kmh
        row.level = _speed_level(None if row.outlier else speed_kmh)
        previous = row


def _smooth_speeds(rows: list[SpeedRow], window: int) -> None:
    valid_speeds = [None if row.outlier else row.speed_kmh for row in rows]
    half = max(0, int(window) // 2)
    for idx, row in enumerate(rows):
        if row.speed_kmh is None or row.outlier:
            continue
        values = [value for value in valid_speeds[max(0, idx - half): idx + half + 1] if value is not None]
        if values:
            row.smoothed_kmh = round(float(median(values)), 3)
            row.level = _speed_level(row.smoothed_kmh)


def _vectors(rows: list[SpeedRow]) -> dict[int, tuple[float, float]]:
    result: dict[int, tuple[float, float]] = {}
    previous: SpeedRow | None = None
    for row in rows:
        if not row.valid or row.court is None:
            continue
        if previous is not None:
            result[row.frame] = (row.court[0] - previous.court[0], row.court[1] - previous.court[1])
        previous = row
    return result


def _is_direction_reversal(prev_vec: tuple[float, float] | None, cur_vec: tuple[float, float] | None) -> bool:
    if not prev_vec or not cur_vec:
        return False
    prev_len = math.hypot(prev_vec[0], prev_vec[1])
    cur_len = math.hypot(cur_vec[0], cur_vec[1])
    if prev_len < 0.03 or cur_len < 0.03:
        return False
    cos_angle = (prev_vec[0] * cur_vec[0] + prev_vec[1] * cur_vec[1]) / (prev_len * cur_len)
    return cos_angle < -0.42  # roughly > 115 degrees


def _detect_hits(rows: list[SpeedRow], fps: float) -> list[dict[str, Any]]:
    vectors_by_frame = _vectors(rows)
    rows_by_frame = {row.frame: row for row in rows}
    min_gap_frames = max(3, int((fps or 30) * 0.25))
    hits: list[dict[str, Any]] = []
    last_hit_frame = -10**9

    previous_speed: float | None = None
    previous_frame: int | None = None
    for row in rows:
        speed = row.smoothed_kmh
        if speed is None or row.outlier or not row.valid or row.court is None:
            continue
        prev_vec = vectors_by_frame.get(previous_frame) if previous_frame is not None else None
        cur_vec = vectors_by_frame.get(row.frame)
        reversal = _is_direction_reversal(prev_vec, cur_vec)
        surge = previous_speed is not None and speed >= 60 and speed > previous_speed * 1.65 and speed - previous_speed > 35
        local_fast = speed >= 250
        if row.frame - last_hit_frame >= min_gap_frames and ((reversal and speed >= 45) or surge or local_fast):
            hit_type, label = _hit_type(speed)
            hit = {
                "frame": row.frame,
                "time_sec": round(float(row.time_sec if row.time_sec is not None else row.frame / (fps or 30)), 3),
                "speed_kmh": round(speed, 1),
                "type": hit_type,
                "label": label,
                "player": _infer_player(row.court),
                "rally_id": row.rally_id,
                "image": [round(v, 2) for v in row.image] if row.image else None,
                "court": [round(v, 3) for v in row.court],
            }
            hits.append(hit)
            row.event = {"hit": True, **hit}
            last_hit_frame = row.frame
        previous_speed = speed
        previous_frame = row.frame

    for row in rows:
        if row.event is None:
            row.event = {
                "hit": False,
                "type": None,
                "label": None,
                "player": None,
                "rally_id": row.rally_id,
                "speed_kmh": None,
            }
    return hits


def _average(values: Iterable[float]) -> float:
    vals = list(values)
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _data_quality(rows: list[SpeedRow]) -> dict[str, Any]:
    total = len(rows)
    mapped = len([row for row in rows if row.valid and row.court is not None])
    interpolated = len([row for row in rows if row.interpolated])
    outliers = len([row for row in rows if row.outlier])
    speed_samples = len([row for row in rows if row.smoothed_kmh is not None and not row.outlier])
    return {
        "total_frames": total,
        "valid_court_frames": mapped,
        "valid_ratio": round(mapped / total, 4) if total else 0.0,
        "interpolated_frames": interpolated,
        "outlier_frames": outliers,
        "speed_sample_frames": speed_samples,
    }


def _summarize(
    rows: list[SpeedRow],
    hits: list[dict[str, Any]],
    rallies: list[dict[str, Any]],
    fps: float,
) -> dict[str, Any]:
    valid_speeds = [row.smoothed_kmh for row in rows if row.smoothed_kmh is not None and not row.outlier]
    summary_rallies = []
    for rally in rallies:
        rally_speeds = [
            row.smoothed_kmh for row in rows
            if row.smoothed_kmh is not None and not row.outlier and row.rally_id == rally["id"]
        ]
        rally_hits = [hit for hit in hits if hit.get("rally_id") == rally["id"]]
        summary_rallies.append(
            {
                **rally,
                "max_speed_kmh": round(max(rally_speeds), 1) if rally_speeds else 0.0,
                "avg_speed_kmh": _average(rally_speeds),
                "hit_count": len(rally_hits),
                "hits": rally_hits,
            }
        )

    players: dict[str, dict[str, Any]] = {}
    for player in ("upper", "lower"):
        player_hits = [hit for hit in hits if hit.get("player") == player]
        hit_speeds = [float(hit["speed_kmh"]) for hit in player_hits]
        players[player] = {
            "max_speed_kmh": round(max(hit_speeds), 1) if hit_speeds else 0.0,
            "avg_hit_speed_kmh": _average(hit_speeds),
            "hit_count": len(player_hits),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "note": PROJECTION_NOTE,
        "fps": float(fps),
        "max_speed_kmh": round(max(valid_speeds), 1) if valid_speeds else 0.0,
        "avg_speed_kmh": _average(valid_speeds),
        "hit_count": len(hits),
        "hits": hits,
        "rallies": summary_rallies,
        "players": players,
        "data_quality": _data_quality(rows),
    }


def _row_payload(row: SpeedRow) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "frame": row.frame,
        "time_sec": round(float(row.time_sec), 6) if row.time_sec is not None else None,
        "shuttlecock": {
            "image": [round(v, 2) for v in row.image] if row.image else None,
            "court": [round(v, 4) for v in row.court] if row.court else None,
            "interpolated": bool(row.interpolated),
            "valid": bool(row.valid),
        },
        "speed": {
            "mps": round(row.speed_mps, 4) if row.speed_mps is not None else None,
            "kmh": round(row.speed_kmh, 3) if row.speed_kmh is not None else None,
            "smoothed_kmh": round(row.smoothed_kmh, 3) if row.smoothed_kmh is not None else None,
            "outlier": bool(row.outlier),
            "level": row.level,
        },
        "event": row.event or {"hit": False},
    }


def calculate_shuttle_speeds(
    detections_path: str | os.PathLike[str],
    metadata_path: str | os.PathLike[str],
    output_jsonl_path: str | os.PathLike[str],
    summary_path: str | os.PathLike[str] | None = None,
    rally_segments_path: str | os.PathLike[str] | None = None,
    max_speed_kmh: float = 520.0,
    max_interpolation_gap: int = 6,
    smoothing_window: int = 3,
    court_margin_m: float = 1.5,
) -> dict[str, Any]:
    """Calculate planar shuttlecock speeds from Good-Badminton detections.

    Returns the same summary object written to ``summary_path``.
    """
    metadata = _read_json(metadata_path)
    fps = float((metadata.get("video") or {}).get("fps") or 0)
    if fps <= 0:
        raise RuntimeError("metadata.json does not contain a valid video.fps")
    corners = (metadata.get("court") or {}).get("corners")
    if not corners or len(corners) != 4:
        raise RuntimeError("metadata.json does not contain court.corners")

    rows = _load_rows(detections_path, fps)
    rallies = _load_rallies(rally_segments_path)
    for row in rows:
        row.rally_id = _rally_id_for_frame(row.frame, rallies)

    _map_points_to_court(rows, corners, court_margin_m)
    _interpolate_short_gaps(rows, max_interpolation_gap, fps)
    _calculate_raw_speeds(rows, fps, max_speed_kmh)
    _smooth_speeds(rows, smoothing_window)
    hits = _detect_hits(rows, fps)
    summary = _summarize(rows, hits, rallies, fps)

    os.makedirs(os.path.dirname(os.path.abspath(output_jsonl_path)), exist_ok=True)
    with open(output_jsonl_path, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(_row_payload(row), ensure_ascii=False, separators=(",", ":")))
            file.write("\n")

    if summary_path:
        _write_json(summary_path, summary)

    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Calculate shuttlecock planar speeds from Good-Badminton detections")
    parser.add_argument("--detections", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--rallies")
    args = parser.parse_args()
    result = calculate_shuttle_speeds(args.detections, args.metadata, args.output, args.summary, args.rallies)
    print(json.dumps(result, ensure_ascii=False, indent=2))
