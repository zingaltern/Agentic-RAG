"""确定性反思器（DEVELOPMENT.md 17.5，V1 默认）。

不依赖 LLM：按子目标证据数量/分数/来源多样性/无新证据步数做判定。
LLM judge 默认关闭；需要独立校准后再作为可选增强。
"""
from __future__ import annotations

from typing import Any

from app.core.domain import AgentState, ReflectionResult, ToolResult
from app.core.interfaces import Reflector


class RuleReflector(Reflector):
    def __init__(self, config: Any, **_):
        self.config = config
        self.min_score = float(config.min_evidence_score)
        self.min_per_goal = max(1, int(config.reflection.min_evidence_per_subgoal))

    def _goal_evidence(self, state: AgentState, goal_id: str) -> list:
        return [e for e in state.evidence.values()
                if e.evidence_id in (self._goal_ids(state, goal_id))
                or e.metadata.get("goal_id") == goal_id]

    @staticmethod
    def _goal_ids(state: AgentState, goal_id: str) -> set[str]:
        if not state.plan:
            return set()
        goal = next((g for g in state.plan.subgoals if g.goal_id == goal_id), None)
        return set(goal.evidence_ids) if goal else set()

    def _status(self, state: AgentState, goal_id: str) -> tuple[str, list[str]]:
        ev = self._goal_evidence(state, goal_id)
        strong = [e for e in ev
                  if e.tool_name != "kb_search" or self._relevance(e) >= self.min_score]
        if len(strong) >= self.min_per_goal:
            return "satisfied", []
        if ev:
            return "partial", [f"{goal_id} 证据相关度不足"]
        return "pending", [f"{goal_id} 尚无证据"]

    @staticmethod
    def _relevance(item) -> float:
        """优先用同批归一化分；旧证据回退 relevance / raw score。"""
        for key in ("normalized_score", "relevance"):
            value = item.metadata.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        return float(item.score)

    def _multi_source_ok(self, state: AgentState) -> bool:
        """比较/多跳的软信号：多个来源，或单来源覆盖多个子目标。"""
        docs = {e.document_id for e in state.evidence.values() if e.document_id}
        if len(docs) >= 2:
            return True
        if not state.plan:
            return False
        goal_of = {e.evidence_id: e.metadata.get("goal_id")
                   for e in state.evidence.values()}
        for goal in state.plan.subgoals:
            for eid in goal.evidence_ids:
                goal_of.setdefault(eid, goal.goal_id)
        by_doc: dict[str, set[str]] = {}
        for e in state.evidence.values():
            gid = goal_of.get(e.evidence_id)
            if gid and e.document_id:
                by_doc.setdefault(e.document_id, set()).add(gid)
        return any(len(goals) >= 2 for goals in by_doc.values())

    def sufficient(self, state: AgentState) -> bool:
        if not state.plan or not state.plan.subgoals:
            return bool(state.evidence)
        statuses = [self._status(state, g.goal_id)[0] for g in state.plan.subgoals]
        if not all(s == "satisfied" for s in statuses):
            return False
        if state.plan.complexity in ("multi_hop", "comparison", "aggregation"):
            if not self._multi_source_ok(state):
                return False
        return True

    def missing(self, state: AgentState) -> list[str]:
        if not state.plan:
            return ["缺少计划"]
        missing: list[str] = []
        for goal in state.plan.subgoals:
            status, reasons = self._status(state, goal.goal_id)
            if status != "satisfied":
                missing.append(goal.question)
                missing.extend(reasons)
        return missing[:8]

    def reflect(self, state: AgentState, result: ToolResult | None = None) -> ReflectionResult:
        if not state.plan or not state.plan.subgoals:
            return ReflectionResult(
                step=state.step, decision="abstain", confidence=0.0,
                reason="没有可用计划")
        statuses: dict[str, str] = {}
        missing: list[str] = []
        for goal in state.plan.subgoals:
            status, reasons = self._status(state, goal.goal_id)
            statuses[goal.goal_id] = status
            if status != "satisfied":
                missing.append(goal.question)
                missing.extend(reasons)
        if all(s == "satisfied" for s in statuses.values()):
            if state.plan.complexity in ("multi_hop", "comparison", "aggregation"):
                if not self._multi_source_ok(state):
                    return ReflectionResult(
                        step=state.step, decision="continue", subgoal_status=statuses,
                        missing=["建议补充第二个来源或覆盖多个子目标的单来源证据"],
                        confidence=0.5, reason="证据来源单一，继续补充")
            return ReflectionResult(
                step=state.step, decision="answer", subgoal_status=statuses,
                confidence=0.85, reason="所有子目标均有证据")
        if state.no_new_evidence_steps >= 2:
            return ReflectionResult(
                step=state.step, decision="abstain", subgoal_status=statuses,
                missing=missing[:5], confidence=0.2,
                reason="连续两步没有新增证据")
        # 某子目标尝试两次仍无证据 → 允许一次重规划
        if state.replans < getattr(self.config, "max_replans", 2):
            attempts = {g.goal_id: g.attempts for g in state.plan.subgoals}
            if any(statuses.get(gid) != "satisfied" and n >= 2 for gid, n in attempts.items()):
                return ReflectionResult(
                    step=state.step, decision="replan", subgoal_status=statuses,
                    missing=missing[:5], confidence=0.4,
                    reason="部分子目标多次未满足，尝试重规划")
        return ReflectionResult(
            step=state.step, decision="continue", subgoal_status=statuses,
            missing=missing[:5], confidence=0.5, reason="继续检索")
