"""认证（V5.1 团队模式）：密码登录 + HMAC-SHA256 Token（纯标准库）。

- Token payload {uid, username, role, exp}，签名密钥 RAG_AUTH_SECRET 优先，
  否则运行时生成并持久化 data/secret.key（0o600）。
- require_auth：解析 Authorization: Bearer <token> 挂到 request.state.user；
  auth_enabled=false（个人模式）一律放行且 user=None。
- 白名单 /api/health、/api/plugins、/ui、/assets、/api/auth 免认证。
- current_user 供 S6 审计：认证态返回 user_id，否则 "anonymous"。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.errors import AuthError, ValidationError

AUTH_WHITELIST_PREFIXES = ("/api/health", "/ui", "/assets", "/api/auth")
AUTH_WHITELIST_PATHS = ("/api/plugins/status",)   # 插件状态只读豁免；switch 为写操作必须认证
SECRET_FILE = Path("data/secret.key")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class TokenAuth:
    def __init__(self, secret: str | None = None):
        self._secret = secret or self._load_or_create_secret()

    @staticmethod
    def _load_or_create_secret() -> str:
        if SECRET_FILE.exists():
            try:
                return SECRET_FILE.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_hex(32)
        SECRET_FILE.write_text(secret, encoding="utf-8")
        os.chmod(SECRET_FILE, 0o600)
        return secret

    def create_token(self, user_id: str, username: str, role: str,
                     ttl_days: int = 7, now: float | None = None) -> str:
        now = now if now is not None else time.time()
        payload = {"uid": user_id, "username": username, "role": role,
                   "exp": int(now) + ttl_days * 86400}
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(self._secret.encode("utf-8"), body.encode("ascii"),
                       hashlib.sha256).digest()
        return f"{body}.{_b64url(sig)}"

    def verify_token(self, token: str) -> dict[str, Any]:
        try:
            body, sig = token.split(".", 1)
            expected = hmac.new(self._secret.encode("utf-8"), body.encode("ascii"),
                                hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64url_decode(sig)):
                raise AuthError("Token 签名无效")
            payload = json.loads(_b64url_decode(body).decode("utf-8"))
            if int(payload.get("exp", 0)) < int(time.time()):
                raise AuthError("Token 已过期")
            return payload
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(f"Token 解析失败: {exc}") from exc


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=256)


def _user_dict(user) -> dict:
    return {"user_id": user.user_id, "username": user.username,
            "name": user.name, "role": user.role}


@router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict:
    from app.api.utils import fail, ok

    config = request.app.state.config
    if not config.api.auth_enabled:
        return fail(ValidationError.code, "认证未启用（个人模式）")
    store = request.app.state.deps.users
    user = store.authenticate(payload.username, payload.password)
    if user is None:
        raise AuthError("用户名或密码错误")
    token_auth: TokenAuth = request.app.state.token_auth
    token = token_auth.create_token(user.user_id, user.username, user.role,
                                    ttl_days=config.auth.token_ttl_days)
    expires_at = int(time.time()) + config.auth.token_ttl_days * 86400
    return ok({"token": token, "expires_at": expires_at,
               "user": _user_dict(user)})


@router.get("/me")
def me(request: Request) -> dict:
    from app.api.utils import fail, ok

    user = getattr(request.state, "user", None)
    if user is None:
        return fail(ValidationError.code, "未登录")
    return ok({"user": _user_dict(user)})


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, request: Request) -> dict:
    from app.api.utils import ok

    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthError("未登录")
    store = request.app.state.deps.users
    if store.authenticate(user.username, payload.old_password) is None:
        raise ValidationError("旧密码错误")
    store.reset_password(user.user_id, payload.new_password)
    return ok({"user_id": user.user_id})


async def require_auth(request: Request) -> None:
    """FastAPI 依赖：Bearer Token 解析 → request.state.user（个人模式放行 None）。"""
    config = request.app.state.config
    path = request.url.path
    exempt = not config.api.auth_enabled or path in AUTH_WHITELIST_PATHS or \
        any(path.startswith(p) for p in AUTH_WHITELIST_PREFIXES)
    auth_header = request.headers.get("Authorization", "")
    if exempt:
        # 白名单/个人模式：带有效 Bearer 则挂载（如 /api/auth/me），否则 None
        request.state.user = None
        if auth_header.startswith("Bearer "):
            try:
                token_auth: TokenAuth = request.app.state.token_auth
                payload = token_auth.verify_token(auth_header[7:].strip())
                request.state.user = request.app.state.deps.users.get_by_id(payload.get("uid", ""))
            except AuthError:
                request.state.user = None
        return
    if not auth_header.startswith("Bearer "):
        raise AuthError("缺失或无效的登录凭证（Authorization: Bearer <token>）")
    token_auth: TokenAuth = request.app.state.token_auth
    payload = token_auth.verify_token(auth_header[7:].strip())
    store = request.app.state.deps.users
    user = store.get_by_id(payload.get("uid", ""))
    if user is None:
        raise AuthError("用户不存在或已被删除")
    request.state.user = user


def current_user(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return user.user_id if user else "anonymous"


def get_authorizer(request: Request):
    return request.app.state.authorizer
