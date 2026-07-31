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
    merge_overlap_threshold: float = 0.5,
    merge_max_size_ratio: float = 3.0,
) -> dict[str, Any]:
    coordinates = parse_raw_subtitle_coordinates(raw_subtitle_path)
    merge_overlap_threshold = max(0.0, min(1.0, float(merge_overlap_threshold)))
    merge_max_size_ratio = max(1.0, float(merge_max_size_ratio))
    payload: dict[str, Any] = {
        "video": video,
        "source": "ocr_raw_subtitles",
        "coordinate_order": ["xmin", "xmax", "ymin", "ymax"],
        "overlap_metric": "intersection_area/min_box_area",
        "merge_overlap_threshold": merge_overlap_threshold,
        "merge_max_size_ratio": merge_max_size_ratio,
        "box_count": len(coordinates),
        "frame_count": len({item[0] for item in coordinates}),
    }
    if not coordinates:
        payload.update(
            {
                "status": "no_ocr_subtitle",
                "ocr_subtitle_bboxes": [],
            }
        )
        return payload

    frame_numbers = [item[0] for item in coordinates]
    merged_boxes = merge_overlapping_coordinates(
        coordinates,
        overlap_threshold=merge_overlap_threshold,
        max_size_ratio=merge_max_size_ratio,
    )
    payload.update(
        {
            "status": "ok",
            "frame_start": min(frame_numbers),
            "frame_end": max(frame_numbers),
            "ocr_subtitle_bboxes": merged_boxes,
        }
    )
    return payload


def save_ocr_subtitle_area_json(
    raw_subtitle_path: str | Path,
    output_path: str | Path,
    *,
    video: str | None = None,
    merge_overlap_threshold: float = 0.5,
    merge_max_size_ratio: float = 3.0,
) -> dict[str, Any]:
    payload = build_ocr_subtitle_area_payload(
        raw_subtitle_path,
        video=video,
        merge_overlap_threshold=merge_overlap_threshold,
        merge_max_size_ratio=merge_max_size_ratio,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def merge_overlapping_coordinates(
    coordinates: list[tuple[int, int, int, int, int]],
    *,
    overlap_threshold: float,
    max_size_ratio: float,
) -> list[dict[str, Any]]:
    if not coordinates:
        return []

    parent = list(range(len(coordinates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for i in range(len(coordinates)):
        for j in range(i + 1, len(coordinates)):
            if should_merge_coordinates(
                coordinates[i],
                coordinates[j],
                overlap_threshold=overlap_threshold,
                max_size_ratio=max_size_ratio,
            ):
                union(i, j)

    clusters: dict[int, list[tuple[int, int, int, int, int]]] = {}
    for index, coordinate in enumerate(coordinates):
        clusters.setdefault(find(index), []).append(coordinate)

    merged = []
    for items in clusters.values():
        frame_numbers = [item[0] for item in items]
        merged.append(
            {
                "xmin": min(item[1] for item in items),
                "xmax": max(item[2] for item in items),
                "ymin": min(item[3] for item in items),
                "ymax": max(item[4] for item in items),
                "box_count": len(items),
                "frame_count": len(set(frame_numbers)),
                "frame_start": min(frame_numbers),
                "frame_end": max(frame_numbers),
            }
        )

    merged.sort(key=lambda item: (item["ymin"], item["xmin"], item["ymax"], item["xmax"]))
    for index, item in enumerate(merged, start=1):
        item["index"] = index
    return merged


def should_merge_coordinates(
    first: tuple[int, int, int, int, int],
    second: tuple[int, int, int, int, int],
    *,
    overlap_threshold: float,
    max_size_ratio: float,
) -> bool:
    return (
        coordinate_overlap_ratio(first, second) >= overlap_threshold
        and coordinate_size_ratio(first, second) <= max_size_ratio
    )


def coordinate_overlap_ratio(
    first: tuple[int, int, int, int, int],
    second: tuple[int, int, int, int, int],
) -> float:
    _, first_xmin, first_xmax, first_ymin, first_ymax = first
    _, second_xmin, second_xmax, second_ymin, second_ymax = second
    inter_xmin = max(first_xmin, second_xmin)
    inter_ymin = max(first_ymin, second_ymin)
    inter_xmax = min(first_xmax, second_xmax)
    inter_ymax = min(first_ymax, second_ymax)
    inter_area = max(0, inter_xmax - inter_xmin) * max(0, inter_ymax - inter_ymin)
    first_area = max(0, first_xmax - first_xmin) * max(0, first_ymax - first_ymin)
    second_area = max(0, second_xmax - second_xmin) * max(0, second_ymax - second_ymin)
    smaller_area = min(first_area, second_area)
    if smaller_area <= 0:
        return 0.0
    return inter_area / smaller_area


def coordinate_size_ratio(
    first: tuple[int, int, int, int, int],
    second: tuple[int, int, int, int, int],
) -> float:
    _, first_xmin, first_xmax, first_ymin, first_ymax = first
    _, second_xmin, second_xmax, second_ymin, second_ymax = second
    first_width = max(1, first_xmax - first_xmin)
    first_height = max(1, first_ymax - first_ymin)
    second_width = max(1, second_xmax - second_xmin)
    second_height = max(1, second_ymax - second_ymin)
    width_ratio = max(first_width, second_width) / min(first_width, second_width)
    height_ratio = max(first_height, second_height) / min(first_height, second_height)
    return max(width_ratio, height_ratio)
