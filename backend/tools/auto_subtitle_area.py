# -*- coding: utf-8 -*-
"""
自动识别视频硬字幕的大致 ROI。

该模块只负责 ROI 估计和 JSON 数据生成，不直接调度 OCR/SRT 流程。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

METHOD_VERSION = "auto-roi-v1"
Coordinate = Tuple[int, int, int, int]  # xmin, xmax, ymin, ymax

TPR_NOISE_MAX = 0.10
TPR_PRIMARY_MIN = 0.20
TPR_PRIMARY_MAX = 0.75


@dataclass
class SubtitleAreaCandidate:
    roi: Coordinate
    score: float
    hits: int
    frame_hits: int
    time_bucket_hits: int
    orientation: str = "horizontal"
    temporal_presence_rate: float = 0.0
    temporal_presence_score: float = 0.0
    temporal_presence_label: str = "unknown"
    excluded: bool = False
    exclusion_reason: str = ""

    def to_json_dict(self) -> Dict[str, Any]:
        xmin, xmax, ymin, ymax = self.roi
        data = {
            "roi": {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax},
            "score": round(self.score, 4),
            "hits": self.hits,
            "frame_hits": self.frame_hits,
            "time_bucket_hits": self.time_bucket_hits,
            "orientation": self.orientation,
            "temporal_presence_rate": round(self.temporal_presence_rate, 4),
            "temporal_presence_score": round(self.temporal_presence_score, 4),
            "temporal_presence_label": self.temporal_presence_label,
            "excluded": self.excluded,
        }
        if self.exclusion_reason:
            data["exclusion_reason"] = self.exclusion_reason
        return data


@dataclass
class AutoSubtitleAreaResult:
    video: str
    width: int
    height: int
    fps: float
    frame_count: int
    subtitle_roi: Optional[Coordinate]
    confidence: float
    sampled_frames: int
    status: str
    reason: str = ""
    candidates: List[SubtitleAreaCandidate] = field(default_factory=list)
    method_version: str = METHOD_VERSION
    selected_candidate_index: Optional[int] = None
    text_match_score: Optional[float] = None

    def to_subtitle_area(self):
        if self.subtitle_roi is None:
            return None
        from backend.bean.subtitle_area import SubtitleArea

        xmin, xmax, ymin, ymax = self.subtitle_roi
        return SubtitleArea(ymin, ymax, xmin, xmax)

    def iter_candidate_subtitle_areas(self, min_confidence: float = 0.0, max_candidates: Optional[int] = None):
        from backend.bean.subtitle_area import SubtitleArea

        emitted = 0
        for index, candidate in enumerate(self.candidates):
            if candidate.excluded or candidate.score < min_confidence:
                continue
            xmin, xmax, ymin, ymax = _pad_roi(candidate.roi, self.width, self.height, candidate.orientation)
            yield index, candidate, SubtitleArea(ymin, ymax, xmin, xmax)
            emitted += 1
            if max_candidates is not None and emitted >= max_candidates:
                return

    def to_json_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "video": self.video,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "confidence": round(self.confidence, 4),
            "sampled_frames": self.sampled_frames,
            "method_version": self.method_version,
            "status": self.status,
            "candidates": [candidate.to_json_dict() for candidate in self.candidates],
        }
        if self.selected_candidate_index is not None:
            data["selected_candidate_index"] = self.selected_candidate_index
        if self.text_match_score is not None:
            data["text_match_score"] = round(self.text_match_score, 4)
        if self.subtitle_roi is not None:
            xmin, xmax, ymin, ymax = self.subtitle_roi
            data["subtitle_roi"] = {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}
        if self.reason:
            data["reason"] = self.reason
        return data

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> "AutoSubtitleAreaResult":
        roi_data = data.get("subtitle_roi")
        roi = None
        if roi_data:
            roi = (
                int(roi_data["xmin"]),
                int(roi_data["xmax"]),
                int(roi_data["ymin"]),
                int(roi_data["ymax"]),
            )
        candidates = []
        for candidate_data in data.get("candidates", []):
            candidate_roi = candidate_data.get("roi", {})
            candidates.append(
                SubtitleAreaCandidate(
                    roi=(
                        int(candidate_roi.get("xmin", 0)),
                        int(candidate_roi.get("xmax", 0)),
                        int(candidate_roi.get("ymin", 0)),
                        int(candidate_roi.get("ymax", 0)),
                    ),
                    score=float(candidate_data.get("score", 0)),
                    hits=int(candidate_data.get("hits", 0)),
                    frame_hits=int(candidate_data.get("frame_hits", 0)),
                    time_bucket_hits=int(candidate_data.get("time_bucket_hits", 0)),
                    orientation=str(candidate_data.get("orientation", "horizontal")),
                    temporal_presence_rate=float(candidate_data.get("temporal_presence_rate", 0)),
                    temporal_presence_score=float(candidate_data.get("temporal_presence_score", 0)),
                    temporal_presence_label=str(candidate_data.get("temporal_presence_label", "unknown")),
                    excluded=bool(candidate_data.get("excluded", False)),
                    exclusion_reason=str(candidate_data.get("exclusion_reason", "")),
                )
            )
        return cls(
            video=str(data.get("video", "")),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            fps=float(data.get("fps", 0)),
            frame_count=int(data.get("frame_count", 0)),
            subtitle_roi=roi,
            confidence=float(data.get("confidence", 0)),
            sampled_frames=int(data.get("sampled_frames", 0)),
            status=str(data.get("status", "unknown")),
            reason=str(data.get("reason", "")),
            candidates=candidates,
            method_version=str(data.get("method_version", METHOD_VERSION)),
            selected_candidate_index=(
                int(data["selected_candidate_index"]) if data.get("selected_candidate_index") is not None else None
            ),
            text_match_score=float(data["text_match_score"]) if data.get("text_match_score") is not None else None,
        )


def detect_auto_subtitle_area(
    video_path: str | Path,
    *,
    samples: Optional[int] = None,
    max_samples: int = 1000,
    min_confidence: float = 0.5,
    detector: Optional[Any] = None,
    show_progress: bool = False,
    progress_desc: Optional[str] = None,
) -> AutoSubtitleAreaResult:
    """
    自动识别单个视频的字幕 ROI。

    返回结果中的 ROI 为像素坐标，格式为 (xmin, xmax, ymin, ymax)。
    """
    import cv2
    from backend.tools.subtitle_detect import SubtitleDetect
    from backend.tools.video_metadata import read_video_metadata

    video_path = Path(video_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return _empty_result(video_path, "error", "video could not be opened")

    try:
        metadata = read_video_metadata(cap=cap)
        width = metadata.width
        height = metadata.height
        fps = metadata.fps
        frame_count = metadata.frame_count
        if width <= 0 or height <= 0 or frame_count <= 0:
            return AutoSubtitleAreaResult(
                video=video_path.name,
                width=width,
                height=height,
                fps=fps,
                frame_count=frame_count,
                subtitle_roi=None,
                confidence=0,
                sampled_frames=0,
                status="error",
                reason=f"invalid video metadata: width={width}, height={height}, frame_count={frame_count}",
            )

        sample_frames = build_sample_frame_numbers(frame_count, fps, samples=samples, max_samples=max_samples)
        detector = detector or SubtitleDetect()
        observations, detect_errors = _collect_text_observations(
            cap,
            sample_frames,
            frame_count,
            width,
            height,
            detector,
            show_progress=show_progress,
            progress_desc=progress_desc or f"ROI {video_path.name}",
        )
    finally:
        cap.release()

    candidates = _build_candidates(observations, width, height, max(len(sample_frames), 1))
    if not candidates:
        if detect_errors and len(detect_errors) >= len(sample_frames):
            return AutoSubtitleAreaResult(
                video=video_path.name,
                width=width,
                height=height,
                fps=fps,
                frame_count=frame_count,
                subtitle_roi=None,
                confidence=0,
                sampled_frames=len(sample_frames),
                status="error",
                reason=f"text detection failed for all sampled frames: {detect_errors[-1]}",
            )
        reason = "no stable subtitle band found"
        if detect_errors:
            reason = f"{reason}; text detection failed on {len(detect_errors)} sampled frames"
        return AutoSubtitleAreaResult(
            video=video_path.name,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=len(sample_frames),
            status="low_confidence",
            reason=reason,
        )

    eligible_candidates = [candidate for candidate in candidates if not candidate.excluded]
    if not eligible_candidates:
        return AutoSubtitleAreaResult(
            video=video_path.name,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            subtitle_roi=None,
            confidence=candidates[0].score,
            sampled_frames=len(sample_frames),
            status="low_confidence",
            reason="all candidate bands were rejected by temporal presence rate (TPR)",
            candidates=candidates[:5],
        )

    best = eligible_candidates[0]
    padded_roi = _pad_roi(best.roi, width, height, best.orientation)
    status = "ok" if best.score >= min_confidence else "low_confidence"
    reason = "" if status == "ok" else "best subtitle band confidence below threshold"
    return AutoSubtitleAreaResult(
        video=video_path.name,
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        subtitle_roi=padded_roi if status == "ok" else None,
        confidence=best.score,
        sampled_frames=len(sample_frames),
        status=status,
        reason=reason,
        candidates=candidates[:5],
        selected_candidate_index=0,
    )


def build_sample_frame_numbers(
    frame_count: int,
    fps: float,
    *,
    samples: Optional[int] = None,
    max_samples: int = 1000,
) -> List[int]:
    if frame_count <= 0:
        return []
    if samples is None:
        duration_seconds = frame_count / fps if fps > 0 else 0
        if duration_seconds and duration_seconds < 5 * 60:
            samples = 60
        elif duration_seconds and duration_seconds < 30 * 60:
            samples = 180
        else:
            samples = 240

    sample_count = max(1, min(int(samples), int(max_samples), frame_count))
    if sample_count == 1:
        return [max(0, frame_count // 2)]

    # 避开极端首尾帧，同时保持全片均匀覆盖。
    start = 0
    end = frame_count - 1
    if frame_count > 20:
        start = int(frame_count * 0.02)
        end = max(start, int(frame_count * 0.98))
    step = (end - start) / float(sample_count - 1)
    return sorted({max(0, min(frame_count - 1, int(round(start + i * step)))) for i in range(sample_count)})


def save_result_json(result: AutoSubtitleAreaResult, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.write_text(json.dumps(result.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_result_json(input_path: str | Path) -> AutoSubtitleAreaResult:
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return AutoSubtitleAreaResult.from_json_dict(data)


def _empty_result(video_path: Path, status: str, reason: str) -> AutoSubtitleAreaResult:
    return AutoSubtitleAreaResult(
        video=video_path.name,
        width=0,
        height=0,
        fps=0,
        frame_count=0,
        subtitle_roi=None,
        confidence=0,
        sampled_frames=0,
        status=status,
        reason=reason,
    )


def _collect_text_observations(
    cap: Any,
    sample_frames: Sequence[int],
    frame_count: int,
    width: int,
    height: int,
    detector: Any,
    *,
    show_progress: bool = False,
    progress_desc: str = "ROI",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    import cv2
    from backend.tools.ocr import get_coordinates

    observations: List[Dict[str, Any]] = []
    errors: List[str] = []
    bucket_count = 10
    frame_iterable = sample_frames
    progress_bar = None
    if show_progress:
        from tqdm import tqdm

        progress_bar = tqdm(total=len(sample_frames), desc=progress_desc, unit="frame", position=0, leave=True)

    try:
        for frame_no in frame_iterable:
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                ok, frame = cap.read()
                if not ok:
                    continue
                try:
                    dt_boxes, _ = detector.detect_subtitle(frame)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
                    continue
                if hasattr(dt_boxes, "tolist"):
                    dt_boxes = dt_boxes.tolist()
                for coordinate in _filter_coordinates(get_coordinates(dt_boxes), width, height):
                    bucket = min(bucket_count - 1, int((frame_no / max(frame_count - 1, 1)) * bucket_count))
                    observations.append({"frame_no": frame_no, "bucket": bucket, "coordinate": coordinate})
            finally:
                if progress_bar is not None:
                    progress_bar.update(1)
    finally:
        if progress_bar is not None:
            progress_bar.close()
    return observations, errors


def _filter_coordinates(coordinates: Iterable[Coordinate], width: int, height: int) -> Iterable[Coordinate]:
    for xmin, xmax, ymin, ymax in coordinates:
        xmin, xmax = sorted((int(xmin), int(xmax)))
        ymin, ymax = sorted((int(ymin), int(ymax)))
        box_w = xmax - xmin
        box_h = ymax - ymin
        if box_w <= 0 or box_h <= 0:
            continue
        width_ratio = box_w / width
        height_ratio = box_h / height
        center_x = (xmin + xmax) / 2
        center_y = (ymin + ymax) / 2

        if width_ratio < 0.02 or height_ratio < 0.008:
            continue
        if width_ratio > 0.96:
            continue
        if height_ratio > 0.18 and width_ratio > 0.30:
            continue
        # 过滤常见角落水印、台标、计时器。
        in_side_corner = center_x < width * 0.18 or center_x > width * 0.82
        in_top_or_bottom = center_y < height * 0.25 or center_y > height * 0.90
        if in_side_corner and in_top_or_bottom and width_ratio < 0.12 and height_ratio < 0.018:
            continue
        if center_y < height * 0.15 and not in_side_corner and width_ratio < 0.35:
            continue
        yield (xmin, xmax, ymin, ymax)


def _build_candidates(
    observations: Sequence[Dict[str, Any]],
    width: int,
    height: int,
    sampled_frame_count: int,
) -> List[SubtitleAreaCandidate]:
    if not observations:
        return []

    horizontal_tolerance = max(36, int(height * 0.08))
    vertical_tolerance = max(28, int(width * 0.06))
    horizontal_clusters = _cluster_observations_by_axis(observations, "y", horizontal_tolerance)
    vertical_clusters = _cluster_observations_by_axis(observations, "x", vertical_tolerance)

    candidates = [
        _score_horizontal_cluster(cluster, width, height, sampled_frame_count, horizontal_tolerance)
        for cluster in horizontal_clusters
    ]
    candidates.extend(
        _score_vertical_cluster(cluster, width, height, sampled_frame_count, vertical_tolerance)
        for cluster in vertical_clusters
    )
    return sorted(candidates, key=lambda candidate: (candidate.excluded, -candidate.score))


def _cluster_observations_by_axis(
    observations: Sequence[Dict[str, Any]], axis: str, tolerance: int
) -> List[List[Dict[str, Any]]]:
    center_fn = _center_y if axis == "y" else _center_x
    sorted_observations = sorted(observations, key=lambda item: center_fn(item["coordinate"]))
    clusters: List[List[Dict[str, Any]]] = []
    for observation in sorted_observations:
        if not clusters:
            clusters.append([observation])
            continue
        current_center = center_fn(observation["coordinate"])
        cluster_center = mean(center_fn(item["coordinate"]) for item in clusters[-1])
        if abs(current_center - cluster_center) <= tolerance:
            clusters[-1].append(observation)
        else:
            clusters.append([observation])
    return clusters


def _score_horizontal_cluster(
    cluster: Sequence[Dict[str, Any]],
    width: int,
    height: int,
    sampled_frame_count: int,
    tolerance: int,
) -> SubtitleAreaCandidate:
    coordinates = [item["coordinate"] for item in cluster]
    xmin = min(coord[0] for coord in coordinates)
    xmax = max(coord[1] for coord in coordinates)
    ymin = min(coord[2] for coord in coordinates)
    ymax = max(coord[3] for coord in coordinates)
    frame_hits = len({item["frame_no"] for item in cluster})
    bucket_hits = len({item["bucket"] for item in cluster})
    temporal_presence_rate = frame_hits / max(sampled_frame_count, 1)
    temporal_presence_score, temporal_presence_label, excluded, exclusion_reason = _score_temporal_presence(
        temporal_presence_rate
    )
    centers_y = [_center_y(coord) for coord in coordinates]
    center_x = (xmin + xmax) / 2
    width_ratio = (xmax - xmin) / width
    center_score = 1 - min(abs(center_x - width / 2) / (width / 2), 1)
    stability_score = 1 - min((pstdev(centers_y) if len(centers_y) > 1 else 0) / max(tolerance, 1), 1)
    hit_score = min(frame_hits / max(5, sampled_frame_count * 0.12), 1)
    temporal_score = min(bucket_hits / 6, 1)
    width_score = _piecewise_width_score(width_ratio)
    y_center = (ymin + ymax) / 2
    y_score = 1.0 if y_center >= height * 0.45 else 0.72
    score = (
        hit_score * 0.22
        + temporal_score * 0.16
        + temporal_presence_score * 0.22
        + stability_score * 0.16
        + center_score * 0.12
        + width_score * 0.08
        + y_score * 0.04
    )
    return SubtitleAreaCandidate(
        roi=(xmin, xmax, ymin, ymax),
        score=max(0, min(score, 1)),
        hits=len(cluster),
        frame_hits=frame_hits,
        time_bucket_hits=bucket_hits,
        orientation="horizontal",
        temporal_presence_rate=temporal_presence_rate,
        temporal_presence_score=temporal_presence_score,
        temporal_presence_label=temporal_presence_label,
        excluded=excluded,
        exclusion_reason=exclusion_reason,
    )


def _score_vertical_cluster(
    cluster: Sequence[Dict[str, Any]],
    width: int,
    height: int,
    sampled_frame_count: int,
    tolerance: int,
) -> SubtitleAreaCandidate:
    coordinates = [item["coordinate"] for item in cluster]
    xmin = min(coord[0] for coord in coordinates)
    xmax = max(coord[1] for coord in coordinates)
    ymin = min(coord[2] for coord in coordinates)
    ymax = max(coord[3] for coord in coordinates)
    frame_hits = len({item["frame_no"] for item in cluster})
    bucket_hits = len({item["bucket"] for item in cluster})
    temporal_presence_rate = frame_hits / max(sampled_frame_count, 1)
    temporal_presence_score, temporal_presence_label, excluded, exclusion_reason = _score_temporal_presence(
        temporal_presence_rate
    )
    centers_x = [_center_x(coord) for coord in coordinates]
    center_x = (xmin + xmax) / 2
    width_ratio = (xmax - xmin) / width
    height_ratio = (ymax - ymin) / height
    side_score = 1.0 if center_x <= width * 0.28 or center_x >= width * 0.72 else 0.62
    stability_score = 1 - min((pstdev(centers_x) if len(centers_x) > 1 else 0) / max(tolerance, 1), 1)
    hit_score = min(frame_hits / max(5, sampled_frame_count * 0.12), 1)
    temporal_score = min(bucket_hits / 6, 1)
    narrow_score = _piecewise_vertical_width_score(width_ratio)
    height_score = _piecewise_height_score(height_ratio)
    score = (
        hit_score * 0.20
        + temporal_score * 0.14
        + temporal_presence_score * 0.20
        + stability_score * 0.16
        + side_score * 0.12
        + narrow_score * 0.08
        + height_score * 0.10
    )
    return SubtitleAreaCandidate(
        roi=(xmin, xmax, ymin, ymax),
        score=max(0, min(score, 1)),
        hits=len(cluster),
        frame_hits=frame_hits,
        time_bucket_hits=bucket_hits,
        orientation="vertical",
        temporal_presence_rate=temporal_presence_rate,
        temporal_presence_score=temporal_presence_score,
        temporal_presence_label=temporal_presence_label,
        excluded=excluded,
        exclusion_reason=exclusion_reason,
    )


def _score_temporal_presence(tpr: float) -> Tuple[float, str, bool, str]:
    if tpr < TPR_NOISE_MAX:
        return 0.0, "random_noise_or_background_text", True, "TPR < 10%"
    if TPR_PRIMARY_MIN <= tpr <= TPR_PRIMARY_MAX:
        return 1.0, "primary_subtitle", False, ""
    if tpr < TPR_PRIMARY_MIN:
        progress = (tpr - TPR_NOISE_MAX) / max(TPR_PRIMARY_MIN - TPR_NOISE_MAX, 0.001)
        return 0.35 + max(0.0, min(progress, 1.0)) * 0.40, "low_presence", False, ""

    progress = (tpr - TPR_PRIMARY_MAX) / max(1.0 - TPR_PRIMARY_MAX, 0.001)
    return 0.75 - max(0.0, min(progress, 1.0)) * 0.50, "high_presence", False, ""


def _piecewise_width_score(width_ratio: float) -> float:
    if 0.25 <= width_ratio <= 0.90:
        return 1
    if 0.12 <= width_ratio < 0.25:
        return 0.75
    if 0.90 < width_ratio <= 0.96:
        return 0.65
    return 0.35


def _piecewise_vertical_width_score(width_ratio: float) -> float:
    if 0.03 <= width_ratio <= 0.22:
        return 1
    if 0.22 < width_ratio <= 0.35:
        return 0.70
    if 0.015 <= width_ratio < 0.03:
        return 0.65
    return 0.35


def _piecewise_height_score(height_ratio: float) -> float:
    if 0.28 <= height_ratio <= 0.95:
        return 1
    if 0.16 <= height_ratio < 0.28:
        return 0.72
    if 0.95 < height_ratio <= 1.0:
        return 0.65
    return 0.35


def _pad_roi(roi: Coordinate, width: int, height: int, orientation: str = "horizontal") -> Coordinate:
    xmin, xmax, ymin, ymax = roi
    if orientation == "vertical":
        x_pad = max(12, int(width * 0.025))
        y_pad = min(100, max(40, int(height * 0.06)))
    else:
        x_pad = max(16, int(width * 0.07))
        y_pad = min(80, max(30, int(height * 0.05)))
    xmin = max(0, xmin - x_pad)
    xmax = min(width, xmax + x_pad)
    ymin = max(0, ymin - y_pad)
    ymax = min(height, ymax + y_pad)

    if orientation == "vertical":
        min_height = min(height, max(140, int(height * 0.25)))
    else:
        min_height = min(height, max(80, int(height * 0.10)))
    if ymax - ymin < min_height:
        extra = min_height - (ymax - ymin)
        ymin = max(0, ymin - math.ceil(extra / 2))
        ymax = min(height, ymax + math.floor(extra / 2))
        if ymax - ymin < min_height and ymin == 0:
            ymax = min(height, min_height)
        elif ymax - ymin < min_height and ymax == height:
            ymin = max(0, height - min_height)

    return (int(xmin), int(xmax), int(ymin), int(ymax))


def _center_y(coordinate: Coordinate) -> float:
    return (coordinate[2] + coordinate[3]) / 2


def _center_x(coordinate: Coordinate) -> float:
    return (coordinate[0] + coordinate[1]) / 2


__all__ = [
    "AutoSubtitleAreaResult",
    "SubtitleAreaCandidate",
    "build_sample_frame_numbers",
    "detect_auto_subtitle_area",
    "load_result_json",
    "save_result_json",
]
