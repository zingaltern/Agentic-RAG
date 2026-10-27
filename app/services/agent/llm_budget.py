"""LLM 调用硬预算（P2-2）：在真正发起 provider 请求的层计数。

规划重试、反思 judge、合成、引用修复都经过同一个包装器；达到 max_llm_calls
后不再调用 provider，抛 AgentBudgetExceeded 由 Runtime 转成预算终止。

合成是收尾必需步骤（DEVELOPMENT 17.5）：`allow_extra(1)` 仅对当前线程放行
额外调用（不改全局 max_calls，避免并发请求互相影响）；引用修复等可选步骤
仍使用原预算。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.domain import AgentState
from app.core.errors import AgentBudgetExceeded, LLMUnavailableError


class BudgetedLLM:
    def __init__(self, llm: Any, max_calls: int):
        self._llm = llm
        self.max_calls = max(1, int(max_calls))
        self._local = threading.local()
        self.supports_tool_calls = callable(getattr(llm, "chat_with_tools", None))

    def begin(self, state: AgentState) -> None:
        self._local.state = state
        self._local.extra = 0

    def _state(self) -> AgentState | None:
        return getattr(self._local, "state", None)

    @contextmanager
    def allow_extra(self, calls: int = 1) -> Iterator[None]:
        """当前线程临时放行 N 次调用（合成收尾用），退出后立即恢复。"""
        previous = int(getattr(self._local, "extra", 0) or 0)
        self._local.extra = previous + max(0, int(calls))
        try:
            yield
        finally:
            self._local.extra = previous

    def _before_call(self) -> None:
        state = self._state()
        if state is None:
            return
        extra = int(getattr(self._local, "extra", 0) or 0)
        if state.llm_calls >= self.max_calls + extra:
            raise AgentBudgetExceeded("LLM 调用预算耗尽", reason="budget_llm")
        state.llm_calls += 1

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        self._before_call()
        return self._llm.chat(*args, **kwargs)

    def chat_with_tools(self, *args: Any, **kwargs: Any) -> Any:
        if not self.supports_tool_calls:
            raise LLMUnavailableError("当前 LLM 不支持工具调用")
        self._before_call()
        return self._llm.chat_with_tools(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)
