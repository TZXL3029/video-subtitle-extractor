# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.tools.auto_subtitle_area import AutoSubtitleAreaResult, SubtitleAreaCandidate
from backend.tools import auto_subtitle_area
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
