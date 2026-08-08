"""文档管理（6.2、6.5、13.5）：上传流水线（5.10）+ 列表/详情/删除；支持多知识库 kb_id。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.utils import get_deps, ok
from app.core.errors import NotFoundError, ValidationError
from app.core.registry import Dependencies
from app.services.ingestion import compute_content_hash

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 kb_id: str = Form(default="default"),          # 13.5：目标知识库（缺省 default）
                 deps: Dependencies = Depends(get_deps)) -> dict:
    """上传 → 校验 → content_hash 幂等（按库）→ 创建 Job → 异步入库（5.10）。"""
    if deps.pipeline is None:
        from app.core.errors import InternalError

        raise InternalError("上传流水线尚未装配")
    if deps.kbs is None or not deps.kbs.exists(kb_id):
        raise ValidationError(f"知识库不存在: {kb_id!r}")
    cfg = deps.config.ingestion
    filename = file.filename or "unnamed"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in cfg.allowed_extensions:
        raise ValidationError(f"不支持的文档格式: {filename}（允许：{cfg.allowed_extensions}）")
    raw = await file.read()
    if len(raw) > cfg.max_file_size_mb * 1024 * 1024:
        raise ValidationError(f"文件超过 {cfg.max_file_size_mb}MB 限制: {filename}")

    content_hash = compute_content_hash(raw)
    collection = deps.kbs.collection_of(kb_id)
    existing = deps.pipeline.find_document_id_by_hash(content_hash, collection=collection, kb_id=kb_id)
    if existing:
        # 幂等：重复上传返回既有 document_id，不重复入库（2.2；按库作用域）
        return ok({"duplicate": True, "document_id": existing, "kb_id": kb_id})

    job = deps.jobs.create(filename, kb_id=kb_id)
    deps.pipeline.submit(raw, filename, job, kb_id=kb_id, collection=collection)
    return ok({"job_id": job.job_id, "status": job.status, "kb_id": kb_id})


@router.get("")
def list_documents(page: int = 1, size: int = 20,
                   deps: Dependencies = Depends(get_deps)) -> dict:
    """分页列表（按创建时间倒序，6.5）。"""
    if deps.documents is None:
        return ok({"items": [], "total": 0})
    return ok({"items": deps.documents.list(page, size), "total": deps.documents.count()})


@router.get("/{document_id}")
def get_document(document_id: str, deps: Dependencies = Depends(get_deps)) -> dict:
    """单文档元数据。"""
    entry = deps.documents.get(document_id) if deps.documents else None
    if entry is None:
        raise NotFoundError(f"文档不存在: {document_id}")
    return ok({
        "document_id": entry.document_id,
        "filename": entry.filename,
        "title": entry.title,
        "chunk_count": entry.chunk_count,
        "created_at": entry.created_at,
    })


@router.delete("/{document_id}")
def delete_document(document_id: str, deps: Dependencies = Depends(get_deps)) -> dict:
    """删除文档（级联清向量块，6.5）。"""
    entry = deps.documents.get(document_id) if deps.documents else None
    if entry is None:
        raise NotFoundError(f"文档不存在: {document_id}")
    deleted = 0
    if deps.vector_store is not None:
        deleted = deps.vector_store.delete_document(
            document_id, collection=deps.config.vector_store.collection)
    if deps.documents is not None:
        deps.documents.delete(document_id)
    return ok({"document_id": document_id, "deleted_chunks": deleted})
