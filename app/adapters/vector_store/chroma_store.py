"""Chroma 向量库（DEVELOPMENT.md 3.2）。

- persist 目录由配置 vector_store.persist_dir 指定（SQLite 后端，单文件）。
- 距离空间 cosine；相似度 score = 1 - distance（3.2：由实现决定算法并文档注明）。
- Chroma 不允许 metadata 值为 None → 入库前剔除。
- 附加适配器方法（非接口契约）：get_all_chunks / get_document_id_by_hash，
  供 BM25 索引与幂等去重使用。
"""
from __future__ import annotations

import threading
from typing import Iterable

import chromadb

from app.core.domain import Chunk, ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import VectorStore


class ChromaStore(VectorStore):
    def __init__(self, persist_dir: str = "./data/chroma", collection: str = "kb",
                 batch_size: int = 64, **_: object):
        try:
            self._client = chromadb.PersistentClient(path=persist_dir)
        except Exception as exc:
            raise EmbeddingError(f"Chroma 初始化失败（{persist_dir}）: {exc}") from exc
        self._default_collection = collection
        self._batch_size = batch_size
        # C2：语料变更纪元——add_chunks/delete_document 递增，供 BM25 缓存做变更检测
        self._epoch: dict[str, int] = {}
        self._epoch_lock = threading.Lock()

    def mutation_epoch(self, collection: str = "kb") -> int:
        """附加适配器方法（非接口契约）：返回集合的语料变更纪元。"""
        with self._epoch_lock:
            return self._epoch.get(collection, 0)

    def _bump_epoch(self, collection: str) -> None:
        with self._epoch_lock:
            self._epoch[collection] = self._epoch.get(collection, 0) + 1

    # -- 内部工具 ------------------------------------------------------------

    @staticmethod
    def _chroma_name(collection: str) -> str:
        """Chroma 要求集合名 3~512 字符；配置层保持 DEVELOPMENT 4.2 的 `kb` 语义，
        实际存储统一加 `rag_` 前缀（如 kb → rag_kb）。"""
        return f"rag_{collection}"

    def _coll(self, collection: str):
        """取集合，不存在则按 cosine 空间创建。"""
        return self._client.get_or_create_collection(
            name=self._chroma_name(collection),
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _clean_metadata(metadata: dict) -> dict:
        """Chroma metadata 值不允许 None，剔除后再入库。"""
        return {k: v for k, v in metadata.items() if v is not None}

    @staticmethod
    def _score(distance: float) -> float:
        """cosine 空间：相似度 = 1 - distance。"""
        return 1.0 - distance

    # -- 契约（3.2） ----------------------------------------------------------

    def add_chunks(self, chunks: Iterable[tuple[Chunk, list[float]]],
                   collection: str = "kb") -> None:
        coll = self._coll(collection)
        ids: list[str] = []
        documents: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict] = []
        for chunk, vector in chunks:
            ids.append(chunk.chunk_id)
            documents.append(chunk.text)
            embeddings.append(vector)
            meta = {
                **chunk.metadata,
                "document_id": chunk.document_id,
                "parent_id": chunk.parent_id,
                "order": chunk.order,
            }
            metadatas.append(self._clean_metadata(meta))
            if len(ids) >= self._batch_size:
                coll.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
                ids, documents, embeddings, metadatas = [], [], [], []
        if ids:
            coll.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        self._bump_epoch(collection)   # C2：语料变更，通知 BM25 缓存失效

    def search(self, query_vector: list[float], k: int,
               filters: dict | None = None, collection: str = "kb") -> list[ScoredChunk]:
        coll = self._coll(collection)
        results = coll.query(
            query_embeddings=[query_vector],
            n_results=max(k, 1),
            where=filters or None,
            include=["documents", "metadatas", "distances"],
        )
        out: list[ScoredChunk] = []
        for i, chunk_id in enumerate(results["ids"][0]):
            meta = (results["metadatas"][0] or [{}])[i] or {}
            text = results["documents"][0][i]
            chunk = Chunk(
                chunk_id=chunk_id,
                document_id=meta.get("document_id", ""),
                text=text,
                order=int(meta.get("order", 0)),
                parent_id=meta.get("parent_id"),
                metadata={k: v for k, v in meta.items() if k not in ("document_id", "parent_id", "order")},
            )
            out.append(ScoredChunk(chunk=chunk, score=self._score(results["distances"][0][i]), notion="vector"))
        return out

    def delete_document(self, document_id: str, collection: str = "kb") -> int:
        coll = self._coll(collection)
        hit = coll.get(where={"document_id": document_id}, include=[])
        deleted = len(hit["ids"])
        if deleted:
            coll.delete(where={"document_id": document_id})
            self._bump_epoch(collection)   # C2：语料变更，通知 BM25 缓存失效
        return deleted

    def count(self, collection: str = "kb") -> int:
        return self._coll(collection).count()

    def drop_collection(self, collection: str = "kb") -> bool:
        """删除集合（含物理落盘）。超时保护：60s 内未完成按清理失败处理，杜绝挂死进程。

        返回 True=已删除/不存在，False=超时或删除失败（调用方记日志继续后续流程）。
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError

        from app.core.logging import get_logger

        name = self._chroma_name(collection)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._client.delete_collection, name)
        try:
            future.result(timeout=60)
            return True
        except _TimeoutError:
            # C8：超时后不得等待（shutdown(wait=False)），否则 __exit__ 仍会阻塞，超时保护形同虚设
            get_logger("vector_store").warning("drop_collection 超时（>60s）：%s，按清理失败处理", name)
            return False
        except Exception:
            return True  # 集合不存在等异常按"已清理"处理（静默兼容旧行为）
        finally:
            executor.shutdown(wait=False)

    # -- 适配器扩展（BM25 索引 / 幂等去重） ------------------------------------

    def get_all_chunks(self, collection: str = "kb") -> list[Chunk]:
        """拉取集合内全部块（供 HybridRetriever 重建 BM25 索引）。"""
        coll = self._coll(collection)
        data = coll.get(include=["documents", "metadatas"])
        chunks: list[Chunk] = []
        for i, chunk_id in enumerate(data["ids"]):
            meta = data["metadatas"][i] or {}
            chunks.append(Chunk(
                chunk_id=chunk_id,
                document_id=meta.get("document_id", ""),
                text=data["documents"][i],
                order=int(meta.get("order", 0)),
                parent_id=meta.get("parent_id"),
                metadata={k: v for k, v in meta.items() if k not in ("document_id", "parent_id", "order")},
            ))
        return chunks

    def get_document_id_by_hash(self, content_hash: str, collection: str = "kb") -> str | None:
        """按 content_hash 查既有文档（5.10 幂等上传）。"""
        data = self._coll(collection).get(where={"content_hash": content_hash}, include=["metadatas"])
        if not data["ids"]:
            return None
        return (data["metadatas"][0] or {}).get("document_id")
