"""阶段级指标端点（P2-7，DEVELOPMENT.md 10.2）：Prometheus exposition 文本输出。

权限：登录 + 仅管理员（模仿 /api/plugins/switch 模式）；非 admin 40101。
无 Prometheus 部署时可用 curl 人工观测（零前端依赖）。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.api.auth import get_authorizer
from app.api.utils import get_deps
from app.core.errors import AuthError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
def metrics_endpoint(request: Request,
                     deps: Dependencies = Depends(get_deps)) -> Response:
    user = getattr(request.state, "user", None)
    if not get_authorizer(request).is_admin(user):
        raise AuthError("需要管理员权限")
    from app.core.metrics import render_exposition

    return Response(content=render_exposition(),
                    media_type="text/plain; version=0.0.4; charset=utf-8")
