# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.tools.label_text_matcher import load_label_matchers
from scripts import batch_auto_extract


class LabelDictionarySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name)
        self._write_config(
            "baduanjin",
            "baduanjin",
            [
                "两手托天理三焦",
                "左右开弓似射雕",
                "调理脾胃须单举",
                "五劳七伤往后瞧",
                "摇头摆尾去心火",
                "两手攀足固肾腰",
                "攒拳怒目增气力",
                "背后七颠百病消",
            ],
        )
        self._write_config(
            "taiji24",
            "taiji24",
            [
                "起势",
                "左右野马分鬃",
                "白鹤亮翅",
                "左右搂膝拗步",
                "手挥琵琶",
                "左右倒卷肱",
                "左揽雀尾",
                "右揽雀尾",
                "单鞭",
                "云手",
                "单鞭",
                "高探马",
                "右蹬脚",
                "双峰贯耳",
                "转身左蹬脚",
                "左下势独立",
                "右下势独立",
                "左右穿梭",
                "海底针",
                "闪通臂",
                "转身搬拦捶",
                "如封似闭",
                "十字手",
                "收势",
            ],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_label_matchers_keep_independent_coverage_denominators(self) -> None:
        matchers = {matcher.label_id: matcher for matcher in load_label_matchers(self.config_dir)}

        self.assertEqual(len(matchers["baduanjin"].action_groups), 8)
        self.assertEqual(len(matchers["taiji24"].action_groups), 24)
        self.assertEqual(matchers["baduanjin"].score_text("两手托天理三焦").coverage_score, 0.125)
        self.assertEqual(matchers["taiji24"].score_text("起势").coverage_score, 0.0417)

    def test_explicit_label_selects_one_dictionary(self) -> None:
        baduanjin = batch_auto_extract.load_label_matchers(str(self.config_dir), "baduanjin")
        taiji24 = batch_auto_extract.load_label_matchers(str(self.config_dir), "taiji24.json")

        self.assertEqual([matcher.label_id for matcher in baduanjin], ["baduanjin"])
        self.assertEqual([matcher.label_id for matcher in taiji24], ["taiji24"])

    def test_video_name_auto_selects_label_dictionary_by_alias(self) -> None:
        matchers = batch_auto_extract.load_label_matchers(str(self.config_dir))

        baduanjin = batch_auto_extract.select_label_matchers_for_video(Path("八段锦教学.mp4"), matchers)
        taiji = batch_auto_extract.select_label_matchers_for_video(Path("24式太极拳演示.mp4"), matchers)

        self.assertEqual([matcher.label_id for matcher in baduanjin], ["baduanjin"])
        self.assertEqual([matcher.label_id for matcher in taiji], ["taiji24"])

    def test_unknown_video_name_scores_against_each_dictionary(self) -> None:
        matchers = batch_auto_extract.load_label_matchers(str(self.config_dir))

        selected = batch_auto_extract.select_label_matchers_for_video(Path("demo.mp4"), matchers)
        matcher, result = batch_auto_extract.best_label_match_for_text("两手托天理三焦", selected)

        self.assertEqual([matcher.label_id for matcher in selected], ["baduanjin", "taiji24"])
        self.assertEqual(matcher.label_id, "baduanjin")
        self.assertEqual(result.coverage_score, 0.125)

    def test_unknown_explicit_label_reports_available_labels(self) -> None:
        with self.assertRaises(ValueError) as raised:
            batch_auto_extract.load_label_matchers(str(self.config_dir), "missing")

        message = str(raised.exception)
        self.assertIn("Unknown --label", message)
        self.assertIn("baduanjin", message)
        self.assertIn("taiji24", message)

    def _write_config(self, stem: str, name: str, action_names: list[str]) -> None:
        data = {
            "name": name,
            "description": f"{name} label dictionary",
            "labels": {"opening": "opening", "closing": "closing"},
            "action_labels": {
                str(index): f"{index:02d}_{action_name}" for index, action_name in enumerate(action_names, start=1)
            },
            "action_rules": {},
        }
        (self.config_dir / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
