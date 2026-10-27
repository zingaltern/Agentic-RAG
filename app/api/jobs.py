"""上传任务查询（6.3）：从 JobStore 读取（内存 dict + threading.Lock）。

S5（V5.1）：job 按所属知识库做作用域校验——不可读该库的用户视为"不存在"
（40401，不泄露 job/document 存在性）；个人模式（无用户）视为 admin 放行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import get_authorizer
from app.api.utils import get_deps, ok
from app.core.errors import NotFoundError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, request: Request,
            deps: Dependencies = Depends(get_deps)) -> dict:
    job = deps.jobs.get(job_id) if deps.jobs else None
    if job is None:
        raise NotFoundError(f"Job 不存在: {job_id}")
    if job.kb_id and deps.kbs is not None:
        info = deps.kbs.get(job.kb_id)
        if info is None or not get_authorizer(request).can_read_kb(
                getattr(request.state, "user", None), info):
            raise NotFoundError(f"Job 不存在: {job_id}")
    return ok({
        "job_id": job.job_id,
        "status": job.status,
        "document_id": job.document_id,
        "chunk_count": job.chunk_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "replaced_document_id": job.replaced_document_id,   # V5.2：增量更新替换的旧版本
    })
