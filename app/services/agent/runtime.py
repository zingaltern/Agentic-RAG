"""AgentRuntime（DEVELOPMENT.md 17.5）：Plan → Act → Observe → Reflect → Synthesize。

设计约束：
- 只依赖 core 契约与注入组件；工具在构造时装配；
- 预算/循环/回退由本层负责；规划/反思/合成/校验由 adapters/agent 实现；
- 任何不可恢复错误抛 AgentInternalError，由 OrchestrationService 回退 reflect。
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from app.adapters.agent.llm_planner import LLMPlanner
from app.adapters.agent.llm_reflector import LLMReflector
from app.adapters.agent.llm_synthesizer import LLMSynthesizer, NO_EVIDENCE_TEXT
from app.adapters.agent.llm_verifier import LLMVerifier
from app.adapters.agent.rule_reflector import RuleReflector
from app.adapters.injection_scanner import InjectionScanner
from app.adapters.rewriter.rule_rewriter import normalize_query
from app.adapters.tools import (
    AskUserTool, KbFetchTool, KbListDocumentsTool, KbOutlineTool, KbSearchTool,
    ToolRegistry, WebSearchTool, allowed_tool_names,
)
from app.core.config import SystemConfig
from app.core.domain import (
    AgentMode, AgentState, Answer, Evidence, Plan, SubGoal, TerminationReason,
    ToolContext, TraceSpan,
)
from app.core.errors import (
    AgentBudgetExceeded, AgentInternalError, AgentPlanError, AgentProtocolError,
    AgentToolError, LLMUnavailableError, ValidationError,
)
from app.core.interfaces import (
    HallucinationDetector, LLM, Reranker, Retriever, VectorStore,
)
from app.core.logging import get_logger, get_trace_id
from app.services.agent.budget import budget_from_config, check_budget
from app.services.agent.llm_budget import BudgetedLLM

logger = get_logger("agent.runtime")

FALLBACK_TEXT = "抱歉，暂未找到相关答案…"
CITE_RE = re.compile(r"\[(\d+)\]")


def _strip_cites(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text or "")


def _call_key(name: str, args: dict[str, Any]) -> str:
    return name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)


def _overlap_score(question: str, text: str) -> int:
    """字符二元组重合度（确定性、零依赖），用于把证据归到最相关子目标。"""
    q = {question[i:i + 2] for i in range(max(0, len(question) - 1)) if question[i:i + 2].strip()}
    t = {text[i:i + 2] for i in range(max(0, len(text) - 1)) if text[i:i + 2].strip()}
    if not q or not t:
        return 0
    return len(q & t)


class AgentRuntime:
    def __init__(self, config: SystemConfig, llm: LLM | None,
                 retriever: Retriever | None, reranker: Reranker | None,
                 vector_store: VectorStore | None, documents: Any = None,
                 detector: HallucinationDetector | None = None,
                 web_retriever: Any = None, normalizer: Any = None,
                 kbs: Any = None, **_: Any):
        self.config = config
        self.normalizer = normalizer
        self._injection_scanner = InjectionScanner(
            patterns=config.injection.patterns, whitelist=config.injection.whitelist)
        self.llm = llm
        self.retriever = retriever
        self.reranker = reranker
        self.vector_store = vector_store
        self.documents = documents
        self.detector = detector
        self.kbs = kbs
        self.agent_cfg = config.orchestration.agent
        self._llm_budget = BudgetedLLM(llm, self.agent_cfg.max_llm_calls) if llm is not None else None
        self._sem = threading.BoundedSemaphore(max(1, self.agent_cfg.max_concurrent_queries))

        tools = [
            KbSearchTool(retriever, reranker,
                         default_k=self.agent_cfg.tools.kb_search.default_k,
                         max_k=self.agent_cfg.tools.kb_search.max_k)
            if retriever is not None else None,
            KbFetchTool(vector_store, max_chars=self.agent_cfg.tools.kb_fetch.max_chars),
            KbListDocumentsTool(documents, max_items=self.agent_cfg.tools.kb_list_documents.max_items),
            KbOutlineTool(vector_store, max_sections=self.agent_cfg.tools.kb_outline.max_sections),
            WebSearchTool(web_retriever, max_results=self.agent_cfg.tools.web_search.max_results,
                          allowed_domains=self.agent_cfg.tools.web_search.allowed_domains),
            AskUserTool(),
        ]
        self.tools = ToolRegistry(
            [t for t in tools if t is not None],
            call_timeout_seconds=config.llm.tool_call_timeout_seconds)
        self.planner = LLMPlanner(
            self._llm_budget, self.tools, self.agent_cfg,
            tool_calling=config.llm.tool_calling,
            max_tool_calls_per_turn=config.llm.max_tool_calls_per_turn)
        self.reflector = (LLMReflector(self._llm_budget, self.agent_cfg)
                          if self.agent_cfg.reflection.llm_judge
                          else RuleReflector(self.agent_cfg))
        self.synthesizer = LLMSynthesizer(self._llm_budget, self.agent_cfg)
        self.verifier = LLMVerifier(detector, self._llm_budget, self.agent_cfg)

    # -- 对外入口 ------------------------------------------------------------

    def run(self, query: str, filters: dict | None = None,
            collection: str | None = None, top_k: int | None = None,
            debug: bool = False, history: list[dict] | None = None,
            web_search: bool = False, allow_clarify: bool = True,
            capture_contexts: bool = False, kb_id: str | None = None,
            deadline: float | None = None) -> Answer:
        if self.llm is None:
            raise AgentInternalError("LLM 未装配，无法进入 Agentic 模式")
        if not self._sem.acquire(timeout=max(0.0, self.agent_cfg.acquire_timeout_seconds)):
            raise AgentInternalError("Agentic 并发已满，回退 reflect")
        # 冷启动预热（幂等）：失败也必须释放信号量
        try:
            for holder in (getattr(self.reranker, "ready", None),
                           getattr(getattr(self.retriever, "embedding", None), "ready", None)):
                if callable(holder):
                    holder()
        except Exception as exc:  # noqa: BLE001
            self._sem.release()
            raise AgentInternalError(f"模型预热失败: {exc}", reason="tool_error") from exc
        state: AgentState | None = None
        try:
            normalized = normalize_query(query)
            if self.normalizer is not None:
                normalized = self.normalizer.normalize(normalized) or normalized
            resolved_kb, resolved_collection = self._resolve_scope(kb_id, collection)
            state = AgentState(
                run_id=f"run_{int(time.time() * 1000)}_{id(self) % 10000}",
                trace_id=get_trace_id(),
                query=query,
                normalized_query=normalized,
                mode=AgentMode.AGENTIC,
                kb_id=resolved_kb,
                collection=resolved_collection,
                filters=filters or {},
                history_summary=self._history_summary(history),
                web_search=bool(web_search and self.config.web.enabled),
            )
            if self._llm_budget is not None:
                self._llm_budget.max_calls = max(1, int(self.agent_cfg.max_llm_calls))
                self._llm_budget.begin(state)
            state.allowed_tools = allowed_tool_names(
                self.agent_cfg.tools, web_allowed=state.web_search)
            if not self.config.clarification.enabled:
                state.allowed_tools.discard("ask_user")
            if not state.allowed_tools:
                raise AgentInternalError("没有可用工具，回退 reflect")
            budget = budget_from_config(self.agent_cfg)
            state.started_at = time.time()
            if deadline is not None:
                budget.deadline = min(budget.deadline, deadline)
            state.deadline = budget.deadline
            return self._run_loop(state, budget, debug=debug,
                                  allow_clarify=allow_clarify,
                                  capture_contexts=capture_contexts)
        except AgentBudgetExceeded as exc:
            logger.warning("Agent 预算耗尽：%s", exc)
            if state is not None:
                return self._budget_answer(state, exc)
            raise AgentInternalError(str(exc), reason=getattr(exc, "reason", "budget_steps"))
        except LLMUnavailableError as exc:
            raise AgentInternalError(f"LLM 不可用：{exc}", reason="tool_error") from exc
        except (AgentPlanError, AgentProtocolError, AgentToolError):
            raise
        except AgentInternalError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent 未预期异常：%s", exc)
            raise AgentInternalError(f"Agent 执行异常：{type(exc).__name__}") from exc
        finally:
            self._sem.release()

    def _resolve_scope(self, kb_id: str | None, collection: str | None) -> tuple[str, str]:
        """统一 kb_id/collection；两者必须同源，错配 fail-closed。"""
        default_kb = getattr(self.kbs, "default_kb", "default") if self.kbs else "default"
        default_col = self.config.vector_store.collection
        if self.kbs is None:
            if kb_id and collection:
                allowed = {"kb", f"rag_{kb_id}", kb_id}
                if collection not in allowed:
                    raise ValidationError(
                        f"kb_id/collection 不一致: {kb_id!r}/{collection!r}")
            return kb_id or default_kb, collection or default_col
        if kb_id and collection:
            info = self.kbs.get(kb_id)
            if info is None or info.collection != collection:
                raise ValidationError(
                    f"kb_id/collection 不一致: {kb_id!r}/{collection!r}")
            return info.kb_id, info.collection
        if kb_id:
            info = self.kbs.get(kb_id)
            if info is None:
                raise ValidationError(f"知识库不存在: {kb_id!r}")
            return info.kb_id, info.collection
        if collection:
            info = next((k for k in self.kbs.list() if k.collection == collection), None)
            if info is None:
                if collection == default_col:
                    return default_kb, collection
                raise ValidationError(f"collection 未映射到知识库: {collection!r}")
            return info.kb_id, info.collection
        info = self.kbs.get(default_kb)
        return default_kb, info.collection if info else default_col

    @staticmethod
    def _default_plan(state: AgentState) -> Plan:
        return Plan(
            plan_id=f"plan_default_{state.step}_{int(time.time() * 1000) % 100000}",
            goal=f"回答：{state.normalized_query[:120]}",
            complexity="simple",
            subgoals=[SubGoal(
                goal_id="g1", question=state.normalized_query,
                success_criteria=f"找到与「{state.normalized_query}」直接相关的证据")],
            created_by="default",
            rationale="planning.enabled=false，使用确定性单子目标计划",
        )

    # -- 主循环 --------------------------------------------------------------

    def _run_loop(self, state: AgentState, budget, debug: bool,
                  allow_clarify: bool, capture_contexts: bool) -> Answer:
        assert state.plan is None
        check_budget(state, budget)
        plan_start = time.perf_counter()
        if self.agent_cfg.planning.enabled:
            state.plan = self.planner.plan(state, budget)
        else:
            # 确定性单子目标计划：不发起规划 LLM 调用（17.6）
            state.plan = self._default_plan(state)
        if state.plan is None:
            raise AgentPlanError("规划器返回空计划")
        state.subgoal_status = {g.goal_id: "pending" for g in state.plan.subgoals}
        self._span(state, "plan", name="plan",
                   output=state.plan.goal,
                   attrs={"subgoals": len(state.plan.subgoals),
                          "version": state.plan.version},
                   duration_ms=int((time.perf_counter() - plan_start) * 1000))

        seen_calls: set[str] = set()
        last_result = None
        while True:
            check_budget(state, budget)
            state.step += 1
            action_start = time.perf_counter()
            action = self.planner.next_action(state, budget)
            self._span(state, "action", name=action.kind, step=state.step,
                       output=(action.content or "")[:80],
                       attrs={"tool_calls": [c.name for c in action.tool_calls]},
                       duration_ms=int((time.perf_counter() - action_start) * 1000))

            if action.kind == "final":
                if self.reflector.sufficient(state):
                    return self._synthesize(state, debug, capture_contexts,
                                            TerminationReason.ANSWERED)
                if state.no_new_evidence_steps >= 1:
                    return self._abstain(state, debug, reason="证据不足")
                state.no_new_evidence_steps += 1
                state.observations.append({
                    "step": state.step, "hint": "模型想结束但证据不足，请继续检索",
                    "missing": self.reflector.missing(state)})
                continue

            if action.kind == "clarify":
                return self._clarify(state, action.content or "请补充问题的具体范围。",
                                     debug, allow_clarify)

            if not action.tool_calls:
                raise AgentProtocolError("模型未给出工具调用或最终答案")

            step_had_ok = False
            step_had_skipped = False
            step_new_evidence = 0
            step_fetch_progress = False
            for call in action.tool_calls:
                check_budget(state, budget, check_step=False)
                key = _call_key(call.name, call.arguments)
                if key in seen_calls:
                    step_had_skipped = True
                    state.duplicate_steps += 1
                    state.observations.append({
                        "step": state.step, "tool": call.name,
                        "ok": False, "summary": "重复工具调用，已跳过",
                        "skipped_duplicate": True, "evidence_ids": [],
                    })
                    self._span(state, "tool_skipped", name=call.name, step=state.step,
                               output="duplicate skipped",
                               attrs={"duplicate_steps": state.duplicate_steps})
                    if state.duplicate_steps >= 2:
                        raise AgentProtocolError("检测到重复工具调用，终止 Agent")
                    continue
                seen_calls.add(key)
                state.duplicate_steps = 0
                ctx = ToolContext(
                    trace_id=state.trace_id, user_id=state.user_id,
                    kb_id=state.kb_id, collection=state.collection,
                    filters=state.filters, deadline=state.deadline,
                    allowed_tools=state.allowed_tools, request_id=state.run_id,
                    web_search=state.web_search,
                )
                result = self.tools.invoke(call, ctx)
                state.tool_calls += 1
                step_had_ok = step_had_ok or result.ok
                if result.ok and result.data.get("clarify"):
                    return self._clarify(
                        state, str(result.data.get("question") or "请补充问题的具体范围。"),
                        debug, allow_clarify)
                last_result = result
                new_count = self._ingest(state, result)
                step_new_evidence += new_count
                if result.ok and result.name == "kb_fetch" and result.evidence:
                    # fetch 通常命中已有 chunk，但会补全父块正文：视为“有进展”
                    step_fetch_progress = True
                state.observations.append({
                    "step": state.step, "tool": result.name, "ok": result.ok,
                    "summary": result.summary,
                    "new_evidence": new_count,
                    "evidence_ids": [e.evidence_id for e in result.evidence],
                    "error_code": result.error_code,
                })
                self._span(state, "tool", name=result.name, step=state.step,
                           input=json.dumps(call.arguments, ensure_ascii=False)[:120],
                           output=result.summary[:120],
                           attrs={"ok": result.ok, "new_evidence": new_count,
                                  "evidence_ids": [e.evidence_id for e in result.evidence],
                                  "error_code": result.error_code},
                           duration_ms=result.latency_ms)

            if step_new_evidence or step_fetch_progress:
                state.no_new_evidence_steps = 0
            else:
                state.no_new_evidence_steps += 1
                self._mark_failed_attempt(state)
            if not step_had_ok and not step_had_skipped and not state.evidence:
                raise AgentToolError("本轮工具全部失败，回退 reflect")
            reflect_start = time.perf_counter()
            reflection = self.reflector.reflect(state, last_result)
            state.reflections.append(reflection)
            self._apply_reflection(state, reflection)
            self._span(state, "reflect", name=reflection.decision, step=state.step,
                       output=reflection.reason,
                       attrs={"missing": reflection.missing,
                              "status": reflection.subgoal_status},
                       duration_ms=int((time.perf_counter() - reflect_start) * 1000))
            logger.info("agent_reflect", extra={"extra_fields": {
                "step": state.step, "decision": reflection.decision,
                "missing": reflection.missing, "trace_id": state.trace_id}})

            if reflection.decision == "answer":
                return self._synthesize(state, debug, capture_contexts,
                                        TerminationReason.ANSWERED)
            if reflection.decision == "clarify":
                return self._clarify(state, "请补充问题的具体范围。", debug, allow_clarify)
            if reflection.decision == "abstain":
                return self._abstain(state, debug, reason=reflection.reason)
            if reflection.decision == "replan":
                if not self.agent_cfg.planning.enabled:
                    return self._abstain(state, debug, reason="规划已禁用，无法重规划")
                if state.replans >= budget.max_replans:
                    return self._abstain(state, debug, reason="重规划次数达到上限")
                check_budget(state, budget)
                new_plan = self.planner.replan(state, reflection)
                if new_plan is None:
                    return self._abstain(state, debug, reason="重规划失败")
                state.plan = new_plan
                state.replans += 1
                state.subgoal_status = {g.goal_id: "pending" for g in new_plan.subgoals}
                self._span(state, "plan", name="replan", step=state.step,
                           output=new_plan.goal,
                           attrs={"version": new_plan.version,
                                  "replans": state.replans})
            # continue

    # -- 证据/状态 -----------------------------------------------------------

    def _ingest(self, state: AgentState, result) -> int:
        new_count = 0
        for item in result.evidence:
            item.step = state.step
            item.tool_name = item.tool_name or result.name
            try:
                hits = self._injection_scanner.scan(item.text)
            except Exception:  # noqa: BLE001
                hits = []
            if hits:
                item.metadata["injection_suspected"] = True
                item.metadata["injection_hits"] = len(hits)
                logger.warning("证据疑似提示注入（仅标记，不执行）",
                               extra={"extra_fields": {
                                   "source": item.source, "hits": len(hits),
                                   "trace_id": state.trace_id}})
            if (len(state.evidence) >= self.agent_cfg.max_evidence_items
                    and not any(e.chunk_id == item.chunk_id for e in state.evidence.values())):
                continue
            stored, is_new = state.add_evidence(item)
            self._assign_goal(state, stored)
            if is_new:
                new_count += 1
        return new_count

    @staticmethod
    def _assign_goal(state: AgentState, item: Evidence) -> None:
        if not state.plan or not state.plan.subgoals:
            return
        best = None
        best_score = 0
        for goal in state.plan.subgoals:
            score = _overlap_score(goal.question + goal.success_criteria, item.text[:1200])
            if score > best_score:
                best, best_score = goal, score
        if best is None:
            best = next((g for g in state.plan.subgoals if not g.evidence_ids),
                        state.plan.subgoals[0])
        if item.evidence_id not in best.evidence_ids:
            best.evidence_ids.append(item.evidence_id)
        item.metadata["goal_id"] = best.goal_id
        if best.status in ("pending", "failed"):
            best.status = "running"

    @staticmethod
    def _mark_failed_attempt(state: AgentState) -> None:
        if not state.plan:
            return
        for goal in state.plan.subgoals:
            if goal.status != "satisfied":
                goal.attempts += 1

    @staticmethod
    def _apply_reflection(state: AgentState, reflection) -> None:
        state.subgoal_status = dict(reflection.subgoal_status)
        if not state.plan:
            return
        for goal in state.plan.subgoals:
            status = reflection.subgoal_status.get(goal.goal_id, goal.status)
            if status == "satisfied":
                goal.status = "satisfied"
            elif status == "failed":
                goal.status = "failed"
            elif goal.status != "satisfied":
                goal.status = "running"

    # -- 收尾 ----------------------------------------------------------------

    def _synthesize(self, state: AgentState, debug: bool, capture_contexts: bool,
                    termination: TerminationReason) -> Answer:
        if not state.evidence:
            return self._abstain(state, debug, reason="没有证据")
        # 合成是收尾必需：允许在步数/LLM 上限上各 +1（时间预算仍硬检查）
        synth_budget = budget_from_config(self.agent_cfg, state.started_at)
        synth_budget.max_steps = state.step + 1
        synth_budget.max_tool_calls = state.tool_calls + 1
        check_budget(state, synth_budget)
        synth_start = time.perf_counter()
        answer_text, cited = self.synthesizer.synthesize(state)
        self._span(state, "synthesize", name="synthesize", step=state.step,
                   output=answer_text[:120],
                   attrs={"evidence_ids": [e.evidence_id for e in cited]},
                   duration_ms=int((time.perf_counter() - synth_start) * 1000))
        if answer_text == NO_EVIDENCE_TEXT or not cited:
            return self._abstain(state, debug, reason="合成阶段无证据")
        verify_start = time.perf_counter()
        detection = self.verifier.verify(answer_text, cited)
        attempts = (max(0, int(self.agent_cfg.synthesis.max_repair_attempts))
                    if self.agent_cfg.synthesis.citation_repair else 0)
        for _ in range(attempts):
            if detection.verified:
                break
            repaired = self.verifier.repair(answer_text, state, detection.suspected, cited)
            if not repaired or repaired == answer_text:
                break
            answer_text = repaired
            detection = self.verifier.verify(answer_text, cited)
            if not detection.verified and not self.agent_cfg.synthesis.strict_verify:
                # 非 strict：删除越界引用，保证不返回非法 [N]
                answer_text = re.sub(
                    r"\[(\d+)\]",
                    lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(cited) else "",
                    answer_text)
                detection = self.verifier.verify(answer_text, cited)
        if self.agent_cfg.synthesis.strict_verify and not detection.verified:
            return self._abstain(state, debug, reason="答案校验未通过")
        self._span(state, "verify", name="verify", step=state.step,
                   output=f"verified={detection.verified}",
                   attrs={"verified_rate": detection.verified_rate,
                          "citation_repair": self.agent_cfg.synthesis.citation_repair},
                   duration_ms=int((time.perf_counter() - verify_start) * 1000))
        return self._answer(state, answer_text, cited, detection, termination,
                            debug, capture_contexts)

    def _budget_answer(self, state: AgentState, exc: AgentBudgetExceeded) -> Answer:
        # V1 不区分 budget_llm 与 budget_steps：两者都映射 budget_steps/21001；
        # 若未来新增独立错误码，先更新 DEVELOPMENT 17.5/13.4。
        reason = getattr(exc, "reason", "budget_steps")
        termination = (TerminationReason.BUDGET_TIME if reason == "budget_time"
                       else TerminationReason.BUDGET_STEPS)
        if state.evidence and state.llm_calls < max(1, self.agent_cfg.max_llm_calls):
            try:
                return self._synthesize(state, debug=False, capture_contexts=False,
                                        termination=termination)
            except Exception:  # noqa: BLE001
                pass
        return self._abstain(state, debug=False, reason=str(exc),
                             termination=TerminationReason(reason)
                             if reason in TerminationReason._value2member_map_ else TerminationReason.BUDGET_STEPS)

    def _abstain(self, state: AgentState, debug: bool, reason: str = "",
                 termination: TerminationReason = TerminationReason.ABSTAIN_INSUFFICIENT) -> Answer:
        trace = self._debug_trace(state) if debug else []
        is_timeout = termination == TerminationReason.BUDGET_TIME
        return Answer(
            text=FALLBACK_TEXT, sources=[], iterations=state.step,
            debug_trace=trace, code=21002 if is_timeout else 21001,
            message=("请求超时" if is_timeout else (reason or "未找到可靠答案")),
            elapsed_ms=int((time.time() - state.started_at) * 1000),
            mode="agentic", agent_steps=state.step,
            plan=state.plan.to_dict() if state.plan else None,
            steps=self._steps(state), termination_reason=termination.value,
            usage=self._usage(state), agent_trace=self._agent_trace(state) if debug else [],
        )

    def _clarify(self, state: AgentState, question: str, debug: bool,
                 allow_clarify: bool) -> Answer:
        if not allow_clarify:
            return self._abstain(state, debug, reason="澄清次数已达上限")
        return Answer(
            text=question[:120], sources=[], iterations=state.step,
            debug_trace=self._debug_trace(state) if debug else [],
            code=21003, message="需要澄清：请明确您的意图", need_clarify=True,
            elapsed_ms=int((time.time() - state.started_at) * 1000),
            mode="agentic", agent_steps=state.step,
            plan=state.plan.to_dict() if state.plan else None,
            steps=self._steps(state), termination_reason=TerminationReason.CLARIFY.value,
            usage=self._usage(state), agent_trace=self._agent_trace(state) if debug else [],
        )

    def _answer(self, state: AgentState, text: str, cited: list[Evidence],
                detection, termination: TerminationReason, debug: bool,
                capture_contexts: bool) -> Answer:
        sources = [{
            "document_id": e.document_id,
            "filename": e.source,
            "url": e.metadata.get("url") if e.is_web else None,
            "source_type": "web" if e.is_web else None,
            "page": e.metadata.get("url") if e.is_web else e.page,
            "chunk": e.text[:300],
            "score": round(e.score, 4),
        } for e in cited]
        contexts = [{
            "chunk_id": e.chunk_id, "document_id": e.document_id,
            "filename": e.source, "page": e.page, "text": e.text,
            "score": round(e.score, 4),
        } for e in state.evidence.values()] if capture_contexts else []
        return Answer(
            text=text, sources=sources, iterations=state.step,
            hallucination=detection,
            debug_trace=self._debug_trace(state) if debug else [],
            code=0, message="ok",
            elapsed_ms=int((time.time() - state.started_at) * 1000),
            contexts=contexts, mode="agentic", agent_steps=state.step,
            plan=state.plan.to_dict() if state.plan else None,
            steps=self._steps(state), termination_reason=termination.value,
            usage=self._usage(state), agent_trace=self._agent_trace(state) if debug else [],
        )

    # -- 轨迹/工具 -----------------------------------------------------------

    def _history_summary(self, history: list[dict] | None) -> str | None:
        if not history:
            return None
        lines = []
        for turn in history[-6:]:
            role = "user" if turn.get("role") == "user" else "assistant"
            content = _strip_cites(str(turn.get("content") or turn.get("text") or ""))[:200]
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines) or None

    @staticmethod
    def _span(state: AgentState, type_: str, name: str = "", step: int = 0,
              input: str = "", output: str = "", attrs: dict | None = None,
              duration_ms: int = 0) -> None:
        state.trace.append(TraceSpan(
            span_id=f"s{len(state.trace) + 1}", type=type_, step=step, name=name,
            duration_ms=duration_ms, input_summary=input[:200],
            output_summary=output[:200], attributes=attrs or {}))

    def _steps(self, state: AgentState) -> list[dict[str, Any]]:
        return [{"step": o.get("step"), "action": o.get("tool") or o.get("hint", "")[:60],
                 "summary": o.get("summary") or o.get("hint", ""),
                 "evidence_ids": o.get("evidence_ids", [])}
                for o in state.observations]

    def _debug_trace(self, state: AgentState) -> list[dict[str, Any]]:
        trace: list[dict[str, Any]] = []
        if state.plan:
            trace.append({"iter": 0, "action": "plan", "summary": state.plan.goal,
                          "subgoals": [g.goal_id for g in state.plan.subgoals]})
        for obs in state.observations:
            trace.append({"iter": obs.get("step"), "action": obs.get("tool", "reflect"),
                          "summary": obs.get("summary", ""),
                          "evidence_ids": obs.get("evidence_ids", []),
                          "ok": obs.get("ok", True)})
        for ref in state.reflections:
            trace.append({"iter": ref.step, "action": "reflect",
                          "summary": ref.reason, "decision": ref.decision,
                          "missing": ref.missing})
        return trace

    def _agent_trace(self, state: AgentState) -> list[dict[str, Any]]:
        return [s.to_dict() for s in state.trace]

    def _usage(self, state: AgentState) -> dict[str, Any]:
        return {
            "llm_calls": state.llm_calls, "tool_calls": state.tool_calls,
            "prompt_tokens": state.prompt_tokens, "completion_tokens": state.completion_tokens,
            "steps": state.step, "replans": state.replans,
        }
