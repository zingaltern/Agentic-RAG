"""上传任务查询（6.3）：从 JobStore 读取（内存 dict + threading.Lock）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.utils import get_deps, ok
from app.core.errors import NotFoundError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, deps: Dependencies = Depends(get_deps)) -> dict:
    job = deps.jobs.get(job_id) if deps.jobs else None
    if job is None:
        raise NotFoundError(f"Job 不存在: {job_id}")
    return ok({
        "job_id": job.job_id,
        "status": job.status,
        "document_id": job.document_id,
        "chunk_count": job.chunk_count,
        "error_code": job.error_code,
    })
