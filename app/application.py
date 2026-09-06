"""FastAPI 应用工厂（DEVELOPMENT.md 1.2、5.11）。

- create_app(settings_path, components=True)：加载 SystemConfig → wire() 装配 → 注入路由。
- components=False：不装配重型组件（单测/契约测试用，避免加载嵌入模型）。
- 中间件：X-Trace-Id 贯穿（2.3）+ 请求耗时 JSON 日志。
- 异常统一转 {code, message, data} 响应包（2.2）。
- 托管 /ui 内置测试页（第 7 章）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import documents, health, jobs, kbs, metrics, plugins, query, users
from app.api.auth import TokenAuth, require_auth, router as auth_router
from app.api.auth import _LoginGuard
from app.api.utils import fail
from app.core.config import DEFAULT_SETTINGS_PATH, SystemConfig, load_config
from app.core.errors import AppError, InternalError, ValidationError
from app.core.logging import get_logger, set_trace_id, setup_logging
from app.core.registry import Dependencies, wire
from app.services.rbac import Authorizer

APP_VERSION = "1.0.0"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
logger = get_logger("main")


def create_app(settings_path: str = DEFAULT_SETTINGS_PATH,
               components: bool = True,
               config: SystemConfig | None = None,
               deps: Dependencies | None = None,
               prewarm: bool = True,
               **wire_kwargs: Any) -> FastAPI:
    """创建应用实例。

    - config：传入则以它为准（测试可改 persist_dir 等）。
    - deps：直接注入装配好的依赖（测试用）。
    - prewarm：是否后台预热真实模型（默认 True；单测传 False 避免并发加载真实模型卡死）。
    - **wire_kwargs：透传给 wire（如 embedding=StubEmbedding()）。
    """
    if config is None:
        config = load_config(settings_path)
    setup_logging(level=config.logging.level, json=config.logging.json_enabled)
    if deps is None:
        deps = wire(config, components=components, **wire_kwargs)

    if components and prewarm:
        # 启动加速：模型后台预热（health 不受阻，首次提问不冷加载）
        threading.Thread(target=prewarm_models, args=(deps,), daemon=True).start()

    app = FastAPI(
        title="Agentic RAG 智能知识库系统",
        version=APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.deps = deps
    app.state.config = config
    app.state.token_auth = TokenAuth(config.auth.secret)
    app.state.authorizer = Authorizer()
    app.state.login_guard = _LoginGuard()
    app.state.query_semaphore = threading.BoundedSemaphore(config.orchestration.max_concurrent_queries)  # C3
    from app.core.rate_limit import SlidingWindowRateLimiter

    app.state.rate_limiters = {          # C4：进程内滑动窗口限速（多 worker 各进程独立计数）
        "query": SlidingWindowRateLimiter(config.api.query_rate_per_min),
        "upload": SlidingWindowRateLimiter(config.api.upload_rate_per_min),
    }

    # V5.1 首管理员引导：auth_enabled=true 且用户表为空时，用环境变量创建 admin
    if config.api.auth_enabled and deps.users is not None and deps.users.is_empty():
        if config.auth.admin_user and config.auth.admin_password:
            deps.users.create(config.auth.admin_user, config.auth.admin_password,
                              name="系统管理员", role="admin")
            logger.info("已通过环境变量创建首管理员: %s", config.auth.admin_user)
        else:
            logger.warning("认证已启用但用户表为空：请配置 RAG_ADMIN_USER/RAG_ADMIN_PASSWORD "
                           "后重启，或通过 CLI 创建首管理员")

    # 6.5 兜底重建：documents.json 缺失/为空但 Chroma 有数据时，全量重建文档索引
    # （幽灵文档恢复；空索引才重建，已有数据不覆盖，异常不影响启动）
    if components and deps.documents is not None:
        try:
            rebuilt = deps.documents.rebuild_from_vector_store(deps.vector_store, deps.kbs)
            if rebuilt:
                logger.info("已从 Chroma 兜底重建文档索引: %s 个文档", rebuilt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("文档索引兜底重建失败（不影响启动）: %s", exc)

    # 13.8 CORS：dev 模式 5173 跨域白名单（生产同源不受影响）
    if config.api.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.api.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", "X-Trace-Id"],
        )

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
        # S4（V5.1）：安全响应头（CSP 允许内联样式——Element Plus 动态样式；构建产物无 eval）
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'",
        )
        return response

    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 404/405 等框架级错误同样走统一 {code, message, data} 契约（40440/40500）
        self_code = 40440 if exc.status_code == 404 else 40500 if exc.status_code == 405 else 50001
        return JSONResponse(status_code=exc.status_code, content=fail(self_code, str(exc.detail)))

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=200, content=fail(exc.code, exc.message, exc.data))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 参数校验失败 → 40001（6.7 错误码表）；pydantic 英文原文人话化，不暴露内部细节
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "")
        for en, zh in (("String should have at least 1 character", "不能为空"),
                       ("String should have at most", "内容过长"),
                       ("Input should be a valid integer", "需为数字"),
                       ("Input should be a valid boolean", "需为布尔值")):
            if en in msg:
                msg = zh
                break
        return JSONResponse(status_code=200, content=fail(
            ValidationError.code, f"参数校验失败: {loc} {msg}"))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常", extra={"extra_fields": {"path": request.url.path}})
        return JSONResponse(status_code=200, content=fail(InternalError.code, InternalError.message))

    app.include_router(health.router)
    app.include_router(documents.router, dependencies=[Depends(require_auth)])
    app.include_router(jobs.router, dependencies=[Depends(require_auth)])
    app.include_router(query.router, dependencies=[Depends(require_auth)])
    app.include_router(plugins.router, dependencies=[Depends(require_auth)])
    app.include_router(kbs.router, dependencies=[Depends(require_auth)])
    app.include_router(users.router, dependencies=[Depends(require_auth)])
    app.include_router(metrics.router, dependencies=[Depends(require_auth)])
    app.include_router(auth_router, dependencies=[Depends(require_auth)])

    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")

    # 13.7 生产托管：vite build 产物经 FastAPI 挂载（路由已注册在前，/api/* 不被吞）；
    # SPA history 路由回退到 index.html（非静态文件路径）；
    # /ui（旧测试页）目录路径由 catch-all 兜底（新版 Starlette Mount 对无尾斜杠目录不匹配）
    _dist = Path(config.frontend.dist_dir)
    if _dist.is_dir():
        _assets = _dist / "assets"
        if _assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets)), name="spa-assets")

        @app.middleware("http")
        async def no_cache_static(request: Request, call_next) -> Any:
            """SPA 静态资源禁用缓存：vite rebuild 后桌面 Electron 拖缓存旧页面。"""
            resp = await call_next(request)
            if not resp.headers.get("Cache-Control"):
                path = request.url.path
                if path == "" or path == "/" or path.startswith("/assets/") or path.startswith("/ui"):
                    resp.headers["Cache-Control"] = "no-store"
            return resp

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/") or full_path == "api":
                raise StarletteHTTPException(status_code=404, detail="接口不存在")
            if full_path == "ui" or full_path.startswith("ui/"):
                rel = full_path[len("ui/"):] if full_path.startswith("ui/") else "index.html"
                web_file = (WEB_DIR / rel).resolve()
                if web_file.is_file() and web_file.is_relative_to(WEB_DIR.resolve()):
                    return FileResponse(web_file)
                return FileResponse(WEB_DIR / "index.html")
            candidate = (_dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(_dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(_dist / "index.html")
    return app


def prewarm_models(deps: "Any") -> None:
    """后台预热 embedding/reranker 模型（daemon 线程，不阻塞启动与 /api/health）。

    模型本身懒加载（首次使用时加载）；预热让首次提问不等待下载/加载，
    同时 health 只读探活（is_loaded）保持秒回。
    """
    import logging

    logger = logging.getLogger("app.main")
    try:
        if deps.embedding is not None:
            getattr(deps.embedding, "ready", lambda: True)()
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding 预热失败（首次提问将现场加载）: %s", exc)
    try:
        if deps.reranker is not None:
            getattr(deps.reranker, "ready", lambda: True)()
    except Exception as exc:  # noqa: BLE001
        logger.warning("reranker 预热失败（首次提问将现场加载）: %s", exc)
