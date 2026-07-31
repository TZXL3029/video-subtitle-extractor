# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.tools.auto_subtitle_area import AutoSubtitleAreaResult, SubtitleAreaCandidate
from backend.tools import auto_subtitle_area
from backend.tools.ocr_subtitle_area import build_ocr_subtitle_area_payload
from scripts import batch_auto_extract


class FakeSubtitleArea:
    LOWER_PART = None
    UPPER_PART = None

    def __init__(self, ymin: int, ymax: int, xmin: int, xmax: int) -> None:
        self.ymin = ymin
        self.ymax = ymax
        self.xmin = xmin
        self.xmax = xmax


fake_subtitle_area_module = types.ModuleType("backend.bean.subtitle_area")
fake_subtitle_area_module.SubtitleArea = FakeSubtitleArea
sys.modules.setdefault("backend.bean.subtitle_area", fake_subtitle_area_module)


class ShortSubtitleRoiRescueTests(unittest.TestCase):
    def test_stable_short_horizontal_band_is_exported_as_candidate(self) -> None:
        cluster = [
            {"frame_no": 10, "bucket": 1, "coordinate": (300, 900, 600, 640)},
            {"frame_no": 930, "bucket": 8, "coordinate": (310, 910, 602, 642)},
        ]

        candidate = auto_subtitle_area._score_horizontal_cluster(
            cluster,
            width=1280,
            height=720,
            sampled_frame_count=45,
            tolerance=57,
        )

        self.assertFalse(candidate.excluded)
        self.assertEqual(candidate.temporal_presence_label, "short_primary_subtitle")
        self.assertGreaterEqual(candidate.score, 0.35)

    def test_short_primary_candidate_can_bypass_default_min_confidence(self) -> None:
        result = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=45,
            status="low_confidence",
            candidates=[
                SubtitleAreaCandidate(
                    roi=(320, 900, 600, 640),
                    score=0.36,
                    hits=2,
                    frame_hits=2,
                    time_bucket_hits=2,
                    temporal_presence_label="short_primary_subtitle",
                    excluded=False,
                )
            ],
        )

        entries = list(result.iter_candidate_subtitle_areas(min_confidence=0.5))

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1].temporal_presence_label, "short_primary_subtitle")

    def test_roi_rescue_sample_count_increases_until_configured_limit(self) -> None:
        self.assertEqual(batch_auto_extract.calculate_roi_rescue_samples(45, 1200), 240)
        self.assertEqual(batch_auto_extract.calculate_roi_rescue_samples(90, 1200), 360)
        self.assertEqual(batch_auto_extract.calculate_roi_rescue_samples(150, 1200), 600)
        self.assertEqual(batch_auto_extract.calculate_roi_rescue_samples(240, 1200), 960)
        self.assertEqual(batch_auto_extract.calculate_roi_rescue_samples(300, 1200), 1200)
        self.assertIsNone(batch_auto_extract.calculate_roi_rescue_samples(1200, 1200))

    def test_rescue_filter_keeps_only_new_roi_candidates(self) -> None:
        existing = SimpleNamespace(roi=(300, 900, 600, 650), orientation="horizontal")
        duplicate = SimpleNamespace(roi=(305, 905, 602, 652), orientation="horizontal")
        new_candidate = SimpleNamespace(roi=(300, 900, 120, 170), orientation="horizontal")

        entries = batch_auto_extract.filter_new_candidate_entries(
            [(0, duplicate, None), (1, new_candidate, None)],
            [(0, existing, None)],
            width=1280,
            height=720,
        )

        self.assertEqual([entry[1] for entry in entries], [new_candidate])

    def test_rescue_detection_merges_new_candidates_into_roi_json(self) -> None:
        original = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=45,
            status="low_confidence",
            candidates=[],
        )
        rescue_candidate = SubtitleAreaCandidate(
            roi=(320, 900, 600, 640),
            score=0.42,
            hits=2,
            frame_hits=2,
            time_bucket_hits=2,
            temporal_presence_label="short_primary_subtitle",
            excluded=False,
        )
        rescue = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0.42,
            sampled_frames=240,
            status="low_confidence",
            candidates=[rescue_candidate],
        )
        saved = []

        def fake_detect_auto_subtitle_area(*args, **kwargs):
            return rescue

        def fake_save_result_json(result, output_path):
            saved.append((result, output_path))

        real_detect = auto_subtitle_area.detect_auto_subtitle_area
        real_save = auto_subtitle_area.save_result_json
        auto_subtitle_area.detect_auto_subtitle_area = fake_detect_auto_subtitle_area
        auto_subtitle_area.save_result_json = fake_save_result_json
        try:
            entries = batch_auto_extract.detect_roi_rescue_candidate_entries(
                Path("demo.mp4"),
                original,
                Path("demo.subtitle_area.json"),
                argparse.Namespace(max_samples=1200, min_confidence=0.5, no_roi_progress=True),
                existing_entries=[],
            )
        finally:
            auto_subtitle_area.detect_auto_subtitle_area = real_detect
            auto_subtitle_area.save_result_json = real_save

        self.assertEqual([entry[1] for entry in entries], [rescue_candidate])
        self.assertEqual(original.candidates, [rescue_candidate])
        self.assertEqual(original.sampled_frames, 240)
        self.assertEqual(len(saved), 1)

    def test_single_candidate_success_records_roi_json_without_label_matcher(self) -> None:
        result = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=240,
            status="low_confidence",
            candidates=[],
        )
        candidate = SubtitleAreaCandidate(
            roi=(320, 900, 600, 640),
            score=0.42,
            hits=2,
            frame_hits=2,
            time_bucket_hits=2,
            temporal_presence_label="short_primary_subtitle",
            excluded=False,
        )
        area = FakeSubtitleArea(570, 670, 280, 940)
        saved = []

        batch_auto_extract.record_candidate_roi_result(
            (0, candidate, area),
            result,
            Path("demo.subtitle_area.json"),
            lambda saved_result, output_path: saved.append((saved_result, output_path)),
            status="ok",
            reason="",
        )

        self.assertEqual(result.subtitle_roi, (280, 940, 570, 670))
        self.assertEqual(result.confidence, 0.42)
        self.assertEqual(result.selected_candidate_index, 0)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.reason, "")
        self.assertEqual(result.candidates, [candidate])
        self.assertEqual(len(saved), 1)

    def test_ocr_subtitle_area_payload_uses_outer_bbox_from_raw_ocr_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.txt"
            raw_path.write_text(
                "00000010\t(120, 680, 610, 650)\t第一行\n"
                "00000020\t(100, 720, 600, 660)\t第二行\n"
                "00000030\t(500, 820, 120, 160)\t顶部字幕\n"
                "bad\t(0, 1, 2, 3)\tignored\n"
                "00000040\tno-coordinate\tignored\n",
                encoding="utf-8",
            )

            payload = build_ocr_subtitle_area_payload(raw_path, video="demo.mp4")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["video"], "demo.mp4")
        self.assertEqual(payload["box_count"], 3)
        self.assertEqual(payload["frame_count"], 3)
        self.assertEqual(payload["frame_start"], 10)
        self.assertEqual(payload["frame_end"], 30)
        self.assertEqual(payload["ocr_subtitle_bbox"], {"xmin": 100, "xmax": 820, "ymin": 120, "ymax": 660})
        self.assertEqual(
            payload["ocr_subtitle_bboxes"],
            [
                {
                    "xmin": 500,
                    "xmax": 820,
                    "ymin": 120,
                    "ymax": 160,
                    "box_count": 1,
                    "frame_count": 1,
                    "frame_start": 30,
                    "frame_end": 30,
                    "index": 1,
                },
                {
                    "xmin": 100,
                    "xmax": 720,
                    "ymin": 600,
                    "ymax": 660,
                    "box_count": 2,
                    "frame_count": 2,
                    "frame_start": 10,
                    "frame_end": 20,
                    "index": 2,
                },
            ],
        )

    def test_ocr_subtitle_area_keeps_boxes_separate_when_size_ratio_is_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.txt"
            raw_path.write_text(
                "00000010\t(100, 1100, 500, 700)\t大框\n"
                "00000020\t(450, 650, 540, 580)\t小框\n",
                encoding="utf-8",
            )

            payload = build_ocr_subtitle_area_payload(
                raw_path,
                video="demo.mp4",
                merge_overlap_threshold=0.5,
                merge_max_size_ratio=3.0,
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["ocr_subtitle_bboxes"]), 2)
        self.assertEqual(payload["ocr_subtitle_bboxes"][0]["box_count"], 1)
        self.assertEqual(payload["ocr_subtitle_bboxes"][1]["box_count"], 1)

    def test_selected_candidate_copies_ocr_subtitle_area_json(self) -> None:
        result = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=240,
            status="low_confidence",
            candidates=[],
        )
        candidate = SubtitleAreaCandidate(
            roi=(320, 900, 600, 640),
            score=0.42,
            hits=2,
            frame_hits=2,
            time_bucket_hits=2,
            temporal_presence_label="short_primary_subtitle",
            excluded=False,
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate_srt = tmp_path / "candidate.srt"
            candidate_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\ntext\n", encoding="utf-8")
            candidate_ocr_area = tmp_path / "candidate.ocr_subtitle_area.json"
            candidate_ocr_area.write_text(
                json.dumps({"ocr_subtitle_bbox": {"xmin": 1, "xmax": 2, "ymin": 3, "ymax": 4}}),
                encoding="utf-8",
            )
            final_srt = tmp_path / "demo.srt"
            final_ocr_area = tmp_path / "demo.ocr_subtitle_area.json"
            saved = []

            batch_auto_extract.finalize_candidate_selection(
                {
                    "candidate": candidate,
                    "subtitle_area": FakeSubtitleArea(570, 670, 280, 940),
                    "srt_path": candidate_srt,
                    "ocr_area_path": candidate_ocr_area,
                    "extractor": SimpleNamespace(srt2txt=lambda path: None),
                    "text_match": SimpleNamespace(score=0.8, coverage_score=0.7),
                    "label_matcher": SimpleNamespace(label_id="demo"),
                },
                final_srt,
                result,
                tmp_path / "demo.subtitle_area.json",
                lambda saved_result, output_path: saved.append((saved_result, output_path)),
                SimpleNamespace(generateTxt=SimpleNamespace(value=False)),
                ocr_area_path=final_ocr_area,
            )

            copied_payload = json.loads(final_ocr_area.read_text(encoding="utf-8"))
            final_srt_exists = final_srt.exists()

        self.assertTrue(final_srt_exists)
        self.assertEqual(copied_payload["ocr_subtitle_bbox"], {"xmin": 1, "xmax": 2, "ymin": 3, "ymax": 4})
        self.assertEqual(result.subtitle_roi, (280, 940, 570, 670))
        self.assertEqual(result.text_match_score, 0.8)
        self.assertEqual(result.text_match_coverage, 0.7)
        self.assertEqual(result.text_match_label, "demo")
        self.assertEqual(len(saved), 1)

    def test_vsf_candidate_failure_marks_candidate_excluded_in_json(self) -> None:
        result = AutoSubtitleAreaResult(
            video="demo.mp4",
            width=1280,
            height=720,
            fps=30,
            frame_count=1800,
            subtitle_roi=None,
            confidence=0,
            sampled_frames=240,
            status="low_confidence",
            candidates=[],
        )
        candidate = SubtitleAreaCandidate(
            roi=(320, 900, 600, 640),
            score=0.42,
            hits=2,
            frame_hits=2,
            time_bucket_hits=2,
            temporal_presence_label="short_primary_subtitle",
            excluded=False,
        )
        saved = []

        batch_auto_extract.mark_candidate_excluded(
            (0, candidate, FakeSubtitleArea(570, 670, 280, 940)),
            result,
            Path("demo.subtitle_area.json"),
            lambda saved_result, output_path: saved.append((saved_result, output_path)),
            "VideoSubFinder failed with decoder ffmpeg: no subtitle output",
        )

        self.assertTrue(candidate.excluded)
        self.assertIn("VideoSubFinder failed for ROI candidate", candidate.exclusion_reason)
        self.assertEqual(result.candidates, [candidate])
        self.assertEqual(len(saved), 1)

    def test_only_vsf_failures_are_candidate_exclusion_errors(self) -> None:
        self.assertTrue(
            batch_auto_extract.is_vsf_candidate_exclusion_error(
                RuntimeError("VideoSubFinder failed with decoder opencv: no subtitle output")
            )
        )
        self.assertFalse(batch_auto_extract.is_vsf_candidate_exclusion_error(RuntimeError("OCR failed")))


if __name__ == "__main__":
    unittest.main()
