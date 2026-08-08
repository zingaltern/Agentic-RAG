"""知识库管理（DEVELOPMENT.md 13.5）：/api/kbs CRUD。

- GET 列表（含 default）；POST {name, description?} → {kb_id}（新建物理集合）；
- DELETE 级联（drop_collection + 删映射）；拒删 default（40001）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.utils import get_deps, ok
from app.core.errors import InternalError, NotFoundError, ValidationError
from app.core.registry import Dependencies
from app.services.kb_store import DEFAULT_KB_ID

router = APIRouter(prefix="/api/kbs", tags=["kbs"])


class KbCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="知识库名称")
    description: str = Field(default="", max_length=256)


def _kb_dict(info) -> dict:
    return {
        "kb_id": info.kb_id,
        "name": info.name,
        "description": info.description,
        "collection": info.collection,
        "created_at": info.created_at,
    }


@router.get("")
def list_kbs(deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    items = [_kb_dict(k) for k in deps.kbs.list()]
    return ok({"kbs": items, "total": len(items)})


@router.post("")
def create_kb(payload: KbCreate, deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    try:
        info = deps.kbs.create(payload.name, payload.description)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    # 物理隔离：确保 Chroma 集合存在（count 触发 get_or_create；组件未装配时跳过）
    if deps.vector_store is not None:
        deps.vector_store.count(info.collection)
    return ok(_kb_dict(info))


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")
    if kb_id == DEFAULT_KB_ID:
        raise ValidationError("默认知识库不可删除")
    info = deps.kbs.get(kb_id)
    if info is None:
        raise NotFoundError(f"知识库不存在: {kb_id}")
    # 级联：先清物理集合，再删映射
    if deps.vector_store is not None:
        deps.vector_store.drop_collection(info.collection)
    deps.kbs.delete(kb_id)
    return ok({"kb_id": kb_id})
