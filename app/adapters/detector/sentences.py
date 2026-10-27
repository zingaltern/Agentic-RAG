"""幻觉检测共用的句子切分（DEVELOPMENT.md 5.8 / 13.4）。

LLM 常把引用标号写在句号之后（如“年假 10 天。[1]”）；若直接按 。！？ 切分，
`[1]` 会被切成一个没有正文的独立句，前句又因“无引用”被判 suspected。
这里先把句末引用标号归入前句，再切分，保证“正文 + 引用”成对。
"""
from __future__ import annotations

import re

_CITATION_RE = re.compile(r"\[(\d+)\]")
# 句末引用：[1] / [1][2]（允许标点与引用之间有空白）
_TRAILING_CITATIONS_RE = re.compile(r"([。！？!?])\s*((?:\[\d+\])+)")
_ANSWER_SPLIT_RE = re.compile(r"[。！？!?]")
_CONTEXT_SPLIT_RE = re.compile(r"[。！？!?\n]")


def extract_citations(text: str) -> set[int]:
    """提取文本中的引用编号（1-based）。"""
    return {int(n) for n in _CITATION_RE.findall(text)}


def split_answer_sentences(text: str) -> list[str]:
    """切分回答句子；句末引用标号归入前句。"""
    normalized = _TRAILING_CITATIONS_RE.sub(r"\2\1", text or "")
    return [s.strip() for s in _ANSWER_SPLIT_RE.split(normalized) if s.strip()]


def split_context_sentences(text: str) -> list[str]:
    """把引用块切成句级片段（含换行边界，便于匹配手册条目）。"""
    return [s.strip() for s in _CONTEXT_SPLIT_RE.split(text or "") if s.strip()]
