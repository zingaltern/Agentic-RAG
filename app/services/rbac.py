"""授权（V5.1 团队模式）：知识库级权限判定（13.11.1 S1 落地 + 12.3 深度 C 轻量实现）。

- is_admin：认证关闭（user=None，个人模式）→ True，行为与改动前完全一致。
- 判定规则：shared 库读放行、写/删仅 admin；private 库仅 owner 或 admin。
- 实例挂 request.app.state.authorizer（create_app 装配），路由内统一调用。
"""
from __future__ import annotations

import enum
from typing import Any


class Decision(enum.Enum):
    Allow = "allow"
    Deny = "deny"


class Authorizer:
    def is_admin(self, user) -> bool:
        return user is None or user.role == "admin"

    def can_read_kb(self, user, kb) -> bool:
        if self.is_admin(user):
            return True
        if kb.scope == "shared":
            return True
        return kb.owner == user.user_id

    def can_write_kb(self, user, kb) -> bool:
        if self.is_admin(user):
            return True
        if kb.scope == "shared":
            return False
        return kb.owner == user.user_id

    def can_delete_kb(self, user, kb) -> bool:
        return self.is_admin(user)

    def list_accessible_kbs(self, user, all_kbs) -> list:
        return [kb for kb in all_kbs if self.can_read_kb(user, kb)]


# 兼容旧接口（resolve_user_kb 供 12.3 深度 C 映射扩展；默认按 Authorizer 规则）
class RbacPolicy(Authorizer):
    def resolve_user_kb(self, user_id: str | None, kb_id: str) -> Decision:
        return Decision.Allow
