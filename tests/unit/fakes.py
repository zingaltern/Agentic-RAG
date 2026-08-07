"""测试桩：确定性嵌入（不联网）。

同文本→同向量；字符 bigram 哈希落桶（相似文本→相似向量）。
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

from app.core.interfaces import VectorEngine


class StubEmbedding(VectorEngine):
    def __init__(self, dimension: int = 512,
                 query_instruction: str = "为这个句子生成表示以用于检索相关文章："):
        self._dimension = dimension
        self.query_instruction = query_instruction

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self._dimension
        for i in range(len(text)):
            bigram = text[max(0, i - 1):i + 1].encode("utf-8")
            digest = hashlib.md5(bigram).digest()
            idx = (digest[0] | (digest[1] << 8)) % self._dimension
            sign = 1.0 if digest[2] % 2 else -1.0
            v[idx] += sign * (digest[3] / 255.0 + 1.0)
        norm = math.sqrt(sum(x * x for x in v))
        if norm > 0:
            v = [x / norm for x in v]
        return v

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(self.query_instruction + text)

    def ready(self) -> bool:
        return True


class StubSentenceModel:
    """模拟 SentenceTransformer：记录 encode 输入，便于断言查询指令前缀。"""

    def __init__(self, dimension: int = 512):
        self.dimension = dimension
        self.embedder = StubEmbedding(dimension)
        self.encoded_sentences: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, sentences: list[str], **_: object):
        self.encoded_sentences.append(list(sentences))
        return np.array([self.embedder._vector(s) for s in sentences], dtype=float)
