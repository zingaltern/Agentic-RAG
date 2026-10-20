"""Agent 预算（DEVELOPMENT.md 17.5/17.8）：时间/步数/工具/LLM 硬约束。"""
from __future__ import annotations

import time
from typing import Any

from app.core.domain import AgentState, Budget
from app.core.errors import AgentBudgetExceeded


def budget_from_config(config: Any, now: float | None = None) -> Budget:
    now = time.time() if now is None else now
    timeout = max(1, int(getattr(config, "request_timeout_seconds", 45)))
    return Budget(
        max_steps=max(1, int(getattr(config, "max_steps", 8))),
        max_llm_calls=max(1, int(getattr(config, "max_llm_calls", 8))),
        max_tool_calls=max(1, int(getattr(config, "max_tool_calls", 12))),
        max_replans=max(0, int(getattr(config, "max_replans", 2))),
        max_context_tokens=max(1000, int(getattr(config, "max_context_tokens", 6000))),
        deadline=now + timeout,
    )


def check_budget(state: AgentState, budget: Budget, check_step: bool = True) -> None:
    """每步/每次调用前检查；超限抛 AgentBudgetExceeded（内部异常）。

    check_step=False 用于同一步内的多次工具调用：步数在进入该步时已检查，
    不能被「state.step == max_steps」重复拦截。
    """
    if budget.deadline and time.time() > budget.deadline:
        raise AgentBudgetExceeded("Agent 超过时间预算", reason="budget_time")
    if check_step and state.step >= budget.max_steps:
        raise AgentBudgetExceeded("Agent 超过步数预算", reason="budget_steps")
    if state.tool_calls >= budget.max_tool_calls:
        raise AgentBudgetExceeded("Agent 超过工具调用预算", reason="budget_steps")
    if state.llm_calls >= budget.max_llm_calls:
        raise AgentBudgetExceeded("Agent 超过 LLM 调用预算", reason="budget_steps")
