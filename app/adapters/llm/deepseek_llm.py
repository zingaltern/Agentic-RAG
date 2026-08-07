"""DeepSeek LLM（DEVELOPMENT.md 3.8、5.7）：OpenAI 兼容协议。

- base_url=llm.base_url（默认 https://api.deepseek.com）、模型 llm.model（默认 deepseek-chat）。
- 认证：Authorization: Bearer <api_key>；key 只来自环境变量 RAG_DEEPSEEK_API_KEY / RAG_LLM_API_KEY
  （由 config 层映射进 llm.api_key），绝不写进配置/代码。
- 超时 30s（llm.timeout_seconds）；失败抛 LLMUnavailableError → 对外 code=30001。
- deepseek-reasoner 时忽略 reasoning_content，只取 content。
"""
from __future__ import annotations

from typing import Any

from app.core.errors import LLMUnavailableError
from app.core.interfaces import LLM
from app.core.logging import get_logger

logger = get_logger("llm")


class DeepSeekLLM(LLM):
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
