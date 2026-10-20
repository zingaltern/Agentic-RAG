"""确定性反思器单测（DEVELOPMENT.md 17.5）。"""
from __future__ import annotations

from app.adapters.agent.rule_reflector import RuleReflector
from app.core.config import load_config
from app.core.domain import AgentState, Evidence, Plan, SubGoal


def _state(complexity="comparison") -> AgentState:
    plan = Plan("p1", "对比", complexity=complexity, subgoals=[
        SubGoal("g1", "年假天数", "找到条款"),
        SubGoal("g2", "病假天数", "找到条款"),
    ])
    state = AgentState("r", "t", "q", "q", "agentic", plan=plan)
    state.subgoal_status = {"g1": "pending", "g2": "pending"}
    return state


def _add(state, cid, doc, text, goal, score=0.9):
    item, _ = state.add_evidence(Evidence(
        chunk_id=cid, document_id=doc, text=text, source=f"{doc}.md",
        score=score, tool_name="kb_search", metadata={"goal_id": goal}))
    goal_obj = next(g for g in state.plan.subgoals if g.goal_id == goal)
    goal_obj.evidence_ids.append(item.evidence_id)
    return item


def test_sufficient_when_all_subgoals_have_distinct_docs():
    state = _state()
    _add(state, "c1", "doc1", "年假 5 天", "g1")
    _add(state, "c2", "doc2", "病假 3 天", "g2")
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.sufficient(state) is True
    result = reflector.reflect(state)
    assert result.decision == "answer"


def test_single_source_covering_two_subgoals_is_soft_signal():
    """单文档同时覆盖两个子目标时不再必然拒答（P2-8 软信号）。"""
    state = _state()
    _add(state, "c1", "doc1", "年假 5 天", "g1")
    _add(state, "c2", "doc1", "病假 3 天", "g2")
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.sufficient(state) is True
    assert reflector.reflect(state).decision == "answer"


def test_single_source_single_goal_not_sufficient_for_comparison():
    state = _state()
    _add(state, "c1", "doc1", "年假 5 天", "g1")
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.sufficient(state) is False
    assert reflector.reflect(state).decision == "continue"


def test_no_new_evidence_abstains():
    state = _state()
    state.no_new_evidence_steps = 2
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.reflect(state).decision == "abstain"


def test_repeated_failure_replans():
    state = _state()
    for goal in state.plan.subgoals:
        goal.attempts = 2
    state.no_new_evidence_steps = 0
    reflector = RuleReflector(load_config().orchestration.agent)
    result = reflector.reflect(state)
    assert result.decision == "replan"


def test_fourth_rank_high_normalized_score_is_satisfied():
    state = _state(complexity="simple")
    item = _add(state, "c1", "doc1", "年假 5 天", "g1", score=0.01)
    item.metadata.update({"relevance": 0.2, "normalized_score": 0.9})
    item2 = _add(state, "c2", "doc2", "病假 3 天", "g2", score=0.01)
    item2.metadata.update({"relevance": 0.2, "normalized_score": 0.88})
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.sufficient(state) is True


def test_low_normalized_score_still_rejects():
    state = _state(complexity="simple")
    item = _add(state, "c1", "doc1", "无关内容", "g1", score=0.01)
    item.metadata.update({"relevance": 0.1, "normalized_score": 0.1})
    reflector = RuleReflector(load_config().orchestration.agent)
    assert reflector.sufficient(state) is False
