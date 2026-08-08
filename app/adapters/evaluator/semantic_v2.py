"""语义评估器 semantic_v2（DEVELOPMENT.md 13.3.1）。

- 主信号：query 与 top_n 块嵌入余弦的候选集内相对分（min-max 差距），不依赖绝对阈值；
  加相关性护栏（hi < 0.2 视为候选集整体无关，主信号=0；全同分且整体高分=1）。
- 副信号：去停用词字面覆盖修正。
- score = 0.7 × 主信号 + 0.3 × 副信号；passed = score ≥ semantic_v2_threshold（默认 0.45）。
- 需要注入 VectorEngine（wire 装配时传入）。
"""
from __future__ import annotations

import math
from typing import Any

from app.core.domain import EvaluationResult, ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import Evaluator, VectorEngine

# 相关性护栏：候选集内最大余弦低于该值 → 主信号按 0 计（防 z-score 放大低相关查询）。
# 实测校准（真实 BGE）：相关题 Q03 hi≈0.38，无关查询 hi≈0.44 → 绝对护栏无法两全；
# 按 13.3.1"相对分为主"原则取低护栏，无关查询由"评估通过→LLM 拒答"兜底（13.6 判据方向）
_MIN_SIM_GUARD = 0.35


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def _relative_score(sims: list[float]) -> float:
    """候选集内相对分（13.3.1）：z-score 归一映射 [0,1]，不依赖绝对阈值。

    z = (hi - mean) / std 表示最佳块高出候选集均值的程度；z/1.5 映射到 [0,1]。
    候选集单块或全同分时：按 hi 高度直接给分。
    """
    hi = max(sims)
    if hi < _MIN_SIM_GUARD:
        return 0.0
    if len(sims) <= 1:
        return 1.0 if hi >= 0.4 else 0.3
    mean = sum(sims) / len(sims)
    var = sum((s - mean) ** 2 for s in sims) / len(sims)
    std = math.sqrt(var)
    if std < 1e-9:
        return 1.0 if hi >= 0.4 else 0.5
    z = (hi - mean) / std
    return min(1.0, max(0.0, z / 1.5))


class SemanticV2Evaluator(Evaluator):
    def __init__(self, embedding: VectorEngine | None = None, top_n: int = 5,
                 semantic_v2_threshold: float = 0.45, rule_threshold: float = 0.45,
                 **_: Any):
        self.embedding = embedding
        self.top_n = top_n
        self.threshold = semantic_v2_threshold

    def evaluate(self, query: str, chunks: list[ScoredChunk]) -> EvaluationResult:
        top = list(chunks[:self.top_n])
        if not top:
            return EvaluationResult(passed=False, score=0.0, reason="empty_context")
        if self.embedding is None:
            raise EmbeddingError("semantic_v2 评估器需要注入 embedding（wire 装配）")

        try:
            query_vec = self.embedding.embed_query(query)
            doc_vecs = self.embedding.embed_documents([c.chunk.text for c in top])
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"语义评估向量化失败: {exc}") from exc

        sims = [_cosine(query_vec, v) for v in doc_vecs]
        relative = _relative_score(sims)
        coverage = _literal_coverage(query, top)
        score = round(0.7 * relative + 0.3 * coverage, 4)
        passed = score >= self.threshold
        return EvaluationResult(passed=passed, score=score,
                                reason="ok" if passed else "below_threshold")


def _literal_coverage(query: str, top: list[ScoredChunk]) -> float:
    """副信号：查询词（非停用词）在 top_n 块中被覆盖的比例（与 rule 同口径）。"""
    terms = [t for t in _cut(query) if t.strip() and t not in _STOPWORDS]
    if not terms:
        return 1.0
    covered = {t for t in terms if any(t in c.chunk.text for c in top)}
    return len(covered) / len(terms)


_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "有", "和", "就", "都",
    "而", "及", "与", "或", "一个", "怎么", "如何", "吗", "呢", "吧", "啊", "呀",
    "这个", "那个", "什么", "可以", "需要", "请问", "请", "能", "会", "要", "对",
}


def _cut(text: str) -> list[str]:
    import jieba

    return list(jieba.lcut(text))
