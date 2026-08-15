"""文档管理（6.2、6.5、13.5）：上传流水线（5.10）+ 列表/详情/删除；支持多知识库 kb_id。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import get_authorizer
from app.api.utils import get_deps, ok
from app.core.errors import AuthError, NotFoundError, RateLimitError, ValidationError
from app.core.registry import Dependencies
from app.services.ingestion import compute_content_hash

router = APIRouter(prefix="/api/documents", tags=["documents"])


class BatchDeleteRequest(BaseModel):
    """批量删除请求体：一次最多 200 条（前端多选上限）。"""

    document_ids: list[str] = Field(default_factory=list, max_length=200)


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 kb_id: str = Form(default="default"),          # 13.5：目标知识库（缺省 default）
                 request: Request = None,
                 deps: Dependencies = Depends(get_deps)) -> dict:
    """上传 → 校验 → content_hash 幂等（按库）→ 创建 Job → 异步入库（5.10）。C4（V5.1）：上传速率限制（43002）。"""
    if request is not None:
        limiter = request.app.state.rate_limiters["upload"]
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise RateLimitError()
    if deps.pipeline is None:
        from app.core.errors import InternalError

        raise InternalError("上传流水线尚未装配")
    if deps.kbs is None or not deps.kbs.exists(kb_id):
        raise ValidationError(f"知识库不存在: {kb_id!r}")
    if request is not None:
        from app.api.auth import get_authorizer

        kb_info = deps.kbs.get(kb_id)
        if not get_authorizer(request).can_write_kb(getattr(request.state, "user", None), kb_info):
            raise AuthError("无权向该知识库上传文档（共享库仅管理员可写）")
    cfg = deps.config.ingestion
    filename = file.filename or "unnamed"
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in cfg.allowed_extensions:
        raise ValidationError(f"不支持的文档格式: {filename}（允许：{cfg.allowed_extensions}）")
    max_bytes = cfg.max_file_size_mb * 1024 * 1024
    if request is not None:
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise ValidationError(f"文件超过 {cfg.max_file_size_mb}MB 限制: {filename}")
            except ValueError:
                pass
    raw = await file.read(max_bytes + 1)   # C3：限量读取——无 content-length（chunked）请求也会被截断，防全量入内存 DoS
    if len(raw) > max_bytes:
        raise ValidationError(f"文件超过 {cfg.max_file_size_mb}MB 限制: {filename}")

    content_hash = compute_content_hash(raw)
    collection = deps.kbs.collection_of(kb_id)
    existing = deps.pipeline.find_document_id_by_hash(content_hash, collection=collection, kb_id=kb_id)
    if existing:
        # 幂等：重复上传返回既有 document_id，不重复入库（2.2；按库作用域）
        return ok({"duplicate": True, "document_id": existing, "kb_id": kb_id})

    job = deps.jobs.create(filename, kb_id=kb_id)
    deps.pipeline.submit(raw, filename, job, kb_id=kb_id, collection=collection)
    # S6 审计（13.11.1）：含 user_id/kb_id/文件名摘要，不含完整敏感文本与 key
    from app.api.auth import current_user
    from app.core.logging import get_logger, get_trace_id

    get_logger("audit").info(
        "upload_audit",
        extra={"extra_fields": {
            "user_id": current_user(request) if request else "anonymous",
            "kb_id": kb_id,
            "filename_summary": filename[:80],
            "size_bytes": len(raw),
            "job_id": job.job_id,
            "trace_id": get_trace_id(),
        }},
    )
    return ok({"job_id": job.job_id, "status": job.status, "kb_id": kb_id})


@router.get("")
def list_documents(page: int = 1, size: int = 20, kb_id: str | None = None,
                   request: Request = None,
                   deps: Dependencies = Depends(get_deps)) -> dict:
    """分页列表（按创建时间倒序，6.5）；V5.1：仅返回当前用户可访问库的文档。"""
    if deps.documents is None:
        return ok({"items": [], "total": 0})
    user = getattr(request.state, "user", None) if request else None
    authorizer = get_authorizer(request) if request else None
    accessible = set()
    if authorizer is not None and deps.kbs is not None:
        accessible = {k.kb_id for k in authorizer.list_accessible_kbs(user, deps.kbs.list())}
    if kb_id is not None:
        if deps.kbs is None or not deps.kbs.exists(kb_id):
            raise ValidationError(f"知识库不存在: {kb_id!r}")
        if accessible and kb_id not in accessible:
            raise NotFoundError(f"文档不存在或无权访问: kb={kb_id}")
    if accessible:
        all_items = deps.documents.list(page=1, size=10000, kb_id=kb_id)
        items = [e for e in all_items if e.get("kb_id") in accessible]
        return ok({"items": items[(page - 1) * size:(page - 1) * size + size],
                   "total": len(items)})
    return ok({"items": deps.documents.list(page, size, kb_id=kb_id),
               "total": deps.documents.count(kb_id=kb_id)})


@router.get("/{document_id}")
def get_document(document_id: str, request: Request = None,
                 deps: Dependencies = Depends(get_deps)) -> dict:
    """单文档元数据（V5.1：不可见库的文档一律 40401，不泄露存在性）。"""
    entry = deps.documents.get(document_id) if deps.documents else None
    if entry is None:
        raise NotFoundError(f"文档不存在: {document_id}")
    if request is not None and not _can_access_doc(request, entry.kb_id):
        raise NotFoundError(f"文档不存在: {document_id}")
    return ok({
        "document_id": entry.document_id,
        "filename": entry.filename,
        "title": entry.title,
        "chunk_count": entry.chunk_count,
        "kb_id": entry.kb_id,
        "created_at": entry.created_at,
    })


def _can_access_doc(request: Request, kb_id: str) -> bool:
    from app.api.auth import get_authorizer

    deps = request.app.state.deps
    info = deps.kbs.get(kb_id) if deps.kbs else None
    if info is None:
        return False
    return get_authorizer(request).can_read_kb(getattr(request.state, "user", None), info)


@router.delete("/{document_id}")
def delete_document(document_id: str, request: Request = None,
                    deps: Dependencies = Depends(get_deps)) -> dict:
    """删除文档（级联清向量块，6.5）；向量删除定位文档所属库，而非配置默认集合。"""
    entry = deps.documents.get(document_id) if deps.documents else None
    if entry is None:
        raise NotFoundError(f"文档不存在: {document_id}")
    if request is not None:
        info = deps.kbs.get(entry.kb_id) if deps.kbs else None
        if info is None or not get_authorizer(request).can_write_kb(
                getattr(request.state, "user", None), info):
            raise NotFoundError(f"文档不存在: {document_id}")
    collection = None
    if deps.kbs is not None:
        collection = deps.kbs.collection_of(entry.kb_id)
    deleted = 0
    if deps.vector_store is not None:
        deleted = deps.vector_store.delete_document(
            document_id, collection=collection or deps.config.vector_store.collection)
    if deps.documents is not None:
        deps.documents.delete(document_id)
    return ok({"document_id": document_id, "deleted_chunks": deleted})


@router.post("/batch-delete")
def batch_delete_documents(body: BatchDeleteRequest, request: Request = None,
                           deps: Dependencies = Depends(get_deps)) -> dict:
    """批量删除（级联清向量块，6.5）；与单条删除同权限语义：
    不存在的条目与无权访问的库一律记入 failed（不泄露存在性）。"""
    ids = body.document_ids
    if not ids:
        raise ValidationError("document_ids 不能为空")
    if len(ids) > 200:
        raise ValidationError("批量删除一次最多 200 条")
    deleted, failed = [], []
    for document_id in ids:
        entry = deps.documents.get(document_id) if deps.documents else None
        if entry is None:
            failed.append({"document_id": document_id, "reason_code": 40401})
            continue
        if request is not None:
            info = deps.kbs.get(entry.kb_id) if deps.kbs else None
            if info is None or not get_authorizer(request).can_write_kb(
                    getattr(request.state, "user", None), info):
                failed.append({"document_id": document_id, "reason_code": 40401})
                continue
        collection = None
        if deps.kbs is not None:
            collection = deps.kbs.collection_of(entry.kb_id)
        chunks = 0
        if deps.vector_store is not None:
            chunks = deps.vector_store.delete_document(
                document_id, collection=collection or deps.config.vector_store.collection)
        if deps.documents is not None:
            deps.documents.delete(document_id)
        deleted.append({"document_id": document_id, "deleted_chunks": chunks})
    return ok({"deleted": deleted, "failed": failed})
