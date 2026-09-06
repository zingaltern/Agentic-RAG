"""Job 存储（DEVELOPMENT.md 5.10 + V5.1 修订）：进程内 dict + 轻量持久化。

- 仅落盘未终态任务（pending/running）到 data/jobs.json；终态（success/failed）移除。
- **重启语义（方案 A）**：启动加载残留未终态 Job 并统一标记 failed（error_code=50003
  「服务重启，任务中断」），前端轮询收到明确终止而非 404/超时误报。
- 生产可换 Redis/MQ（接口不变）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from uuid import uuid4

from app.core.domain import Job
from app.core.logging import get_logger
from app.core.persist import atomic_write_json

logger = get_logger("jobs_store")

INTERRUPTED_CODE = "50003"          # C6：服务重启，任务中断
DEFAULT_PERSIST_FILE = "data/jobs.json"


class JobStore:
    def __init__(self, persist_file: str = DEFAULT_PERSIST_FILE):
        self._persist_file = persist_file
        self._jobs: dict[str, Job] = {}
        self._pending: dict[str, Job] = {}     # 未终态任务（持久化集合）
        self._lock = threading.Lock()
        self._load_and_mark_interrupted()

    # -- 持久化（C6 方案 A） ------------------------------------------------------

    def _load_and_mark_interrupted(self) -> None:
        path = Path(self._persist_file)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for item in raw:
                    job = Job(
                        job_id=item["job_id"], filename=item.get("filename", ""),
                        status=item.get("status", "pending"), document_id=item.get("document_id"),
                        chunk_count=item.get("chunk_count", 0),
                        error_code=item.get("error_code"), kb_id=item.get("kb_id"),
                        created_at=item.get("created_at", 0.0), updated_at=item.get("updated_at", 0.0), stage_timings=item.get("stage_timings") or {},
                    )
                    self._jobs[job.job_id] = job
                    if job.status in ("pending", "running"):
                        # 重启残留：明确标记中断，避免前端 404/超时误报
                        job.status = "failed"
                        job.error_code = INTERRUPTED_CODE
                        job.updated_at = time.time()
                        logger.warning("重启恢复：Job %s 标记为中断（%s）", job.job_id, INTERRUPTED_CODE)
            except Exception as exc:  # noqa: BLE001
                logger.warning("jobs.json 读取失败（%s）：%s，按空任务表启动", path, exc)
        self._persist()

    def _persist(self) -> None:
        try:
            payload = [self._to_dict(j) for j in self._pending.values()]
            atomic_write_json(self._persist_file, payload)   # S7：原子写 + 0o600
        except Exception as exc:  # noqa: BLE001
            logger.warning("jobs.json 写入失败（降级内存态）：%s", exc)

    @staticmethod
    def _to_dict(job: Job) -> dict:
        return {"job_id": job.job_id, "filename": job.filename, "status": job.status,
                "document_id": job.document_id, "chunk_count": job.chunk_count,
                "error_code": job.error_code, "kb_id": job.kb_id,
                "created_at": job.created_at, "updated_at": job.updated_at,
                "stage_timings": job.stage_timings}

    # -- CRUD -------------------------------------------------------------------

    def create(self, filename: str, kb_id: str | None = None) -> Job:
        now = time.time()
        job = Job(job_id=uuid4().hex[:8], filename=filename,
                  status="pending", kb_id=kb_id, created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.job_id] = job
            self._pending[job.job_id] = job
        self._persist()
        return job

    def update(self, job_id: str, **fields) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            if job.status in ("pending", "running"):
                self._pending[job.job_id] = job
            else:
                self._pending.pop(job.job_id, None)     # 终态出盘
        self._persist()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)
