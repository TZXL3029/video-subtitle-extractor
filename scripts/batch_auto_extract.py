# -*- coding: utf-8 -*-
"""
批量自动识别字幕 ROI，并复用现有 VideoSubFinder + OCR + SRT 流程。
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".webm")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch auto extract hard subtitles to SRT.")
    parser.add_argument("inputs", nargs="+", help="Video files or directories.")
    parser.add_argument("--recursive", action="store_true", help="Scan input directories recursively.")
    parser.add_argument("--extensions", default=",".join(DEFAULT_EXTENSIONS), help="Comma-separated video extensions.")
    parser.add_argument("--force-roi", action="store_true", help="Regenerate *.subtitle_area.json.")
    parser.add_argument("--force-srt", action="store_true", help="Regenerate existing *.srt files.")
    parser.add_argument("--samples", type=int, default=None, help="Exact frame sample count for ROI detection.")
    parser.add_argument("--max-samples", type=int, default=1000, help="Upper bound of sampled frames per video.")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Minimum ROI confidence required to run OCR.")
    parser.add_argument("--ocr-drop-score", type=int, default=70, help="Discard OCR text below this confidence percentage.")
    parser.add_argument("--no-roi-progress", action="store_true", help="Hide ROI frame sampling progress bars.")
    parser.add_argument(
        "--vsf-decoder",
        default="ffmpeg",
        choices=["ffmpeg", "opencv"],
        help="VideoSubFinder video decoder. Batch mode defaults to ffmpeg to avoid OpenCV zero-size-frame popups.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.ocr_drop_score <= 100:
        raise SystemExit("--ocr-drop-score must be between 0 and 100")
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")
    extensions = normalize_extensions(args.extensions)
    video_paths = collect_video_paths(args.inputs, extensions, recursive=args.recursive)
    if not video_paths:
        logging.error("No video files found.")
        return 2

    summary = {"total": len(video_paths), "success": 0, "skipped": 0, "failed": 0, "low_confidence": 0}
    for video_path in video_paths:
        outcome = process_video(video_path, args)
        summary[outcome] += 1

    logging.info(
        "Batch finished: total=%s success=%s skipped=%s low_confidence=%s failed=%s",
        summary["total"],
        summary["success"],
        summary["skipped"],
        summary["low_confidence"],
        summary["failed"],
    )
    return 1 if summary["failed"] else 0


def normalize_extensions(value: str) -> Sequence[str]:
    return tuple(ext if ext.startswith(".") else f".{ext}" for ext in (item.strip().lower() for item in value.split(",")) if ext)


def collect_video_paths(inputs: Iterable[str], extensions: Sequence[str], *, recursive: bool) -> List[Path]:
    videos = []
    for input_value in inputs:
        path = Path(input_value)
        if path.is_file() and path.suffix.lower() in extensions:
            videos.append(path)
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            videos.extend(item for item in path.glob(pattern) if item.is_file() and item.suffix.lower() in extensions)
        else:
            logging.warning("Input not found or unsupported: %s", path)
    return sorted({path.resolve() for path in videos})


def process_video(video_path: Path, args: argparse.Namespace) -> str:
    roi_path = subtitle_area_json_path(video_path)
    srt_path = video_path.with_suffix(".srt")

    if srt_path.exists() and not args.force_srt:
        logging.info("Skip existing SRT: %s", srt_path)
        return "skipped"

    try:
        result = get_or_detect_roi(video_path, roi_path, args)
    except Exception as exc:
        write_error_roi(video_path, roi_path, exc)
        logging.exception("ROI detection failed: %s", video_path)
        return "failed"

    subtitle_area = result.to_subtitle_area()
    if result.status == "error":
        logging.error("ROI detection error: %s reason=%s", video_path, result.reason)
        return "failed"
    if result.status != "ok" or subtitle_area is None or result.confidence < args.min_confidence:
        logging.warning("Low-confidence ROI skipped: %s confidence=%.4f reason=%s", video_path, result.confidence, result.reason)
        return "low_confidence"

    try:
        from backend.main import SubtitleExtractor
        from backend.config import config
        from backend.tools.constant import VideoSubFinderDecoder

        config.dropScore.value = args.ocr_drop_score
        extractor = SubtitleExtractor(str(video_path))
        extractor.sub_area = subtitle_area
        extractor.scan_strategy = "vsf"
        extractor.vsf_decoder = VideoSubFinderDecoder.FFMPEG if args.vsf_decoder == "ffmpeg" else VideoSubFinderDecoder.OPENCV
        extractor.run()
        logging.info("SRT generated: %s", extractor.subtitle_output_path)
        return "success"
    except Exception:
        logging.exception("Subtitle extraction failed: %s", video_path)
        return "failed"


def get_or_detect_roi(video_path: Path, roi_path: Path, args: argparse.Namespace):
    from backend.tools.auto_subtitle_area import detect_auto_subtitle_area, load_result_json, save_result_json

    if roi_path.exists() and not args.force_roi:
        result = load_result_json(roi_path)
        logging.info("Reuse ROI JSON: %s", roi_path)
        return result

    logging.info("Detect ROI: %s", video_path)
    result = detect_auto_subtitle_area(
        video_path,
        samples=args.samples,
        max_samples=args.max_samples,
        min_confidence=args.min_confidence,
        show_progress=not args.no_roi_progress,
        progress_desc=f"ROI {video_path.name}",
    )
    save_result_json(result, roi_path)
    logging.info("ROI JSON saved: %s status=%s confidence=%.4f", roi_path, result.status, result.confidence)
    return result


def subtitle_area_json_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}.subtitle_area.json")


def write_error_roi(video_path: Path, roi_path: Path, exc: Exception) -> None:
    payload = {
        "video": video_path.name,
        "status": "error",
        "confidence": 0,
        "sampled_frames": 0,
        "method_version": "auto-roi-v1",
        "reason": str(exc),
    }
    roi_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    raise SystemExit(main())
