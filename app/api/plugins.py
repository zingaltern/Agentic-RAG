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
        if any(key in payload.config for key in ("api_key", "secret")):
            raise ValidationError("不允许热切修改密钥类配置（api_key/secret）")
        from pydantic import ValidationError as PydanticValidationError

        module_cfg = getattr(new_config, payload.module)
        try:
            # 模型级重建：pydantic 对类型/约束完整校验，替代逐键 setattr
            updated = type(module_cfg)(**{**module_cfg.model_dump(), **payload.config})
        except PydanticValidationError as exc:
            raise ValidationError(f"非法配置项: {exc}") from exc
        setattr(new_config, payload.module, updated)

    try:
        # C4：按模块复用未切换组件——避免重载模型与泄漏 pipeline worker 线程。
        # 复用规则：被切换模块本身除外；**下游依赖模块变更时依赖方必须重建**
        # （retriever 依赖 embedding/vector_store；evaluator/detector 依赖 embedding；
        #  rewriter 依赖 llm；pipeline 依赖 embedding/vector_store），否则热切换对查询路径无效。
        old = deps

        def _reuse(name: str, depends_on: tuple = ()) -> object:
            if payload.module == name or payload.module in depends_on:
                return None
            return getattr(old, name)

        new_deps = wire(new_config, kbs=old.kbs, users=old.users,
                        documents=old.documents, jobs=old.jobs,
                        embedding=_reuse("embedding"),
                        vector_store=_reuse("vector_store"),
                        retriever=_reuse("retriever", depends_on=("embedding", "vector_store")),
                        llm=_reuse("llm"),
                        reranker=_reuse("reranker"),
                        evaluator=_reuse("evaluator", depends_on=("embedding",)),
                        rewriter=_reuse("rewriter", depends_on=("llm",)),
                        detector=_reuse("detector", depends_on=("embedding",)),
                        pipeline=_reuse("pipeline", depends_on=("embedding", "vector_store")))
        # C4：先原子替换 deps，再关闭旧 pipeline——避免 shutdown 窗口内新请求
        # 提交到将被停用的队列导致 job 永久 pending（shutdown 会拒绝新提交）
        request.app.state.deps = new_deps
        request.app.state.config = new_config
        if old.pipeline is not None and new_deps.pipeline is not old.pipeline:
            old.pipeline.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("热切换装配失败（已回滚，deps 不变）：%s", exc)
        raise InternalError(f"热切换装配失败（已回滚）: {exc}") from exc
    logger.info("热切换成功",
                extra={"extra_fields": {"module": payload.module,
                                        "provider": payload.strategy}})
    return ok({"module": payload.module, "provider": payload.strategy, "switched": True})
