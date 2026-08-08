"""语义幻觉检测 v2（DEVELOPMENT.md 13.4）。

- 规则保留：按 。！？ 切句、正则 [(\d+)] 取引用、无引用整句 → suspected；
- 判定升级：句 ⊕ 引用块的嵌入余弦替代字符 2-gram Jaccard，
  阈值 semantic_similarity_threshold（默认 0.6）；
- 输出契约不变：verified / verified_rate / suspected。
- 需要注入 VectorEngine（wire 装配时传入）。
"""
from __future__ import annotations

import math
import re
from typing import Any

from app.core.domain import DetectionResult, ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import HallucinationDetector, VectorEngine

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"[。！？]")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


class SemanticVerifierDetector(HallucinationDetector):
    def __init__(self, embedding: VectorEngine | None = None,
                 semantic_similarity_threshold: float = 0.6,
                 similarity_threshold: float = 0.8, **_: Any):
        self.embedding = embedding
        self.threshold = semantic_similarity_threshold

    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult:
        if not answer or not answer.strip():
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)
        if self.embedding is None:
            raise EmbeddingError("semantic 检测器需要注入 embedding（wire 装配）")

        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(answer) if s.strip()]
        context_texts = [c.chunk.text for c in context]
        if not sentences:
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)

        try:
            # 批量向量化：所有句子 + 所有上下文块（一次调用）
            vecs = self.embedding.embed_documents(sentences + context_texts)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"语义检测向量化失败: {exc}") from exc
        sentence_vecs, ctx_vecs = vecs[:len(sentences)], vecs[len(sentences):]

        confirmed = 0
        suspected: list[str] = []
        for idx, sentence in enumerate(sentences):
            citation_nums = {int(n) for n in _CITATION_RE.findall(sentence)}
            if not citation_nums:
                suspected.append(sentence)
                continue
            best = max(
                (_cosine(sentence_vecs[idx], ctx_vecs[n - 1])
                 for n in citation_nums if 1 <= n <= len(ctx_vecs)),
                default=0.0,
            )
            if best >= self.threshold:
                confirmed += 1
            else:
                suspected.append(sentence)

        total = len(sentences)
        verified_rate = confirmed / total if total else 1.0
        return DetectionResult(verified=len(suspected) == 0,
                               suspected=suspected,
                               verified_rate=round(verified_rate, 4))
