"""知识库管理（DEVELOPMENT.md 13.5 + V5.1 团队模式）：/api/kbs CRUD + 权限。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import get_authorizer
from app.api.utils import get_deps, ok
from app.core.errors import AuthError, InternalError, NotFoundError, ValidationError
from app.core.registry import Dependencies
from app.services.kb_store import DEFAULT_KB_ID

router = APIRouter(prefix="/api/kbs", tags=["kbs"])


class KbCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="知识库名称")
    description: str = Field(default="", max_length=256)
    scope: str = Field(default="shared", pattern="^(shared|private)$", description="V5.1：shared|private")


def _kb_dict(info) -> dict:
    return {
        "kb_id": info.kb_id,
        "name": info.name,
        "description": info.description,
        "collection": info.collection,
        "owner": info.owner,
        "scope": info.scope,
        "created_at": info.created_at,
    }


@router.get("")
def list_kbs(request: Request, deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    user = getattr(request.state, "user", None)
    accessible = get_authorizer(request).list_accessible_kbs(user, deps.kbs.list())
    items = [_kb_dict(k) for k in accessible]
    return ok({"kbs": items, "total": len(items)})


@router.post("")
def create_kb(payload: KbCreate, request: Request,
              deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    user = getattr(request.state, "user", None)
    authorizer = get_authorizer(request)
    # V5.1：普通用户只能建私有库（服务端强制），owner=当前用户
    if authorizer.is_admin(user):
        owner, scope = "admin", payload.scope
    else:
        owner, scope = user.user_id, "private"
    try:
        info = deps.kbs.create(payload.name, payload.description, owner=owner, scope=scope)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if deps.vector_store is not None:
        deps.vector_store.count(info.collection)
    return ok(_kb_dict(info))


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, request: Request,
              deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    user = getattr(request.state, "user", None)
    if kb_id == DEFAULT_KB_ID:
        raise ValidationError("默认知识库不可删除")
    info = deps.kbs.get(kb_id)
    if info is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")
    if not get_authorizer(request).can_delete_kb(user, info):
        raise AuthError("无权删除该知识库")
    from app.core.logging import get_logger

    if deps.vector_store is not None:
        dropped = deps.vector_store.drop_collection(info.collection)
        if not dropped:
            get_logger("kbs").warning("知识库删除：集合清理失败/超时（%s），映射仍删除", info.collection)
    deps.kbs.delete(kb_id)
    return ok({"kb_id": kb_id})
