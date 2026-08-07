"""FastAPI 应用工厂（DEVELOPMENT.md 1.2、5.11）。

- create_app(settings_path, components=True)：加载 SystemConfig → wire() 装配 → 注入路由。
- components=False：不装配重型组件（单测/契约测试用，避免加载嵌入模型）。
- 中间件：X-Trace-Id 贯穿（2.3）+ 请求耗时 JSON 日志。
- 异常统一转 {code, message, data} 响应包（2.2）。
- 托管 /ui 内置测试页（第 7 章）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import documents, health, jobs, plugins, query
from app.api.utils import fail
from app.core.config import DEFAULT_SETTINGS_PATH, SystemConfig, load_config
from app.core.errors import AppError, InternalError
from app.core.logging import get_logger, set_trace_id, setup_logging
from app.core.registry import Dependencies, wire

APP_VERSION = "1.0.0"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = get_logger("main")


def create_app(settings_path: str = DEFAULT_SETTINGS_PATH,
               components: bool = True,
               config: SystemConfig | None = None,
               deps: Dependencies | None = None,
               **wire_kwargs: Any) -> FastAPI:
    """创建应用实例。

    - config：传入则以它为准（测试可改 persist_dir 等）。
    - deps：直接注入装配好的依赖（测试用）。
    - **wire_kwargs：透传给 wire（如 embedding=StubEmbedding()）。
    """
    if config is None:
        config = load_config(settings_path)
    setup_logging(level=config.logging.level, json=config.logging.json_enabled)
    if deps is None:
        deps = wire(config, components=components, **wire_kwargs)

    app = FastAPI(
        title="Agentic RAG 智能知识库系统",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.deps = deps
    app.state.config = config

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid4().hex[:12]
        set_trace_id(trace_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000)
            logger.info(
                "http_request",
                extra={"extra_fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                    "trace_id": trace_id,
                }},
            )
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=200, content=fail(exc.code, exc.message))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常", extra={"extra_fields": {"path": request.url.path}})
        return JSONResponse(status_code=200, content=fail(InternalError.code, InternalError.message))

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(query.router)
    app.include_router(plugins.router)

    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")
    return app
