"""文档元数据存储（进程内 MVP，重启清空；持久事实以 Chroma 为准）。

职责：/api/documents 列表/详情/删除、content_hash 幂等（与 Chroma 双检）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.core.domain import Document


@dataclass
class DocEntry:
    document_id: str
    filename: str
    title: str
    chunk_count: int = 0
    created_at: float = 0.0


class DocumentsStore:
    def __init__(self):
        self._docs: dict[str, DocEntry] = {}
        self._by_hash: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, doc: Document, chunk_count: int) -> DocEntry:
        entry = DocEntry(
            document_id=doc.document_id,
            filename=doc.filename,
            title=doc.title,
            chunk_count=chunk_count,
            created_at=time.time(),
        )
        with self._lock:
            self._docs[entry.document_id] = entry
            self._by_hash[doc.content_hash] = doc.document_id
        return entry

    def get(self, document_id: str) -> DocEntry | None:
        with self._lock:
            return self._docs.get(document_id)

    def by_hash(self, content_hash: str) -> str | None:
        with self._lock:
            return self._by_hash.get(content_hash)

    def list(self, page: int = 1, size: int = 20) -> list[dict]:
        with self._lock:
            ordered = sorted(self._docs.values(), key=lambda e: e.created_at, reverse=True)
            start = (page - 1) * size
            return [self._to_dict(e) for e in ordered[start:start + size]]

    def count(self) -> int:
        with self._lock:
            return len(self._docs)

    def delete(self, document_id: str) -> bool:
        with self._lock:
            entry = self._docs.pop(document_id, None)
            if entry is None:
                return False
            for hash_, doc_id in list(self._by_hash.items()):
                if doc_id == document_id:
                    del self._by_hash[hash_]
            return True

    @staticmethod
    def _to_dict(entry: DocEntry) -> dict:
        return {
            "document_id": entry.document_id,
            "filename": entry.filename,
            "title": entry.title,
            "chunk_count": entry.chunk_count,
            "created_at": entry.created_at,
        }
