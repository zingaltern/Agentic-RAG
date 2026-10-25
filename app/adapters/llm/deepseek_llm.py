"""DeepSeek LLM（DEVELOPMENT.md 3.8、5.7）：OpenAI 兼容协议。

- base_url=llm.base_url（默认 https://api.deepseek.com）、模型 llm.model（默认 deepseek-chat）。
- 认证：Authorization: Bearer <api_key>；key 只来自环境变量 RAG_DEEPSEEK_API_KEY / RAG_LLM_API_KEY
  （由 config 层映射进 llm.api_key），绝不写进配置/代码。
- 超时 30s（llm.timeout_seconds）；失败抛 LLMUnavailableError → 对外 code=30001。
- deepseek-reasoner 时忽略 reasoning_content，只取 content。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.domain import LLMResponse, ToolCall
from app.core.errors import AgentProtocolError, LLMToolsUnsupportedError, LLMUnavailableError
from app.core.interfaces import LLM, ToolCallingLLM
from app.core.logging import get_logger

logger = get_logger("llm")


class DeepSeekLLM(ToolCallingLLM):
    def __init__(self, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com",
                 temperature: float = 0.1,
                 max_tokens: int = 1024,
                 timeout_seconds: int = 30,
                 api_key: str | None = None,
                 **_: Any):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self._client: Any = None

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,   # 超时/失败由编排层统一处理，不做 SDK 级重试
            )
        return self._client

    def chat(self, messages: list[dict[str, str]],
             temperature: float = 0.1, max_tokens: int = 1024) -> str:
        if not self.api_key:
            raise LLMUnavailableError(
                "未配置 LLM API Key：请设置环境变量 RAG_DEEPSEEK_API_KEY（或 RAG_LLM_API_KEY）"
            )
        try:
            resp = self._ensure_client().chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMUnavailableError:
            raise
        except Exception as exc:
            raise LLMUnavailableError(f"DeepSeek 调用失败（{self.model}）: {exc}") from exc
        content = resp.choices[0].message.content if resp.choices else None
        # deepseek-reasoner：忽略 reasoning_content，只取 content（5.7）
        if not content:
            raise LLMUnavailableError(f"DeepSeek 返回空内容（{self.model}）")
        return content

    # -- P-D：原生工具调用（17.3/9.3） ---------------------------------------

    def chat_with_tools(self, messages: list[dict[str, Any]],
                        tools: list[dict[str, Any]],
                        tool_choice: str = "auto",
                        temperature: float = 0.1,
                        max_tokens: int = 1024) -> LLMResponse:
        """OpenAI 兼容 tools/tool_calls；参数 JSON 非法 → AgentProtocolError。

        说明：Chat Completions 不支持插入非模型生成的 tool call，调用方必须使用
        标准 assistant.tool_calls + tool 消息序列（见 DEVELOPMENT.md 17.5）。
        """
        if not self.api_key:
            raise LLMUnavailableError(
                "未配置 LLM API Key：请设置环境变量 RAG_DEEPSEEK_API_KEY（或 RAG_LLM_API_KEY）"
            )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        try:
            resp = self._ensure_client().chat.completions.create(**kwargs)
        except LLMUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            text = str(exc).lower()
            if status in (400, 404, 422) and any(
                    k in text for k in ("tool", "function", "工具")):
                raise LLMToolsUnsupportedError(
                    f"DeepSeek 不支持工具调用参数（HTTP {status}）: {exc}") from exc
            raise LLMUnavailableError(f"DeepSeek 工具调用失败（{self.model}）: {exc}") from exc
        if not resp.choices:
            raise LLMUnavailableError(f"DeepSeek 返回空 choices（{self.model}）")
        choice = resp.choices[0]
        message = choice.message
        parsed_calls: list[ToolCall] = []
        for idx, call in enumerate(getattr(message, "tool_calls", None) or []):
            raw = getattr(getattr(call, "function", None), "arguments", None) or "{}"
            try:
                args = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AgentProtocolError(
                    f"工具参数不是合法 JSON（call={getattr(call, 'id', idx)}）: {exc}") from exc
            if not isinstance(args, dict):
                raise AgentProtocolError(f"工具参数必须是 JSON 对象（call={getattr(call, 'id', idx)}）")
            parsed_calls.append(ToolCall(
                call_id=str(getattr(call, "id", "") or f"call_{idx}"),
                name=str(getattr(getattr(call, "function", None), "name", "") or ""),
                arguments=args,
            ))
        usage_obj = getattr(resp, "usage", None)
        usage = {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
        }
        return LLMResponse(
            content=getattr(message, "content", None),
            tool_calls=parsed_calls,
            finish_reason=str(getattr(choice, "finish_reason", "stop") or "stop"),
            usage=usage,
            reasoning=getattr(message, "reasoning_content", None),
        )
