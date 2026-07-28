# -*- coding: utf-8 -*-
"""
标准招式字库文本匹配。

该模块用于批处理自动 ROI 多候选选择：候选 SRT 生成后，提取 OCR 文本并和
label_configs 中的标准动作名/规则词做快速子串匹配，命中分数高者优先。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_LABEL_CONFIG_DIR = Path("D:/autoCut/autocut/label_configs")

_TEXT_KEEP_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[_\-.、，:：]?\s*")

_RULE_WEIGHTS = {
    "action_label": 5.0,
    "strong": 4.0,
    "medium": 2.5,
    "fuzzy": 1.5,
    "weak": 0.8,
    "label": 1.0,
}


@dataclass(frozen=True)
class LabelTerm:
    text: str
    normalized: str
    weight: float
    source: str


@dataclass(frozen=True)
class LabelMatchResult:
    score: float
    matched_terms: Sequence[str]
    term_count: int


class LabelTextMatcher:
    def __init__(self, terms: Sequence[LabelTerm]):
        self.terms = list(terms)

    @classmethod
    def from_config_dir(cls, config_dir: str | Path = DEFAULT_LABEL_CONFIG_DIR) -> "LabelTextMatcher":
        return cls(load_label_terms(config_dir))

    def score_text(self, text: str) -> LabelMatchResult:
        normalized_text = normalize_text(text)
        if not normalized_text or not self.terms:
            return LabelMatchResult(score=0.0, matched_terms=(), term_count=len(self.terms))

        score = 0.0
        matched_terms: List[str] = []
        matched_normalized = set()
        for term in self.terms:
            if term.normalized in matched_normalized:
                continue
            if term.normalized and term.normalized in normalized_text:
                matched_normalized.add(term.normalized)
                matched_terms.append(term.text)
                score += term.weight * min(len(term.normalized), 12)

        return LabelMatchResult(
            score=round(score, 4),
            matched_terms=tuple(matched_terms[:20]),
            term_count=len(self.terms),
        )


def load_label_terms(config_dir: str | Path = DEFAULT_LABEL_CONFIG_DIR) -> List[LabelTerm]:
    config_dir = Path(config_dir)
    if not config_dir.exists():
        return []

    terms_by_normalized: Dict[str, LabelTerm] = {}
    for json_path in sorted(config_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for text, source in _iter_config_texts(data):
            _add_term_variants(terms_by_normalized, text, source)

    return sorted(terms_by_normalized.values(), key=lambda item: (-item.weight, item.normalized))


def read_srt_text(srt_path: str | Path) -> str:
    try:
        import pysrt

        subs = pysrt.open(str(srt_path), encoding="utf-8")
        return "\n".join(sub.text for sub in subs)
    except Exception:
        text = Path(srt_path).read_text(encoding="utf-8", errors="ignore")
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.isdigit() or "-->" in stripped:
                continue
            lines.append(stripped)
        return "\n".join(lines)


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join(_TEXT_KEEP_RE.findall(text))


def _iter_config_texts(data: dict) -> Iterable[tuple[str, str]]:
    action_labels = data.get("action_labels")
    if isinstance(action_labels, dict):
        for value in action_labels.values():
            if isinstance(value, str):
                yield value, "action_label"

    labels = data.get("labels")
    if isinstance(labels, dict):
        for value in labels.values():
            if isinstance(value, str):
                yield value, "label"

    action_rules = data.get("action_rules")
    if isinstance(action_rules, dict):
        for rules in action_rules.values():
            if not isinstance(rules, dict):
                continue
            for rule_name in ("strong", "medium", "fuzzy", "weak"):
                values = rules.get(rule_name)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, str):
                        yield value, rule_name


def _add_term_variants(terms_by_normalized: Dict[str, LabelTerm], text: str, source: str) -> None:
    values = [text]
    stripped = _LEADING_NUMBER_RE.sub("", text)
    if stripped != text:
        values.append(stripped)

    for value in values:
        normalized = normalize_text(value)
        if len(normalized) < 2:
            continue
        weight = _RULE_WEIGHTS.get(source, 1.0)
        current = terms_by_normalized.get(normalized)
        if current is None or weight > current.weight:
            terms_by_normalized[normalized] = LabelTerm(
                text=value,
                normalized=normalized,
                weight=weight,
                source=source,
            )


__all__ = [
    "DEFAULT_LABEL_CONFIG_DIR",
    "LabelMatchResult",
    "LabelTextMatcher",
    "LabelTerm",
    "load_label_terms",
    "normalize_text",
    "read_srt_text",
]
