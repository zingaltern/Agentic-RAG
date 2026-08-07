"""API 公共工具：统一响应包（2.2）与依赖注入。"""
from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.registry import Dependencies


def ok(data: Any = None, message: str = "ok") -> dict:
    """统一成功响应包：{code:0, message, data}。"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str, data: Any = None) -> dict:
    """统一失败响应包（HTTP 200 + body code，前端/评测靠 code 判断）。"""
    return {"code": code, "message": message, "data": data if data is not None else {}}


def get_deps(request: Request) -> Dependencies:
    """从 app.state 取装配好的 Dependencies（create_app 时注入）。"""
    return request.app.state.deps
