r"""引用溯源幻觉检测（DEVELOPMENT.md 5.8，HallucinationDetector.detect）。

契约：
1. 先把句末（。！？之后）的 [N] 归入前句，再以 。！？ 切分 answer 为句；
2. 正则 \[(\d+)\] 提取每句引用集合；
3. 无引用的整句记入 suspected；
4. 每句与其引用块内的句级片段算字符 2-gram Jaccard，取最大值：
   sim(s, cand) = |bigrams(s) ∩ bigrams(cand)| / |bigrams(s) ∪ bigrams(cand)|
5. 任一候选 ≥ similarity_threshold（默认 0.8）→ 该句"已证"，否则 suspected；
6. verified = len(suspected) == 0；verified_rate = 已证 / 总句。
纯 Python，无网络，毫秒级。
"""
from __future__ import annotations

from typing import Any

from app.adapters.detector.context_units import cited_context_units
from app.adapters.detector.lexical import char_bigrams, jaccard
from app.adapters.detector.sentences import extract_citations, split_answer_sentences
from app.core.domain import DetectionResult, ScoredChunk
from app.core.interfaces import HallucinationDetector


class CitationTracingDetector(HallucinationDetector):
    def __init__(self, similarity_threshold: float = 0.8, **_: Any):
        self.threshold = similarity_threshold

    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult:
        if not answer or not answer.strip():
            # 空串：无待证句子 → 无疑似（verified=True），已证率按 1.0 处理
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)

        sentences = split_answer_sentences(answer)
        # 句级证据：长块整块 Jaccard 会被无关内容稀释，改为引用块内句级片段取最大。
        units, ranges = cited_context_units(sentences, context)

        confirmed_count = 0
        suspected: list[str] = []
        for sentence in sentences:
            citation_nums = extract_citations(sentence)
            if not citation_nums:
                suspected.append(sentence)          # 3. 无引用整句 → suspected
                continue
            bigrams = char_bigrams(sentence)
            best = 0.0
            for n in citation_nums:
                start, end = ranges.get(n, (0, 0))
                for uidx in range(start, end):
                    best = max(best, jaccard(bigrams, char_bigrams(units[uidx])))
            if best >= self.threshold:
                confirmed_count += 1                # 5. 有任一候选达标 → 已证
            else:
                suspected.append(sentence)

        total = len(sentences)
        verified_rate = (confirmed_count / total) if total else 1.0
        return DetectionResult(verified=len(suspected) == 0,
                               suspected=suspected,
                               verified_rate=round(verified_rate, 4))
