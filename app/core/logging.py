"""JSON 结构化日志 + trace_id 贯穿（DEVELOPMENT.md 2.3、10.2、附录 A-7）。"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="-")

_LOGGER_HANDLERS_INIT = False


def set_trace_id(trace_id: str) -> None:
    """在中间件/入口设置本次请求的 trace_id（ContextVar，全链路可读）。"""
    TRACE_ID.set(trace_id)


def get_trace_id() -> str:
    return TRACE_ID.get()


class TraceIdFilter(logging.Filter):
    """把 ContextVar 中的 trace_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = TRACE_ID.get()
        return True


class JsonFormatter(logging.Formatter):
    """JSON 结构化输出；额外字段通过 extra={"extra_fields": {...}} 注入。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "info", json: bool = True) -> None:
    """幂等配置 app.* 命名空间的根 logger（带 trace_id 过滤器）。"""
    root = logging.getLogger("app")
    root.setLevel(level.upper())
    root.handlers.clear()
    root.propagate = False
    handler = logging.StreamHandler()
    if json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s] %(message)s"
        ))
    handler.addFilter(TraceIdFilter())
    root.addHandler(handler)


def get_logger(name: str = "") -> logging.Logger:
    """取带 trace_id 能力的 logger（无需每个 logger 单独挂过滤器，handler 层统一注入）。"""
    return logging.getLogger(f"app.{name}" if name else "app")
