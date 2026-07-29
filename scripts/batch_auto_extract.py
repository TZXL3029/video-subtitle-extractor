# -*- coding: utf-8 -*-
"""
批量自动识别字幕 ROI，并复用现有 VideoSubFinder + OCR + SRT 流程。
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".webm")
LABEL_COVERAGE_EARLY_STOP = 0.90
TEXT_MATCH_LOW_CONFIDENCE_THRESHOLD = 0.50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch auto extract hard subtitles to SRT.")
    parser.add_argument("inputs", nargs="*", help="Video files or directories.")
    parser.add_argument("-i", "--input", dest="input_paths", nargs="+", default=[], help="Video files or directories.")
    parser.add_argument(
        "-o",
        "--output",
        dest="output_dir",
        default=None,
        help="Directory for generated *.srt and *.subtitle_area.json files. Defaults to each video's directory.",
    )
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
        "--label-config-dir",
        default="D:/autoCut/autocut/label_configs",
        help="Directory containing standard action label JSON files for multi-ROI candidate text matching.",
    )
    parser.add_argument(
        "--vsf-decoder",
        default="opencv",
        choices=["ffmpeg", "opencv"],
        help="VideoSubFinder video decoder. Batch mode defaults to opencv after compatibility transcoding, then falls back to ffmpeg.",
    )
    parser.add_argument(
        "--no-vsf-transcode",
        action="store_true",
        help="Disable the compatibility transcode before VideoSubFinder.",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.ocr_drop_score <= 100:
        raise SystemExit("--ocr-drop-score must be between 0 and 100")
    args.inputs = [*args.inputs, *args.input_paths]
    if not args.inputs:
        raise SystemExit("No input specified. Use positional inputs or -i/--input.")
    args.output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")
    extensions = normalize_extensions(args.extensions)
    video_paths = collect_video_paths(args.inputs, extensions, recursive=args.recursive)
    if not video_paths:
        logging.error("No video files found.")
        return 2
    args.label_matcher = load_label_matcher(args.label_config_dir)

    summary = {
        "total": len(video_paths),
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "low_confidence": 0,
        "no_subtitle": 0,
    }
    for video_path in video_paths:
        outcome = process_video(video_path, args)
        summary[outcome] += 1

    logging.info(
        "Batch finished: total=%s success=%s skipped=%s low_confidence=%s no_subtitle=%s failed=%s",
        summary["total"],
        summary["success"],
        summary["skipped"],
        summary["low_confidence"],
        summary["no_subtitle"],
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
    roi_path = subtitle_area_json_path(video_path, args.output_dir)
    srt_path = subtitle_output_path(video_path, args.output_dir)
    roi_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.parent.mkdir(parents=True, exist_ok=True)

    if srt_path.exists() and not args.force_srt:
        logging.info("Skip existing SRT: %s", srt_path)
        return "skipped"

    try:
        result = get_or_detect_roi(video_path, roi_path, args)
    except Exception as exc:
        write_error_roi(video_path, roi_path, exc)
        logging.exception("ROI detection failed: %s", video_path)
        return "failed"

    if result.status == "error":
        logging.error("ROI detection error: %s reason=%s", video_path, result.reason)
        return "failed"
    candidate_entries = list(result.iter_candidate_subtitle_areas(min_confidence=args.min_confidence))
    subtitle_area = candidate_entries[0][2] if candidate_entries else result.to_subtitle_area()
    if result.status != "ok" or subtitle_area is None or result.confidence < args.min_confidence:
        logging.warning("Low-confidence ROI skipped: %s confidence=%.4f reason=%s", video_path, result.confidence, result.reason)
        return "low_confidence"

    shared_vsf_input_root = None
    try:
        shared_vsf_input_path, shared_vsf_input_root = prepare_shared_vsf_input_after_roi(video_path, args)
        if len(candidate_entries) > 1 and args.label_matcher and args.label_matcher.terms:
            return extract_and_select_candidate_srt(
                video_path,
                srt_path,
                roi_path,
                result,
                candidate_entries,
                args,
                vsf_input_video_path=shared_vsf_input_path,
            )

        try:
            extractor = run_subtitle_extractor_vsf_only(
                video_path,
                subtitle_area,
                args,
                subtitle_output_path=srt_path,
                vsf_input_video_path=shared_vsf_input_path,
            )
        except RuntimeError as exc:
            if is_vsf_no_subtitle_output_error(exc):
                logging.info("No subtitle output from VideoSubFinder: %s", video_path)
                return "no_subtitle"
            raise
        logging.info("SRT generated: %s", extractor.subtitle_output_path)
        return "success"
    except Exception:
        logging.exception("Subtitle extraction failed: %s", video_path)
        return "failed"
    finally:
        if shared_vsf_input_root is not None:
            shutil.rmtree(shared_vsf_input_root, ignore_errors=True)


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


def subtitle_area_json_path(video_path: Path, output_dir: Path | None = None) -> Path:
    filename = f"{video_path.stem}.subtitle_area.json"
    if output_dir is not None:
        return output_dir / filename
    return video_path.with_name(filename)


def subtitle_output_path(video_path: Path, output_dir: Path | None = None) -> Path:
    filename = f"{video_path.stem}.srt"
    if output_dir is not None:
        return output_dir / filename
    return video_path.with_name(filename)


def load_label_matcher(label_config_dir: str):
    from backend.tools.label_text_matcher import LabelTextMatcher

    matcher = LabelTextMatcher.from_config_dir(label_config_dir)
    if matcher.terms:
        logging.info("Loaded standard action label terms: %s from %s", len(matcher.terms), label_config_dir)
    else:
        logging.warning("No standard action label terms loaded from: %s", label_config_dir)
    return matcher


def prepare_shared_vsf_input_after_roi(video_path: Path, args: argparse.Namespace) -> tuple[Path, Path | None]:
    if args.no_vsf_transcode:
        return video_path, None

    from backend.tools.video_transcode import transcode_video_for_vsf

    shared_root = PROJECT_ROOT / "output" / f"{video_path.stem}_vsf_input"
    shutil.rmtree(shared_root, ignore_errors=True)
    shared_root.mkdir(parents=True, exist_ok=True)
    output_path = shared_root / "vsf_input.mp4"
    logging.info("Transcode once after ROI detection: %s", output_path)
    try:
        return Path(transcode_video_for_vsf(video_path, output_path)), shared_root
    except Exception:
        shutil.rmtree(shared_root, ignore_errors=True)
        raise


def extract_and_select_candidate_srt(
    video_path: Path,
    final_srt_path: Path,
    roi_path: Path,
    result,
    candidate_entries,
    args,
    *,
    vsf_input_video_path: Path,
) -> str:
    from backend.config import config
    from backend.tools.auto_subtitle_area import save_result_json
    from backend.tools.label_text_matcher import read_srt_text

    candidate_root = PROJECT_ROOT / "output" / f"{video_path.stem}_roi_candidates"
    shutil.rmtree(candidate_root, ignore_errors=True)
    candidate_root.mkdir(parents=True, exist_ok=True)

    evaluations = []
    no_subtitle_count = 0
    failed_count = 0
    try:
        for ordinal, (candidate_index, candidate, subtitle_area) in enumerate(
            order_candidate_entries_for_extraction(candidate_entries),
            start=1,
        ):
            candidate_srt = candidate_root / f"candidate_{ordinal}.srt"
            candidate_work_dir = candidate_root / f"work_{ordinal}"
            logging.info(
                "Evaluate ROI candidate %s/%s: roi_score=%.4f tpr=%.4f label=%s",
                ordinal,
                len(candidate_entries),
                candidate.score,
                candidate.temporal_presence_rate,
                candidate.temporal_presence_label,
            )
            try:
                extractor = run_subtitle_extractor_vsf_only(
                    video_path,
                    subtitle_area,
                    args,
                    subtitle_output_path=candidate_srt,
                    temp_output_dir=candidate_work_dir,
                    vsf_input_video_path=vsf_input_video_path,
                )
            except Exception as exc:
                if is_vsf_no_subtitle_output_error(exc):
                    no_subtitle_count += 1
                    logging.info("ROI candidate has no subtitle output: %s candidate=%s", video_path, ordinal)
                    continue
                failed_count += 1
                logging.warning("ROI candidate extraction failed: %s candidate=%s error=%s", video_path, ordinal, exc)
                logging.debug("ROI candidate extraction traceback", exc_info=True)
                continue

            text = read_srt_text(candidate_srt)
            match_result = args.label_matcher.score_text(text)
            evaluation = {
                "ordinal": ordinal,
                "candidate_index": candidate_index,
                "candidate": candidate,
                "subtitle_area": subtitle_area,
                "srt_path": candidate_srt,
                "extractor": extractor,
                "text_match": match_result,
            }
            evaluations.append(evaluation)
            logging.info(
                "ROI candidate scored: candidate=%s text_score=%.4f coverage=%.4f matched=%s",
                ordinal,
                match_result.score,
                match_result.coverage_score,
                ", ".join(match_result.matched_terms[:8]) if match_result.matched_terms else "-",
            )
            if (
                candidate.temporal_presence_label == "primary_subtitle"
                and match_result.coverage_score >= LABEL_COVERAGE_EARLY_STOP
            ):
                finalize_candidate_selection(evaluation, final_srt_path, result, roi_path, save_result_json, config)
                logging.info(
                    "Selected ROI candidate early: %s coverage=%.4f text_score=%.4f roi_score=%.4f output=%s",
                    ordinal,
                    match_result.coverage_score,
                    match_result.score,
                    candidate.score,
                    final_srt_path,
                )
                return "success"

        if not evaluations:
            if no_subtitle_count and failed_count == 0:
                logging.info("No subtitle output from any ROI candidate: %s", video_path)
                return "no_subtitle"
            logging.error(
                "All ROI candidate extractions failed: %s no_subtitle=%s failed=%s",
                video_path,
                no_subtitle_count,
                failed_count,
            )
            return "failed"

        best = max(
            evaluations,
            key=lambda item: (
                item["text_match"].coverage_score,
                item["text_match"].score,
                item["candidate"].score,
                -item["ordinal"],
            ),
        )
        highest_text_score, highest_coverage = highest_text_match_scores(evaluations)
        if is_low_text_match_confidence(highest_text_score, highest_coverage):
            record_candidate_match_result(
                best,
                result,
                roi_path,
                save_result_json,
                status="low_confidence",
                reason="highest text match score and coverage below threshold",
            )
            logging.warning(
                "Low-confidence text match skipped: %s candidate=%s "
                "highest_coverage=%.4f highest_text_score=%.4f threshold=%.2f",
                video_path,
                best["ordinal"],
                highest_coverage,
                highest_text_score,
                TEXT_MATCH_LOW_CONFIDENCE_THRESHOLD,
            )
            return "low_confidence"

        finalize_candidate_selection(best, final_srt_path, result, roi_path, save_result_json, config)

        logging.info(
            "Selected ROI candidate: %s coverage=%.4f text_score=%.4f roi_score=%.4f output=%s",
            best["ordinal"],
            best["text_match"].coverage_score,
            best["text_match"].score,
            best["candidate"].score,
            final_srt_path,
        )
        return "success"
    finally:
        shutil.rmtree(candidate_root, ignore_errors=True)


def order_candidate_entries_for_extraction(candidate_entries):
    primary_entries = [
        entry for entry in candidate_entries if entry[1].temporal_presence_label == "primary_subtitle"
    ]
    secondary_entries = [
        entry for entry in candidate_entries if entry[1].temporal_presence_label != "primary_subtitle"
    ]
    return [*primary_entries, *secondary_entries]


def highest_text_match_scores(evaluations) -> tuple[float, float]:
    highest_text_score = max(item["text_match"].score for item in evaluations)
    highest_coverage = max(item["text_match"].coverage_score for item in evaluations)
    return highest_text_score, highest_coverage


def is_low_text_match_confidence(highest_text_score: float, highest_coverage: float) -> bool:
    return (
        highest_text_score < TEXT_MATCH_LOW_CONFIDENCE_THRESHOLD
        and highest_coverage < TEXT_MATCH_LOW_CONFIDENCE_THRESHOLD
    )


def finalize_candidate_selection(evaluation, final_srt_path, result, roi_path, save_result_json, config) -> None:
    shutil.copy2(evaluation["srt_path"], final_srt_path)
    if config.generateTxt.value:
        evaluation["extractor"].srt2txt(str(final_srt_path))

    record_candidate_match_result(evaluation, result, roi_path, save_result_json)


def record_candidate_match_result(
    evaluation,
    result,
    roi_path,
    save_result_json,
    *,
    status: str | None = None,
    reason: str | None = None,
) -> None:
    selected_area = evaluation["subtitle_area"]
    result.subtitle_roi = (
        int(selected_area.xmin),
        int(selected_area.xmax),
        int(selected_area.ymin),
        int(selected_area.ymax),
    )
    result.confidence = evaluation["candidate"].score
    result.selected_candidate_index = evaluation["candidate_index"]
    result.text_match_score = evaluation["text_match"].score
    result.text_match_coverage = evaluation["text_match"].coverage_score
    if status is not None:
        result.status = status
    if reason is not None:
        result.reason = reason
    save_result_json(result, roi_path)


def run_subtitle_extractor_vsf_only(
    video_path: Path,
    subtitle_area,
    args: argparse.Namespace,
    *,
    subtitle_output_path: Path,
    temp_output_dir: Path | None = None,
    vsf_input_video_path: Path | None = None,
):
    try:
        return run_subtitle_extractor(
            video_path,
            subtitle_area,
            args,
            subtitle_output_path=subtitle_output_path,
            temp_output_dir=temp_output_dir,
            vsf_input_video_path=vsf_input_video_path,
            scan_strategy="vsf",
        )
    except RuntimeError as exc:
        if "VideoSubFinder failed" not in str(exc):
            raise
        if is_vsf_no_subtitle_output_error(exc):
            logging.info("VideoSubFinder produced no subtitle output; abandoning ROI candidate: %s", exc)
        else:
            logging.warning("VideoSubFinder failed; abandoning ROI candidate without frame_det fallback: %s", exc)
        raise


def is_vsf_no_subtitle_output_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "videosubfinder failed" in message and "no subtitle output" in message


def run_subtitle_extractor(
    video_path: Path,
    subtitle_area,
    args: argparse.Namespace,
    *,
    subtitle_output_path: Path,
    temp_output_dir: Path | None = None,
    vsf_input_video_path: Path | None = None,
    scan_strategy: str = "vsf",
):
    from backend.main import SubtitleExtractor
    from backend.config import config
    from backend.tools.constant import VideoSubFinderDecoder

    config.dropScore.value = args.ocr_drop_score
    extractor = SubtitleExtractor(str(video_path))
    if temp_output_dir is not None:
        configure_extractor_temp_paths(extractor, temp_output_dir)
    extractor.subtitle_output_path = str(subtitle_output_path)
    if vsf_input_video_path is not None:
        extractor.vsf_input_video_path = str(vsf_input_video_path)
    extractor.sub_area = subtitle_area
    extractor.scan_strategy = scan_strategy
    extractor.vsf_decoder = VideoSubFinderDecoder.FFMPEG if args.vsf_decoder == "ffmpeg" else VideoSubFinderDecoder.OPENCV
    extractor.transcode_before_vsf = scan_strategy == "vsf" and not args.no_vsf_transcode and vsf_input_video_path is None
    extractor.run()
    return extractor


def configure_extractor_temp_paths(extractor, temp_output_dir: Path) -> None:
    temp_output_dir = Path(temp_output_dir)
    extractor.temp_output_dir = str(temp_output_dir)
    extractor.frame_output_dir = str(temp_output_dir / "frames")
    extractor.subtitle_output_dir = str(temp_output_dir / "subtitle")
    extractor.vsf_subtitle = str(Path(extractor.subtitle_output_dir) / "raw_vsf.srt")
    extractor.raw_subtitle_path = str(Path(extractor.subtitle_output_dir) / "raw.txt")
    extractor.vsf_input_video_path = extractor.video_path


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
