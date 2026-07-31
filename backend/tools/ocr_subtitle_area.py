# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

COORDINATE_PATTERN = re.compile(r"\((-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\)")


def parse_raw_subtitle_coordinates(raw_subtitle_path: str | Path) -> list[tuple[int, int, int, int, int]]:
    """
    Parse OCR raw subtitle rows.

    Returns tuples in frame_no, xmin, xmax, ymin, ymax order.
    """
    path = Path(raw_subtitle_path)
    if not path.exists():
        return []

    coordinates = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        try:
            frame_no = int(parts[0])
        except ValueError:
            continue
        match = COORDINATE_PATTERN.search(parts[1])
        if not match:
            continue
        xmin, xmax, ymin, ymax = (int(value) for value in match.groups())
        coordinates.append((frame_no, xmin, xmax, ymin, ymax))
    return coordinates


def build_ocr_subtitle_area_payload(
    raw_subtitle_path: str | Path,
    *,
    video: str | None = None,
) -> dict[str, Any]:
    coordinates = parse_raw_subtitle_coordinates(raw_subtitle_path)
    payload: dict[str, Any] = {
        "video": video,
        "source": "ocr_raw_subtitles",
        "coordinate_order": ["xmin", "xmax", "ymin", "ymax"],
        "box_count": len(coordinates),
        "frame_count": len({item[0] for item in coordinates}),
    }
    if not coordinates:
        payload.update(
            {
                "status": "no_ocr_subtitle",
                "ocr_subtitle_bbox": None,
            }
        )
        return payload

    frame_numbers = [item[0] for item in coordinates]
    payload.update(
        {
            "status": "ok",
            "frame_start": min(frame_numbers),
            "frame_end": max(frame_numbers),
            "ocr_subtitle_bbox": {
                "xmin": min(item[1] for item in coordinates),
                "xmax": max(item[2] for item in coordinates),
                "ymin": min(item[3] for item in coordinates),
                "ymax": max(item[4] for item in coordinates),
            },
        }
    )
    return payload


def save_ocr_subtitle_area_json(
    raw_subtitle_path: str | Path,
    output_path: str | Path,
    *,
    video: str | None = None,
) -> dict[str, Any]:
    payload = build_ocr_subtitle_area_payload(raw_subtitle_path, video=video)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
