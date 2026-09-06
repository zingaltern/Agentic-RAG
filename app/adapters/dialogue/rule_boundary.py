"""规则会话边界判定器（在线侧输入处理.md 4.3.2；13.12.6）。

双维度综合判定当前 Query 是否为「新话题」：
- 语义维度：当前 query 与上一轮 query 的嵌入余弦（use_embedding=False 或计算失败 → 跳过）；
- 实体维度：jieba 分词词集 Jaccard 重合度。

判定（保守取向，避免误清上下文）：
- 两维均可用：语义 < semantic_threshold 且 实体 < entity_jaccard_threshold → 新话题；
- 仅实体可用（embedding 失败/关闭）：按实体维度；
- 两维均不可用：判定非新话题（保留历史，降级不丢上下文）。
"""
from __future__ import annotations

import math
from typing import Any

import jieba

from app.core.interfaces import BoundaryDetector
from app.core.logging import get_logger

logger = get_logger("dialogue")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


class RuleBoundaryDetector(BoundaryDetector):
    def __init__(self, embedding: Any | None = None, use_embedding: bool = True,
                 semantic_threshold: float = 0.60,
                 entity_jaccard_threshold: float = 0.20, **_: Any):
        self.embedding = embedding
        self.use_embedding = use_embedding and embedding is not None
        self.semantic_threshold = semantic_threshold
        self.entity_jaccard_threshold = entity_jaccard_threshold

    def _semantic_sim(self, current: str, previous: str) -> float | None:
        if not self.use_embedding:
            return None
        try:
            a = self.embedding.embed_query(current)
            b = self.embedding.embed_query(previous)
            return _cosine(a, b)
        except Exception as exc:  # noqa: BLE001
            logger.warning("边界判定嵌入计算失败（降级仅实体维度）：%s", exc)
            return None

    def is_new_topic(self, current: str, previous: str) -> bool:
        if not previous or not current:
            return False
        sim = self._semantic_sim(current, previous)
        cur_terms = {t for t in jieba.cut(current) if t.strip()}
        prev_terms = {t for t in jieba.cut(previous) if t.strip()}
        jac = _jaccard(cur_terms, prev_terms)
        if sim is not None:
            return sim < self.semantic_threshold and jac < self.entity_jaccard_threshold
        return jac < self.entity_jaccard_threshold
