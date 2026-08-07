"""引用溯源幻觉检测（DEVELOPMENT.md 5.8，HallucinationDetector.detect）。

契约：
1. 以 。！？ 切分 answer 为句；
2. 正则 \[(\d+)\] 提取每句引用集合；
3. 无引用的整句记入 suspected；
4. 每句与其引用的上下文块算字符 2-gram Jaccard：
   sim(s, cand) = |bigrams(s) ∩ bigrams(cand)| / |bigrams(s) ∪ bigrams(cand)|
5. 任一候选 ≥ similarity_threshold（默认 0.8）→ 该句"已证"，否则 suspected；
6. verified = len(suspected) == 0；verified_rate = 已证 / 总句。
纯 Python，无网络，毫秒级。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.domain import DetectionResult, ScoredChunk
from app.core.interfaces import HallucinationDetector

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"[。！？]")


def char_bigrams(s: str) -> set[str]:
    """字符 2-gram（5.8）。"""
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


class CitationTracingDetector(HallucinationDetector):
    def __init__(self, similarity_threshold: float = 0.8, **_: Any):
        self.threshold = similarity_threshold

    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult:
        if not answer or not answer.strip():
            # 空串：无待证句子 → 无疑似（verified=True），已证率按 1.0 处理
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)

        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(answer) if s.strip()]
        context_texts = [c.chunk.text for c in context]  # 编号从 1 开始对应 LLM 提示中的块序号

        confirmed_count = 0
        suspected: list[str] = []
        for sentence in sentences:
            citation_nums = {int(n) for n in _CITATION_RE.findall(sentence)}
            if not citation_nums:
                suspected.append(sentence)          # 3. 无引用整句 → suspected
                continue
            bigrams = char_bigrams(sentence)
            best = max(
                (_jaccard(bigrams, char_bigrams(context_texts[n - 1]))
                 for n in citation_nums if 1 <= n <= len(context_texts)),
                default=0.0,
            )
            if best >= self.threshold:
                confirmed_count += 1                # 5. 有任一候选达标 → 已证
            else:
                suspected.append(sentence)

        total = len(sentences)
        verified_rate = (confirmed_count / total) if total else 1.0
        return DetectionResult(verified=len(suspected) == 0,
                               suspected=suspected,
                               verified_rate=round(verified_rate, 4))
