"""插件状态与热切换（6.6、5.11、13.11.3）。

- /status：按配置真实返回各模块当前 provider（只读，白名单豁免认证）。
- /switch：运行期热切换某模块 provider —— 深拷贝 config → wire 重建依赖 → 原子替换；
  校验失败（未知模块/provider、未注册实现）→ 40001；装配失败 → 50001 且不替换（回滚语义）。
  写操作：需登录且 V5.1 仅管理员可用。
"""
from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import require_auth
from app.api.utils import get_deps, ok
from app.core.config import ALLOWED_PROVIDERS
from app.core.errors import InternalError, ValidationError
from app.core.logging import get_logger
from app.core.registry import Dependencies, PluginRegistry, wire

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# 各模块当前 provider 信息（配置驱动）
_MODULES = ("embedding", "vector_store", "retriever", "reranker",
            "evaluator", "rewriter", "detector", "llm")

logger = get_logger("plugins")


@router.get("/status")
def status(deps: Dependencies = Depends(get_deps)) -> dict:
    cfg = deps.config
    providers = {m: getattr(cfg, m).provider for m in _MODULES}
    return ok({"modules": providers})


class SwitchRequest(BaseModel):
    module: str = Field(..., min_length=1, max_length=32)
    strategy: str = Field(..., min_length=1, max_length=64)
    config: dict | None = None


@router.post("/switch")
def switch(payload: SwitchRequest, request: Request,
           deps: Dependencies = Depends(get_deps),
           _: None = Depends(require_auth)) -> dict:
    """热切换：校验 → 深拷贝 config → wire 重建 → 原子替换；失败回滚（deps 不变）。"""
    from app.api.auth import get_authorizer

    user = getattr(request.state, "user", None)
    if not get_authorizer(request).is_admin(user):
        from app.core.errors import AuthError

        raise AuthError("需要管理员权限")
    if payload.module not in ALLOWED_PROVIDERS:
        raise ValidationError(
            f"未知模块: {payload.module!r}（可选：{sorted(ALLOWED_PROVIDERS)}）")
    allowed = ALLOWED_PROVIDERS[payload.module]
    if payload.strategy not in allowed:
        raise ValidationError(
            f"{payload.module} 不支持的 provider: {payload.strategy!r}（可选：{sorted(allowed)}）")
    try:
        PluginRegistry.get(f"{payload.module}.{payload.strategy}")
    except KeyError:
        raise ValidationError(
            f"provider {payload.strategy!r}（{payload.module}）尚未装配实现")

    new_config = deepcopy(deps.config)
    getattr(new_config, payload.module).provider = payload.strategy
    if payload.config:
        for key, value in payload.config.items():
            setattr(getattr(new_config, payload.module), key, value)

    try:
        new_deps = wire(new_config, kbs=deps.kbs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("热切换装配失败（已回滚，deps 不变）：%s", exc)
        raise InternalError(f"热切换装配失败（已回滚）: {exc}") from exc

    request.app.state.deps = new_deps
    request.app.state.config = new_config
    logger.info("热切换成功",
                extra={"extra_fields": {"module": payload.module,
                                        "provider": payload.strategy}})
    return ok({"module": payload.module, "provider": payload.strategy, "switched": True})
