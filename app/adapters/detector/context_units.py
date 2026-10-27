"""幻觉检测的“引用块 -> 句级片段”展开（5.8 / 13.4 共用）。

长父块整块与短答案做相似度会被无关内容稀释；两个检测器都改为在引用块内按句
取最大相似度。这里集中维护展开逻辑，避免 citation / semantic 两份实现漂移。
"""
from __future__ import annotations

from app.adapters.detector.sentences import extract_citations, split_context_sentences
from app.core.domain import ScoredChunk


def cited_context_units(sentences: list[str],
                        context: list[ScoredChunk]) -> tuple[list[str], dict[int, tuple[int, int]]]:
    """收集被引用块的句级片段；返回片段列表与块编号 -> [start, end) 区间。"""
    cited = sorted({n for sentence in sentences for n in extract_citations(sentence)})
    units: list[str] = []
    ranges: dict[int, tuple[int, int]] = {}
    for n in cited:
        if not 1 <= n <= len(context):
            continue
        text = context[n - 1].chunk.text or ""
        snippets = split_context_sentences(text) or ([text.strip()] if text.strip() else [])
        if not snippets:
            continue
        start = len(units)
        units.extend(snippets)
        ranges[n] = (start, len(units))
    return units, ranges
