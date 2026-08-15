"""关键词审核器（P-C，入库安全）。

- 词表：configs/moderation_keywords.csv（每行 `类别,关键词`）；
  缺失/损坏时用内置最小词表兜底（与 synonyms.csv 同款范式，参考 rule_rewriter._load_synonym_groups）。
- 命中判定：整段文本子串匹配（大小写不敏感）；verdict 由调用方按 moderation.mode 决定
  （本类仅返回 matched_rules；verdict=reject 当命中非空，flag 语义由接线层区分）。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.adapters.content_moderation import ModerationResult, Moderator
from app.core.logging import get_logger

logger = get_logger("moderation")

# 内置最小词表（占位，生产环境由维护者维护）
_BUILTIN_KEYWORDS: list[tuple[str, str]] = [
    ("政治", "敏感政治议题"),
    ("违法", "赌博网站"),
    ("违法", "违禁品交易"),
    ("色情", "成人内容"),
]


def _load_keywords(path: str) -> list[tuple[str, str]]:
    p = Path(path)
    if not p.exists():
        logger.warning("moderation_keywords.csv 不存在（%s），使用内置最小词表", path)
        return _BUILTIN_KEYWORDS
    try:
        with open(p, "r", encoding="utf-8") as fh:
            rows = [row for row in csv.reader(fh) if row and not row[0].strip().startswith("#")]
        keywords = [(row[0].strip(), row[1].strip()) for row in rows
                    if len(row) >= 2 and row[0].strip() and row[1].strip()]
        return keywords or _BUILTIN_KEYWORDS
    except Exception as exc:  # noqa: BLE001
        logger.warning("moderation_keywords.csv 读取失败（%s）：%s，使用内置最小词表", path, exc)
        return _BUILTIN_KEYWORDS


class KeywordModerator(Moderator):
    def __init__(self, keywords_csv: str = "./configs/moderation_keywords.csv", **_: Any):
        self._keywords = _load_keywords(keywords_csv)

    def moderate(self, text: str) -> ModerationResult:
        if not text:
            return ModerationResult(verdict="ok")
        lowered = text.lower()
        matched: list[dict] = []
        for category, keyword in self._keywords:
            if keyword.lower() in lowered:
                matched.append({"category": category, "keyword": keyword})
        if not matched:
            return ModerationResult(verdict="ok")
        return ModerationResult(verdict="reject", matched_rules=matched,
                                snippet=text[:120])
