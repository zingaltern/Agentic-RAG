"""健康检查（6.7）：探活各组件。

- embedding：实例已装配且 ready()（首次调用触发模型加载）→ ok。
- vector_store：实例已装配且可查询（count）→ ok。
- llm：装配即 ok；`?probe=1` 时额外做一次最小叫通（短请求，≤3s，结果缓存 60s），
  鉴权失败/超时 → down。桌面端启动探测使用轻量模式，避免被外部 API 阻塞。
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends

from app.api.utils import get_deps, ok
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/health", tags=["health"])

_PROBE_TTL_SECONDS = 60.0
_PROBE_LOCK = threading.Lock()
_PROBE_CACHE: dict[str, object] = {"at": 0.0, "llm": None, "ok": False, "error": ""}


def _probe_llm(llm) -> tuple[bool, str]:
    """对 LLM 做一次最小叫通；同实例 60s 内复用结果，避免设置页轮询打爆 provider。"""
    now = time.time()
    with _PROBE_LOCK:
        if (_PROBE_CACHE["llm"] is llm
                and now - float(_PROBE_CACHE["at"] or 0.0) < _PROBE_TTL_SECONDS):
            return bool(_PROBE_CACHE["ok"]), str(_PROBE_CACHE["error"] or "")

    probe = getattr(llm, "probe", None)
    if not callable(probe):
        # stub/自定义实现没有 probe 能力：保持“已装配”语义，不发起外部调用。
        return True, ""

    try:
        result = probe()
        if isinstance(result, tuple):
            ok_flag, error = bool(result[0]), str(result[1] if len(result) > 1 else "")
        else:
            ok_flag, error = bool(result), ""
    except Exception as exc:  # noqa: BLE001
        ok_flag, error = False, f"{type(exc).__name__}: {str(exc)[:200]}"

    with _PROBE_LOCK:
        _PROBE_CACHE.update({"at": time.time(), "llm": llm,
                            "ok": ok_flag, "error": error})
    return ok_flag, error


def _reset_probe_cache() -> None:
    """测试辅助：清空缓存，避免用例间互相影响。"""
    with _PROBE_LOCK:
        _PROBE_CACHE.update({"at": 0.0, "llm": None, "ok": False, "error": ""})


@router.get("")
def health(probe: bool = False, deps: Dependencies = Depends(get_deps)) -> dict:
    checks: dict[str, str] = {}

    if deps.embedding is not None:
        # 只读探活：不触发模型加载（加载在启动期后台预热，避免阻塞 health 秒回）
        if hasattr(deps.embedding, "is_loaded"):
            try:
                checks["embedding"] = "ok" if deps.embedding.is_loaded else "not_loaded"
            except Exception:
                checks["embedding"] = "down"
        else:
            # 旧 stub/自定义实现无 is_loaded：回退 ready()（可能触发加载，仅兼容场景）
            try:
                checks["embedding"] = "ok" if deps.embedding.ready() else "not_loaded"
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

    if deps.llm is None:
        checks["llm"] = "not_loaded"
    elif probe:
        checks["llm"] = "ok" if _probe_llm(deps.llm)[0] else "down"
    else:
        checks["llm"] = "ok"

    if all(v == "ok" for v in checks.values()):
        status = "ok"
    elif "down" in checks.values():
        status = "down"
    else:
        status = "degraded"
    # 实际装配态而非仅配置值：agent.enabled=true 但 runtime 未装配（如无 LLM）时前端仍应提示。
    features = {
        "agent_enabled": deps.agent_runtime is not None,
        "web_enabled": bool(getattr(deps.config, "web", None)
                            and deps.config.web.enabled),
        "llm_probe": bool(probe),
    }
    return ok({"status": status, "checks": checks,
               "features": features, "version": "1.0.0"})
