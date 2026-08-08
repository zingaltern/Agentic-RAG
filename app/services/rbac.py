"""RBAC 预留接口（DEVELOPMENT.md 13.11.3 多租户扩展点、12.3 深度 C）。

W8 交付"接口 + 默认全放"实现：认证（S1）落地后，用户身份可从请求头
X-User-Id 透传（见 app/api/query.py 审计），本模块负责"用户→可访问 kb"的判定。
对接 §12.3 深度 C（真实权限诉求）时，替换默认策略或注入 user_kb_map 即可，路由层不改。
"""
from __future__ import annotations

import enum
from typing import Any


class Decision(enum.Enum):
    Allow = "allow"
    Deny = "deny"


class RbacPolicy:
    """用户 → 知识库访问判定（默认全放；可注入 user_kb_map 收窄）。"""

    def __init__(self, user_kb_map: dict[str, set[str]] | None = None):
        # user_kb_map：{"user_1": {"default"}} —— 仅列出可访问的 kb_id；None=全放
        self._user_kb_map = user_kb_map or {}

    def resolve_user_kb(self, user_id: str | None, kb_id: str) -> Decision:
        """判定 user_id 是否有权访问 kb_id（默认 Allow；配置了映射则按映射判定）。"""
        if not self._user_kb_map:
            return Decision.Allow
        allowed = self._user_kb_map.get(user_id or "")
        if allowed is None or kb_id in allowed:
            return Decision.Allow
        return Decision.Deny

    def is_enforced(self) -> bool:
        """是否已配置收窄映射（供路由决定是否实际拦截；默认不拦截）。"""
        return bool(self._user_kb_map)


# 全局单例（wire 可注入替换）；路由层通过依赖获取，禁止把鉴权写死进路由
_default_policy = RbacPolicy()


def get_rbac_policy() -> RbacPolicy:
    return _default_policy


def set_rbac_policy(policy: RbacPolicy) -> None:
    global _default_policy
    _default_policy = policy
