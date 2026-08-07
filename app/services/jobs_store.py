"""Job 存储（DEVELOPMENT.md 5.10）：进程内 dict + threading.Lock。

MVP 语义：重启后 Job 状态丢失（可接受，生产再上 Redis/MQ）。
"""
from __future__ import annotations

import threading
import time
from uuid import uuid4

from app.core.domain import Job


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, filename: str) -> Job:
        now = time.time()
        job = Job(job_id=uuid4().hex[:8], filename=filename,
                  status="pending", created_at=now, updated_at=now)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def update(self, job_id: str, **fields) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)
