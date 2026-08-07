"""直通重排器（DEVELOPMENT.md 3.4，MVP 默认）：原样返回。

职责注记：RRF 融合只在 Retriever 内做；Reranker 只做"精排阶段"，
MVP 用直通占位，不得重复实现 RRF。
"""
from __future__ import annotations

from typing import Any

from app.core.domain import ScoredChunk
from app.core.interfaces import Reranker


class IdentityReranker(Reranker):
    def __init__(self, rerank_top_k: int = 5, **_: Any):
        self.rerank_top_k = rerank_top_k

    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        return list(chunks)
