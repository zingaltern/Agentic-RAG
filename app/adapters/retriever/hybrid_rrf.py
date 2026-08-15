"""混合检索器：BM25 + 向量 RRF 融合（DEVELOPMENT.md 5.4、3.3）。

- BM25：rank_bm25，中文先 jieba 分词后建索引（语料来自 VectorStore 的 get_all_chunks）。
- 向量：embed_query → VectorStore.search。
- 融合：按 5.4 伪代码 rrf_fusion（回归要点：按分数 item[1] 降序，禁止按元组排）。
- filters 必须同时作用于 BM25 与向量两路（3.3/附录 A-8）。
- 去重：同一 parent_id（家族）子块最多取 2 条进入结果（5.4 第 4 步）。
"""
from __future__ import annotations

import json
import threading
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from app.core.domain import ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import Retriever, VectorEngine, VectorStore


def rrf_fusion(bm25_scores: dict[str, float], vector_scores: dict[str, float],
               k: int = 60, top_n: int = 50) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion（5.4 伪代码原样实现）。"""
    fused: dict[str, float] = {}
    for rank, doc_key in enumerate(sorted(bm25_scores, key=bm25_scores.get, reverse=True)):
        fused[doc_key] = fused.get(doc_key, 0.0) + 1.0 / (k + rank + 1.0)
    for rank, doc_key in enumerate(sorted(vector_scores, key=vector_scores.get, reverse=True)):
        fused[doc_key] = fused.get(doc_key, 0.0) + 1.0 / (k + rank + 1.0)
    # 回归防呆：按分数降序（item[1]），不是按元组排序
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_n]


class HybridRRFRetriever(Retriever):
    def __init__(self, embedding: VectorEngine, vector_store: VectorStore,
                 top_k: int = 20, candidates: int = 50, rr_fused_k: int = 60,
                 collection: str = "kb", **_: Any):
        self.embedding = embedding
        self.vector_store = vector_store
        self.top_k = top_k
        self.candidates = candidates
        self.rr_fused_k = rr_fused_k
        self.collection = collection
        self._bm25_cache: dict[str, tuple[int, BM25Okapi, list[ScoredChunk]]] = {}
        self._bm25_lock = threading.Lock()   # C2（V5.1）：缓存读写互斥，并发 miss 只构建一次

    # -- 工具 ----------------------------------------------------------------

    @staticmethod
    def _metadata_filters(filters: dict | None) -> dict:
        """统一 filters 形态：{"metadata": {...}} → 等值过滤 dict。"""
        if not filters:
            return {}
        return filters.get("metadata") or {}

    @staticmethod
    def _matches(meta: dict, cond: dict) -> bool:
        """metadata 等值过滤（BM25 侧）：全部条件命中才算通过。"""
        return all(meta.get(k) == v for k, v in cond.items())

    def _bm25_index(self, collection: str, filters: dict | None) -> tuple[BM25Okapi, list[ScoredChunk]]:
        """从向量库拉取全量块并构建 BM25 索引（按 collection+count 缓存）。

        13.5：BM25 路必须与向量路同作用域（用传入的 collection，而非构造默认），
        否则跨库检索会泄露其他库的块。
        """
        cond = self._metadata_filters(filters)
        cache_key = json.dumps([collection, sorted(cond.items())], ensure_ascii=False, sort_keys=True)
        count = self.vector_store.count(collection)
        # 快路径：无锁读缓存（读多写少，容忍瞬时重复构建由慢路径兜底）
        cached = self._bm25_cache.get(cache_key)
        if cached is not None and cached[0] == count:
            return cached[1], cached[2]

        with self._bm25_lock:            # C2：双重检查后单次构建，其他线程等待复用结果
            cached = self._bm25_cache.get(cache_key)
            if cached is not None and cached[0] == count:
                return cached[1], cached[2]
            chunks = [c for c in self.vector_store.get_all_chunks(collection)
                      if self._matches(c.metadata, cond)]
            tokenized = [list(jieba.cut(c.text)) for c in chunks]
            bm25 = BM25Okapi(tokenized) if chunks else None
            self._bm25_cache[cache_key] = (count, bm25, chunks)
            return bm25, chunks

    # -- 契约（3.3） ----------------------------------------------------------

    def retrieve(self, query: str, k: int, filters: dict | None = None,
                 path: str = "hybrid", collection: str | None = None) -> list[ScoredChunk]:
        """返回降序排列的 k 条候选。

        path: hybrid（默认，BM25+向量 RRF 融合）| bm25（仅全文）| vector（仅向量），
        供编排层"query 必变"换路使用（13.3.3-1）。
        collection: 13.5 多知识库作用域（覆盖构造时的默认 collection），BM25/向量双路生效。
        签名对接口契约 3.3 向后兼容。
        """
        collection = collection or self.collection
        cond = self._metadata_filters(filters)

        # 1) BM25 路（全文检索侧按同条件过滤）
        bm25_scores: dict[str, float] = {}
        bm25, corpus = self._bm25_index(collection, filters)
        if bm25 is not None:
            query_tokens = list(jieba.cut(query))
            if query_tokens:
                raw = bm25.get_scores(query_tokens)
                for idx, chunk in enumerate(corpus):
                    bm25_scores[chunk.chunk_id] = float(raw[idx])

        # 2) 向量路（filters 透传给 VectorStore.search）
        vector_scores: dict[str, float] = {}
        try:
            query_vector = self.embedding.embed_query(query)
            vector_hits = self.vector_store.search(
                query_vector, k=max(self.candidates, k), filters=cond or None, collection=collection
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"向量检索失败: {exc}") from exc
        for hit in vector_hits:
            vector_scores[hit.chunk.chunk_id] = hit.score

        # 3) 按路径融合（单路时另一路为空 → rrf_fusion 等效按分排序）
        if path == "bm25":
            fused = rrf_fusion(bm25_scores, {}, k=self.rr_fused_k, top_n=max(self.candidates, k))
        elif path == "vector":
            fused = rrf_fusion({}, vector_scores, k=self.rr_fused_k, top_n=max(self.candidates, k))
        else:
            fused = rrf_fusion(bm25_scores, vector_scores, k=self.rr_fused_k, top_n=max(self.candidates, k))
        by_id = {c.chunk_id: c for c in corpus}
        for hit in vector_hits:
            by_id.setdefault(hit.chunk.chunk_id, hit.chunk)

        results: list[ScoredChunk] = []
        for chunk_id, score in fused:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            results.append(ScoredChunk(chunk=chunk, score=score, notion="rrf"))

        # 4) 去重：同一 parent_id（家族）子块最多取 2 条
        family_count: dict[str, int] = {}
        deduped: list[ScoredChunk] = []
        for item in results:
            family = item.chunk.parent_id or item.chunk.chunk_id
            if family_count.get(family, 0) >= 2:
                continue
            family_count[family] = family_count.get(family, 0) + 1
            deduped.append(item)
        return deduped[:k]
