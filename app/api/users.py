"""用户管理 API（V5.1 团队模式，仅 admin）：/api/users。

- 单个：GET/POST /api/users、POST /{id}/reset-password|role、DELETE /{id}。
- 批量：POST /api/users/import —— CSV（utf-8-sig，表头 用户名,姓名,初始密码,角色）：
  先全量校验（行格式/用户名重复/已存在/密码长度/角色），任一失败则整批不写入并返回逐行明细。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.auth import current_user
from app.api.utils import get_deps, ok
from app.core.errors import AuthError, NotFoundError, ValidationError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/users", tags=["users"])

MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 1024 * 1024


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="", max_length=64)
    password: str = Field(..., min_length=8, max_length=256)   # S5：≥8 位 + 复杂度见 user_store
    role: str = Field(default="user", pattern="^(admin|user)$")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=256)   # S5


class RoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


def require_admin(request: Request) -> None:
    """V5.1：与 Authorizer.is_admin 一致——个人模式（user=None）视为管理员（40101 修正是为团队模式）。"""
    from app.api.auth import get_authorizer

    user = getattr(request.state, "user", None)
    if not get_authorizer(request).is_admin(user):
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
    from app.services.user_store import validate_password_strength

    reason = validate_password_strength(payload.password)
    if reason:
        raise ValidationError(reason)
    try:
        user = deps.users.create(payload.username, payload.password,
                                 name=payload.name, role=payload.role)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ok({"user_id": user.user_id, "username": user.username, "role": user.role})


@router.post("/import")
async def import_users(file: UploadFile = File(...),
                       request: Request = None,
                       deps: Dependencies = Depends(get_deps)) -> dict:
    """CSV 批量导入：全量校验通过才写入；返回逐行明细（{ok: [{username,role}], errors: [{row,message}]}）。"""
    require_admin(request)
    from app.services.user_store import validate_password_strength

    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValidationError(f"导入文件过大（上限 1MB）")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("文件编码须为 UTF-8（可用模板另存后再填）") from None

    rows = []
    for line_no, cells in enumerate(csv.reader(io.StringIO(text)), start=1):
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        rows.append((line_no, cells))

    if not rows:
        raise ValidationError("文件为空，无可导入的用户")
    first = rows[0][1]
    if first and first[0] in ("用户名", "username"):
        rows = rows[1:]
    if not rows:
        raise ValidationError("文件只有表头，没有用户数据")

    errors: list[dict] = []
    prepared: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for line_no, cells in rows:
        username = cells[0] if len(cells) > 0 else ""
        name = cells[1] if len(cells) > 1 else ""
        password = cells[2] if len(cells) > 2 else ""
        role = (cells[3] if len(cells) > 3 else "user").lower()
        if not username:
            errors.append({"row": line_no, "message": "用户名不能为空"})
            continue
        if len(username) > 64:
            errors.append({"row": line_no, "message": "用户名过长（≤64 字符）"})
            continue
        if username in seen:
            errors.append({"row": line_no, "message": f"文件内用户名重复：{username}"})
            continue
        if deps.users.get_by_username(username) is not None:
            errors.append({"row": line_no, "message": f"用户名已存在：{username}"})
            continue
        reason = validate_password_strength(password)
        if reason:
            errors.append({"row": line_no, "message": f"初始密码不合格：{reason}"})
            continue
        if role not in ("admin", "user"):
            errors.append({"row": line_no, "message": f"角色须为 admin 或 user：{role}"})
            continue
        seen.add(username)
        prepared.append((username, password, name, role))

    if errors:
        detail = "；".join(f"第 {e['row']} 行：{e['message']}" for e in errors[:5])
        raise ValidationError(f"导入失败（{len(errors)} 处问题，未写入任何用户）：{detail}",
                              data={"errors": errors})

    created = []
    for username, password, name, role in prepared:
        user = deps.users.create(username, password, name=name, role=role)
        created.append({"username": user.username, "role": user.role})
    return ok({"created": len(created), "users": created, "errors": errors})


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, payload: ResetPasswordRequest,
                   request: Request, deps: Dependencies = Depends(get_deps)) -> dict:
    require_admin(request)
    from app.services.user_store import validate_password_strength

    reason = validate_password_strength(payload.new_password)
    if reason:
        raise ValidationError(reason)
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
    # 先确认用户存在，再执行级联（避免对不存在用户的孤儿库静默清理被 404 掩盖）
    if deps.users.get_by_id(user_id) is None:
        raise NotFoundError(f"用户不存在: {user_id}")
    # S6：级联清理该用户的私有库（drop_collection + kb 映射 + 文档索引），避免孤儿库残留
    if deps.kbs is not None:
        for info in list(deps.kbs.list()):
            if info.owner == user_id and info.scope == "private":
                if deps.vector_store is not None:
                    deps.vector_store.drop_collection(info.collection)
                if deps.documents is not None:
                    deps.documents.delete_by_kb(info.kb_id)
                deps.kbs.delete(info.kb_id)
    deps.users.delete(user_id)
    return ok({"user_id": user_id})
