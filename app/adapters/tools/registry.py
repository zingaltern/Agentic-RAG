"""工具注册表（DEVELOPMENT.md 17.4）。

- 工具以声明式 args_model（pydantic）注册；registry 生成 OpenAI tools schema；
- 执行管线：allowlist → 参数校验 → 超时 → 异常包装 → 结果截断；
- 作用域（collection/kb_id/filters/user_id）由 ToolContext 注入，工具参数不含这些字段；
- 本模块只做基础设施，不依赖 AgentRuntime，便于单测。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Iterable

from pydantic import ValidationError

from app.core.domain import ToolCall, ToolContext, ToolResult
from app.core.interfaces import Tool
from app.core.logging import get_logger

logger = get_logger("tools")


class ToolRegistry:
    """工具注册/查询/执行；线程安全的只读注册（启动后不再增删）。"""

    def __init__(self, tools: Iterable[Tool] = (),
                 max_workers: int = 4,
                 call_timeout_seconds: float | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)
        # 每个工具独立池：慢工具超时只占自己的 worker，不拖垮快工具。
        # 超时线程仍可能继续运行，因此用独立池 + 工具级超时隔离，而不是共享池。
        self._executors: dict[str, ThreadPoolExecutor] = {
            name: ThreadPoolExecutor(max_workers=1,
                                     thread_name_prefix=f"tool-{name}")
            for name in self._tools
        }
        self.call_timeout_seconds = (
            max(0.05, float(call_timeout_seconds))
            if call_timeout_seconds is not None else None)

    def register(self, tool: Tool) -> None:
        name = (tool.name or "").strip()
        if not name:
            raise ValueError("工具 name 不能为空")
        if name in self._tools:
            raise ValueError(f"工具重复注册: {name}")
        if getattr(tool, "args_model", None) is None:
            raise ValueError(f"工具缺少 args_model: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self, allowed: set[str] | None = None) -> list[dict[str, Any]]:
        """生成 OpenAI 兼容 tools 列表；allowed=None 表示全部。"""
        names = sorted(allowed) if allowed is not None else self.names()
        specs: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            schema = tool.args_model.model_json_schema()
            specs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                },
            })
        return specs

    def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """校验并执行一个工具调用；所有异常转成结构化 ToolResult。"""
        start = time.perf_counter()
        tool = self._tools.get(call.name)
        if tool is None:
            return self._error(call, "TOOL_NOT_FOUND", f"工具不存在: {call.name}", start)
        if ctx.allowed_tools is not None and call.name not in ctx.allowed_tools:
            logger.warning("工具未启用被拒", extra={"extra_fields": {
                "tool": call.name, "trace_id": ctx.trace_id}})
            return self._error(call, "TOOL_SCOPE_DENIED", f"工具未启用: {call.name}", start)
        if call.name.startswith("kb_") and (not ctx.collection or not ctx.kb_id):
            logger.warning("工具缺少知识库作用域被拒", extra={"extra_fields": {
                "tool": call.name, "trace_id": ctx.trace_id}})
            return self._error(call, "TOOL_SCOPE_DENIED",
                               "缺少知识库作用域（collection/kb_id）", start)
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return self._error(call, "TOOL_INVALID_ARGS",
                               f"参数不合法: {exc.errors()[:3]}", start, retryable=False)
        except Exception as exc:  # noqa: BLE001
            return self._error(call, "TOOL_INVALID_ARGS", f"参数解析失败: {exc}", start)
        try:
            timeout = max(0.1, tool.timeout_ms / 1000.0)
            if self.call_timeout_seconds is not None:
                timeout = min(timeout, self.call_timeout_seconds)
            future = self._executors[tool.name].submit(tool.invoke, ctx, args)
            result = future.result(timeout=timeout)
        except FutureTimeoutError:
            return self._error(call, "TOOL_TIMEOUT",
                               f"工具超时（{tool.timeout_ms}ms）: {call.name}", start,
                               retryable=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具执行失败（%s）：%s", call.name, exc)
            return self._error(call, "TOOL_EXECUTION_ERROR",
                               f"工具执行失败: {type(exc).__name__}", start, retryable=True)
        result.call_id = call.call_id
        result.name = tool.name
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        self._truncate(result, tool.max_result_chars)
        return result

    @staticmethod
    def _error(call: ToolCall, code: str, message: str, start: float,
               retryable: bool = False) -> ToolResult:
        return ToolResult(
            call_id=call.call_id, name=call.name, ok=False,
            summary=f"工具失败：{message}", error_code=code,
            error_message=message, latency_ms=int((time.perf_counter() - start) * 1000),
            retryable=retryable,
        )

    @staticmethod
    def _truncate(result: ToolResult, limit: int) -> None:
        if limit <= 0:
            return
        for item in result.evidence:
            if len(item.text) > limit:
                item.text = item.text[:limit]
                result.truncated = True
        if len(result.summary) > limit:
            result.summary = result.summary[:limit]
            result.truncated = True
        try:
            payload = json.dumps(result.data, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = str(result.data)
        if len(payload) > limit:
            result.data = {
                "_truncated": True,
                "preview": payload[: max(0, limit - 32)],
            }
            result.truncated = True


def allowed_tool_names(config, web_allowed: bool = False) -> set[str]:
    """按配置/请求开关生成 allowlist（接受 AgentConfig 或 AgentToolsConfig）。"""
    tools = getattr(config, "tools", config)
    names: set[str] = set()
    if tools.kb_search.enabled:
        names.add("kb_search")
    if tools.kb_fetch.enabled:
        names.add("kb_fetch")
    if tools.kb_list_documents.enabled:
        names.add("kb_list_documents")
    if tools.kb_outline.enabled:
        names.add("kb_outline")
    if tools.ask_user.enabled:
        names.add("ask_user")
    if tools.web_search.enabled and web_allowed:
        names.add("web_search")
    return names
