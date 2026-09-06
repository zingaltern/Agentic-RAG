"""文档元数据存储（持久化 JSON + Chroma 兜底重建）。

- 持久事实：data/documents.json（重启不丢；写入失败静默降级为内存态）。
- 兜底：JSON 缺失/为空且 Chroma 有数据时，rebuild_from_vector_store 全量重建，
  消除"重启后幽灵文档"（列表空但向量仍在、永远删不掉）。
- 职责：/api/documents 列表/详情/删除、content_hash 幂等（与 Chroma 双检）。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.domain import Document
from app.core.logging import get_logger
from app.core.persist import atomic_write_json

if TYPE_CHECKING:
    from app.core.interfaces import VectorStore
    from app.services.kb_store import KbStore

logger = get_logger("documents_store")


@dataclass
class DocEntry:
    document_id: str
    filename: str
    title: str
    chunk_count: int = 0
    kb_id: str = "default"           # V5.1：所属知识库（权限由库继承）
    created_at: float = 0.0
    archive_path: str | None = None  # P0-2：归档原件路径（None=未归档；旧数据兼容缺省）


class DocumentsStore:
    def __init__(self, persist_file: str = "data/documents.json"):
        self._persist_file = persist_file
        self._docs: dict[str, DocEntry] = {}
        self._by_hash: dict[tuple[str, str], str] = {}   # (kb_id, content_hash) → document_id
        self._lock = threading.Lock()
        self._load()

    # -- 持久化 ----------------------------------------------------------------

    def _load(self) -> None:
        path = Path(self._persist_file)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.get("docs", []):
                entry = DocEntry(
                    document_id=item["document_id"],
                    filename=item.get("filename", item["document_id"]),
                    title=item.get("title", ""),
                    kb_id=item.get("kb_id", "default"),
                    chunk_count=item.get("chunk_count", 0),
                    created_at=item.get("created_at", 0.0),
                    archive_path=item.get("archive_path"),
                )
                self._docs[entry.document_id] = entry
            for kb_id, content_hash, doc_id in raw.get("by_hash", []):
                self._by_hash[(kb_id, content_hash)] = doc_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("documents.json 读取失败（%s）：%s，按空索引启动", path, exc)

    def _persist(self) -> None:
        try:
            payload = {
                "docs": [asdict(e) for e in self._docs.values()],
                "by_hash": [[kb, h, doc_id] for (kb, h), doc_id in self._by_hash.items()],
            }
            atomic_write_json(self._persist_file, payload)   # S7：原子写 + 0o600
        except Exception as exc:  # noqa: BLE001
            logger.warning("documents.json 写入失败（降级内存态）：%s", exc)

    def rebuild_from_vector_store(self, vector_store: "VectorStore | None",
                                  kbs: "KbStore | None") -> int:
        """索引为空且 Chroma 有数据时全量重建（幽灵文档恢复）；已有数据不覆盖。"""
        getter = getattr(vector_store, "get_all_chunks", None)
        if getter is None or kbs is None:
            return 0
        with self._lock:
            if self._docs:
                return 0
            rebuilt = 0
            for info in kbs.list():
                try:
                    chunks = getter(info.collection)
                except Exception:  # noqa: BLE001
                    continue
                by_doc: dict[str, list] = {}
                for chunk in chunks:
                    by_doc.setdefault(chunk.document_id, []).append(chunk)
                for doc_id, cs in by_doc.items():
                    meta = cs[0].metadata
                    entry = DocEntry(
                        document_id=doc_id,
                        filename=meta.get("source") or doc_id,
                        title=Path(meta.get("source") or doc_id).stem or doc_id,
                        kb_id=meta.get("kb_id") or info.kb_id,
                        chunk_count=len(cs),
                        created_at=0.0,
                    )
                    self._docs[doc_id] = entry
                    content_hash = meta.get("content_hash")
                    if content_hash:
                        self._by_hash[(entry.kb_id, content_hash)] = doc_id
                    rebuilt += 1
            if rebuilt:
                self._persist()
            return rebuilt

    # -- CRUD ------------------------------------------------------------------

    def register(self, doc: Document, chunk_count: int,
                 archive_path: str | None = None) -> DocEntry:
        kb_id = doc.metadata.get("kb_id", "default")
        entry = DocEntry(
            document_id=doc.document_id,
            filename=doc.filename,
            title=doc.title,
            kb_id=kb_id,
            chunk_count=chunk_count,
            created_at=time.time(),
            archive_path=archive_path,   # P0-2：归档原件路径（None=未归档/归档关闭）
        )
        with self._lock:
            self._docs[entry.document_id] = entry
            self._by_hash[(kb_id, doc.content_hash)] = doc.document_id
        self._persist()
        return entry

    def get(self, document_id: str) -> DocEntry | None:
        with self._lock:
            return self._docs.get(document_id)

    def by_hash(self, content_hash: str, kb_id: str = "default") -> str | None:
        """按库作用域的幂等查询（13.5：同内容不同库互不影响）。"""
        with self._lock:
            return self._by_hash.get((kb_id, content_hash))

    def list(self, page: int = 1, size: int = 20, kb_id: str | None = None) -> list[dict]:
        with self._lock:
            docs = self._docs.values()
            if kb_id is not None:
                docs = (e for e in docs if e.kb_id == kb_id)
            ordered = sorted(docs, key=lambda e: e.created_at, reverse=True)
            start = (page - 1) * size
            return [self._to_dict(e) for e in ordered[start:start + size]]

    def count(self, kb_id: str | None = None) -> int:
        with self._lock:
            if kb_id is None:
                return len(self._docs)
            return sum(1 for e in self._docs.values() if e.kb_id == kb_id)

    def delete(self, document_id: str) -> bool:
        with self._lock:
            entry = self._docs.pop(document_id, None)
            if entry is None:
                return False
            for key, doc_id in list(self._by_hash.items()):
                if doc_id == document_id:
                    del self._by_hash[key]
        self._persist()
        self._remove_archive(entry.archive_path)   # P0-2：级联删归档原件（被遗忘权）
        return True

    def delete_by_kb(self, kb_id: str) -> int:
        """C7：删除知识库时清理其文档索引与幂等表（防止残留导致重建同名库后幂等误判）。"""
        with self._lock:
            removed = [did for did, e in self._docs.items() if e.kb_id == kb_id]
            archive_paths = [e.archive_path for e in self._docs.values() if e.kb_id == kb_id]
            for did in removed:
                del self._docs[did]
            for key in [k for k in self._by_hash if k[0] == kb_id]:
                del self._by_hash[key]
        if removed:
            self._persist()
        for path in archive_paths:   # P0-2：级联删归档原件（best-effort）
            self._remove_archive(path)
        return len(removed)

    @staticmethod
    def _remove_archive(archive_path: str | None) -> None:
        """P0-2：删除归档原件（缺失/失败仅告警不抛，删除主流程不受影响）。"""
        if not archive_path:
            return
        try:
            Path(archive_path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("归档原件删除失败（忽略）：%s（%s）", archive_path, exc)

    @staticmethod
    def _to_dict(entry: DocEntry) -> dict:
        return {
            "document_id": entry.document_id,
            "filename": entry.filename,
            "title": entry.title,
            "kb_id": entry.kb_id,
            "chunk_count": entry.chunk_count,
            "created_at": entry.created_at,
        }
