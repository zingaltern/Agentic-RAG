"""API Key 认证（DEVELOPMENT.md 13.11.1 S1）。

- auth_enabled=true 时：可信 X-API-Key 与注入密钥（环境变量 RAG_API_KEY）比较；
  未带/错误 → AuthError（40101，保持 {code,message,data} 契约）。
- health/plugins 白名单放行（探活与 provider 状态不要求凭据）。
- 密钥只走环境变量注入（config.api.api_key），禁止写死/入库；恒定时间比较。
"""
from __future__ import annotations

import secrets

from fastapi import Request

from app.core.errors import AuthError

# 认证白名单路径前缀（探活/插件状态不要求凭据）
AUTH_WHITELIST_PREFIXES = ("/api/health", "/api/plugins", "/ui", "/assets")


async def require_auth(request: Request) -> None:
    """FastAPI 依赖：校验 X-API-Key（auth_enabled=true 且非白名单时）。"""
    config = request.app.state.config
    if not config.api.auth_enabled:
        return
    path = request.url.path
    if any(path.startswith(p) for p in AUTH_WHITELIST_PREFIXES):
        return
    expected = config.api.api_key
    if not expected:
        raise AuthError("认证已启用但未配置 RAG_API_KEY 环境变量")
    provided = request.headers.get("X-API-Key", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise AuthError("无效或缺失 API Key（X-API-Key）")


def current_user(request: Request) -> str:
    """审计用用户标识（S6）：X-User-Id 透传，缺省 anonymous；RBAC 对接预留。"""
    return request.headers.get("X-User-Id", "anonymous")
