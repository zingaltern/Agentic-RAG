r"""语义幻觉检测 v2（DEVELOPMENT.md 13.4）。

- 规则保留：句末引用标号归入前句后按 。！？ 切句、正则 [(\d+)] 取引用、
  无引用整句 → suspected；
- 判定升级：句 ⊕ 引用块内句级片段的嵌入余弦（取最大）替代字符 2-gram Jaccard，
  且同一片段需满足字符 2-gram Jaccard >= lexical_overlap_min（默认 0.3）；
  语义阈值 semantic_similarity_threshold（默认 0.6）；
- 输出契约不变：verified / verified_rate / suspected。
- 需要注入 VectorEngine（wire 装配时传入）。
"""
from __future__ import annotations

from typing import Any

from app.adapters.detector.context_units import cited_context_units
from app.adapters.detector.lexical import char_bigrams, jaccard
from app.adapters.detector.sentences import extract_citations, split_answer_sentences
from app.core.domain import DetectionResult, ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import HallucinationDetector, VectorEngine
from app.core.similarity import cosine


class SemanticVerifierDetector(HallucinationDetector):
    def __init__(self, embedding: VectorEngine | None = None,
                 semantic_similarity_threshold: float = 0.6,
                 lexical_overlap_min: float = 0.3,
                 similarity_threshold: float = 0.8, **_: Any):
        self.embedding = embedding
        self.threshold = semantic_similarity_threshold
        self.lexical_floor = lexical_overlap_min

    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult:
        if not answer or not answer.strip():
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)
        if self.embedding is None:
            raise EmbeddingError("semantic 检测器需要注入 embedding（wire 装配）")

        sentences = split_answer_sentences(answer)
        if not sentences:
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)

        # 句级证据：长父块与短答案直接算整块余弦会被无关内容稀释；改为对引用块内
        # 的句级片段取最大相似度（13.4 修订）。
        units, ranges = cited_context_units(sentences, context)
        if not units:
            return DetectionResult(verified=False, suspected=sentences, verified_rate=0.0)

        try:
            # 批量向量化：回答句子 + 引用块内句级片段（一次调用）
            vecs = self.embedding.embed_documents(sentences + units)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"语义检测向量化失败: {exc}") from exc
        sentence_vecs, unit_vecs = vecs[:len(sentences)], vecs[len(sentences):]

        confirmed = 0
        suspected: list[str] = []
        for idx, sentence in enumerate(sentences):
            citation_nums = extract_citations(sentence)
            supported = False
            s_bigrams = char_bigrams(sentence)
            for n in citation_nums:
                start, end = ranges.get(n, (0, 0))
                for uidx in range(start, end):
                    # 同一片段必须同时过语义阈值与词面下限，避免“语义上像但内容无支撑”
                    if (cosine(sentence_vecs[idx], unit_vecs[uidx]) >= self.threshold
                            and jaccard(s_bigrams, char_bigrams(units[uidx])) >= self.lexical_floor):
                        supported = True
                        break
                if supported:
                    break
            if supported:
                confirmed += 1
            else:
                suspected.append(sentence)

        total = len(sentences)
        verified_rate = confirmed / total if total else 1.0
        return DetectionResult(verified=len(suspected) == 0,
                               suspected=suspected,
                               verified_rate=round(verified_rate, 4))
