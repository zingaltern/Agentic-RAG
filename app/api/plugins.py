"""插件状态与热切换（6.6）。W1：/status 读配置真实返回；/switch 骨架（W3 填实）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.utils import get_deps, ok
from app.core.errors import InternalError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# 各模块当前 provider 信息（配置驱动）
_MODULES = ("embedding", "vector_store", "retriever", "reranker",
            "evaluator", "rewriter", "detector", "llm")


@router.get("/status")
def status(deps: Dependencies = Depends(get_deps)) -> dict:
    cfg = deps.config
    providers = {m: getattr(cfg, m).provider for m in _MODULES}
    return ok({"modules": providers})


@router.post("/switch")
def switch(payload: dict) -> dict:
    # W3 交付：更新内存 config + 重建该模块单例（失败回滚并返回 50001）
    raise InternalError("插件热切换尚未实现（计划 W3 交付）")
