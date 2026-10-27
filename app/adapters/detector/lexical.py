"""幻觉检测的词面相似度（字符 2-gram，5.8 / 13.4）。

语义余弦单独使用会把“根据知识库上下文，请参考编号 [1]”这类泛化回答判为已证；
语义 + 词面双条件可以在保留短句语义匹配的同时压掉这类假阳性。
"""
from __future__ import annotations

from app.core.similarity import jaccard


def char_bigrams(s: str) -> set[str]:
    """字符 2-gram（5.8）。"""
    return {s[i:i + 2] for i in range(len(s) - 1)}


__all__ = ["char_bigrams", "jaccard"]
