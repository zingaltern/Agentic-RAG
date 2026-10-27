"""BudgetedLLM 单测（17.5）：合成收尾可临时 +1，且不改全局预算。"""
from __future__ import annotations

import pytest

from app.core.domain import AgentState
from app.core.errors import AgentBudgetExceeded
from app.services.agent.llm_budget import BudgetedLLM


class _LLM:
    def __init__(self):
        self.calls = 0

    def chat(self, *_args, **_kwargs):
        self.calls += 1
        return "ok"


def _state(calls: int = 0) -> AgentState:
    state = AgentState(run_id="r", trace_id="t", query="q", normalized_query="q",
                       mode="agentic")
    state.llm_calls = calls
    return state


def test_allow_extra_permits_one_call_without_mutating_global_limit():
    llm = _LLM()
    budget = BudgetedLLM(llm, max_calls=2)
    state = _state(calls=2)
    budget.begin(state)

    with pytest.raises(AgentBudgetExceeded):
        budget.chat([])

    with budget.allow_extra(1):
        assert budget.chat([]) == "ok"
        assert state.llm_calls == 3

    # 退出上下文后立即恢复：可选修复仍受原预算约束。
    with pytest.raises(AgentBudgetExceeded):
        budget.chat([])
    assert budget.max_calls == 2
    assert llm.calls == 1


def test_allow_extra_only_affects_current_thread_state():
    budget = BudgetedLLM(_LLM(), max_calls=1)
    state = _state(calls=1)
    budget.begin(state)
    with budget.allow_extra(1):
        assert budget.chat([]) == "ok"
    assert state.llm_calls == 2
