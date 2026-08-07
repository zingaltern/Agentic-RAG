"""健康检查（6.7）：探活各组件。

- embedding：实例已装配且 ready()（首次调用触发模型加载）→ ok。
- vector_store：实例已装配且可查询（count）→ ok。
- llm：W3 装配前恒为 not_loaded。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.utils import get_deps, ok
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(deps: Dependencies = Depends(get_deps)) -> dict:
    checks: dict[str, str] = {}

    if deps.embedding is not None:
        ready = getattr(deps.embedding, "ready", lambda: True)
        try:
            checks["embedding"] = "ok" if ready() else "not_loaded"
        except Exception:
            checks["embedding"] = "down"
    else:
        checks["embedding"] = "not_loaded"

    if deps.vector_store is not None:
        try:
            deps.vector_store.count()
            checks["vector_store"] = "ok"
        except Exception:
            checks["vector_store"] = "down"
    else:
        checks["vector_store"] = "not_loaded"

    checks["llm"] = "ok" if deps.llm is not None else "not_loaded"

    if all(v == "ok" for v in checks.values()):
        status = "ok"
    elif "down" in checks.values():
        status = "down"
    else:
        status = "degraded"
    return ok({"status": status, "checks": checks, "version": "1.0.0"})
