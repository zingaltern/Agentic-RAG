"""规则评估器（DEVELOPMENT.md 5.5）：判定检索结果是否有足够依据。

score = 0.7 * coverage + 0.3 * length_norm（默认阈值 0.45，见 evaluator.rule_threshold）。
纯函数、无副作用。
"""
from __future__ import annotations

import re
from typing import Any

import jieba

from app.core.domain import EvaluationResult, ScoredChunk
from app.core.interfaces import Evaluator

# 中文停用词（内置小表，评估用）
_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "有", "和", "就", "都",
    "而", "及", "与", "或", "一个", "怎么", "如何", "吗", "呢", "吧", "啊", "呀",
    "这个", "那个", "什么", "可以", "需要", "请问", "请", "能", "会", "要", "对",
}


class RuleEvaluator(Evaluator):
    def __init__(self, rule_threshold: float = 0.45, top_n: int = 5, **_: Any):
        self.threshold = rule_threshold
        self.top_n = top_n

    @staticmethod
    def _terms(query: str) -> list[str]:
        return [t for t in jieba.lcut(query) if t.strip() and t not in _STOPWORDS]

    def evaluate(self, query: str, chunks: list[ScoredChunk]) -> EvaluationResult:
        top_chunks = list(chunks[:self.top_n])
        if not top_chunks:
            return EvaluationResult(passed=False, score=0.0, reason="empty_context")

        terms = self._terms(query)
        if not terms:
            # 查询本身只剩停用词/空白：无法判定不足，视为通过（5.5）
            return EvaluationResult(passed=True, score=1.0, reason="ok")

        covered = {t for t in terms if any(t in c.chunk.text for c in top_chunks)}
        coverage = len(covered) / len(terms)
        context_len = sum(len(c.chunk.text) for c in top_chunks)
        length_norm = min(1.0, context_len / 200)
        score = 0.7 * coverage + 0.3 * length_norm
        passed = score >= self.threshold
        reason = "ok" if passed else "below_threshold"
        return EvaluationResult(passed=passed, score=round(score, 4), reason=reason)
