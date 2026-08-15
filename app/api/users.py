"""用户管理 API（V5.1 团队模式，仅 admin）：/api/users。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user
from app.api.utils import get_deps, ok
from app.core.errors import AuthError, NotFoundError, ValidationError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    password: str = Field(..., min_length=6, max_length=256)
    role: str = Field(default="user", pattern="^(admin|user)$")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=256)


class RoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


def require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        raise AuthError("需要管理员权限")


@router.get("")
def list_users(request: Request, deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    users = [{"user_id": u.user_id, "username": u.username, "name": u.name,
              "role": u.role, "created_at": u.created_at, "last_login": u.last_login}
             for u in deps.users.list()]
    return ok({"users": users, "total": len(users)})


@router.post("")
def create_user(payload: UserCreate, request: Request,
                deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    try:
        user = deps.users.create(payload.username, payload.password,
                                 name=payload.name, role=payload.role)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ok({"user_id": user.user_id, "username": user.username, "role": user.role})


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, payload: ResetPasswordRequest,
                   request: Request, deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    if not deps.users.reset_password(user_id, payload.new_password):
        raise NotFoundError(f"用户不存在: {user_id}")
    return ok({"user_id": user_id})


@router.post("/{user_id}/role")
def update_role(user_id: str, payload: RoleRequest,
                request: Request, deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    try:
        user = deps.users.update_role(user_id, payload.role)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if user is None:
        raise NotFoundError(f"用户不存在: {user_id}")
    return ok({"user_id": user_id, "role": user.role})


@router.delete("/{user_id}")
def delete_user(user_id: str, request: Request,
                deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    if user_id == current_user(request):
        raise ValidationError("不能删除当前登录账号")
    if not deps.users.delete(user_id):
        raise NotFoundError(f"用户不存在: {user_id}")
    return ok({"user_id": user_id})
