"""Orchestration 模式分发/回退单测（DEVELOPMENT.md 17.2）。"""
from __future__ import annotations

from app.core.config import load_config
from app.core.domain import Answer, Chunk, DetectionResult, EvaluationResult, ScoredChunk
from app.core.errors import AgentInternalError
from app.services.orchestration import OrchestrationService


def _scored(text="年假申请流程说明"):
    return ScoredChunk(
        chunk=Chunk("c1", "doc1", text, 0, None,
                    {"source": "员工手册.md", "kind": "md"}),
        score=0.9, notion="rrf")


class _Retriever:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append({"query": query, "path": path, "collection": collection})
        return [_scored()]


class _Reranker:
    def rerank(self, query, chunks):
        return list(chunks)


class _Eval:
    def evaluate(self, query, chunks):
        return EvaluationResult(True, 0.9, "ok")


class _Rewrite:
    def rewrite(self, query, feedback):
        return query


class _Detect:
    def detect(self, answer, context):
        return DetectionResult(True, [], 1.0)


class _LLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "年假需提前申请[1]。"


class _Agent:
    def __init__(self, raises: bool = False):
        self.calls = 0
        self.raises = raises

    def run(self, **kwargs):
        self.calls += 1
        if self.raises:
            raise AgentInternalError("模拟 Agent 失败")
        return Answer(text="agent 答案[1]", sources=[], mode="agentic",
                      agent_steps=2, termination_reason="answered")


def _build(agent):
    config = load_config()
    config.orchestration.agent.enabled = True
    config.greeting.enabled = False
    config.clarification.enabled = False
    return OrchestrationService(
        config=config, retriever=_Retriever(), reranker=_Reranker(),
        evaluator=_Eval(), rewriter=_Rewrite(), detector=_Detect(),
        llm=_LLM(), vector_store=None, agent_runtime=agent,
    )


def test_agentic_mode_uses_agent_runtime():
    agent = _Agent()
    svc = _build(agent)
    answer = svc.run("年假怎么申请", mode="agentic")
    assert agent.calls == 1
    assert answer.mode == "agentic" and answer.text.startswith("agent")


def test_agentic_failure_falls_back_to_reflect():
    agent = _Agent(raises=True)
    svc = _build(agent)
    answer = svc.run("年假怎么申请", mode="agentic")
    assert agent.calls == 1
    assert answer.code == 0 and answer.text.startswith("年假")


def test_reflect_mode_never_calls_agent():
    agent = _Agent()
    svc = _build(agent)
    answer = svc.run("年假怎么申请", mode="reflect")
    assert agent.calls == 0 and answer.text.startswith("年假")


def test_auto_routes_complex_query_to_agent():
    agent = _Agent()
    svc = _build(agent)
    answer = svc.run("年假和病假分别有几天？", mode="auto")
    assert agent.calls == 1 and answer.mode == "agentic"


def test_auto_routes_simple_query_to_reflect():
    agent = _Agent()
    svc = _build(agent)
    answer = svc.run("年假几天", mode="auto")
    assert agent.calls == 0 and answer.text.startswith("年假")


def test_invalid_mode_rejected():
    svc = _build(_Agent())
    try:
        svc.run("年假怎么申请", mode="deep")
    except Exception as exc:  # ValidationError
        assert "mode" in str(exc)
    else:
        raise AssertionError("非法 mode 应拒绝")


def test_auto_escalates_after_reflect_failure():
    class _FailingEval:
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.1, "below_threshold")

    agent = _Agent()
    config = load_config()
    config.orchestration.agent.enabled = True
    config.greeting.enabled = False
    config.clarification.enabled = False
    svc = OrchestrationService(
        config=config, retriever=_Retriever(), reranker=_Reranker(),
        evaluator=_FailingEval(), rewriter=_Rewrite(), detector=_Detect(),
        llm=_LLM(), vector_store=None, agent_runtime=agent,
    )
    # “A 和 B” 复杂度=2 → reflect_first；reflect 失败后预算充足 → 升级 agentic
    answer = svc.run("年假和病假", mode="auto")
    assert agent.calls == 1
    assert answer.mode == "agentic"


def test_wire_builds_agent_runtime_when_enabled(tmp_path, isolated_stores):
    from app.core.registry import wire
    from tests.unit.fakes import StubEmbedding

    config = load_config()
    config.orchestration.agent.enabled = True
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    deps = wire(
        config, embedding=StubEmbedding(128), llm=_LLM(), reranker=_Reranker(),
        evaluator=_Eval(), rewriter=_Rewrite(), detector=_Detect(),
        kbs=isolated_stores["kbs"], users=isolated_stores["users"],
        documents=isolated_stores["documents"], jobs=isolated_stores["jobs"],
    )
    assert deps.agent_runtime is not None
    assert deps.orchestration.agent_runtime is deps.agent_runtime


def test_routing_disabled_auto_never_escalates():
    agent = _Agent()
    svc = _build(agent)
    svc.config.orchestration.agent.routing.enabled = False
    answer = svc.run("年假和病假分别有几天？", mode="auto")
    assert agent.calls == 0 and answer.mode == "reflect"


def test_max_escalations_zero_blocks_upgrade():
    class _FailingEval:
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.1, "below")

    agent = _Agent()
    config = load_config()
    config.orchestration.agent.enabled = True
    config.greeting.enabled = False
    config.clarification.enabled = False
    config.orchestration.agent.routing.max_escalations = 0
    svc = OrchestrationService(
        config=config, retriever=_Retriever(), reranker=_Reranker(),
        evaluator=_FailingEval(), rewriter=_Rewrite(), detector=_Detect(),
        llm=_LLM(), vector_store=None, agent_runtime=agent)
    answer = svc.run("年假和病假", mode="auto")
    assert agent.calls == 0 and answer.code == 21001
    assert answer.termination_reason == "abstain_insufficient"


def test_max_escalations_one_retry_then_falls_back():
    class _FailingEval:
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.1, "below")

    agent = _Agent(raises=True)
    config = load_config()
    config.orchestration.agent.enabled = True
    config.greeting.enabled = False
    config.clarification.enabled = False
    config.orchestration.agent.routing.max_escalations = 1
    svc = OrchestrationService(
        config=config, retriever=_Retriever(), reranker=_Reranker(),
        evaluator=_FailingEval(), rewriter=_Rewrite(), detector=_Detect(),
        llm=_LLM(), vector_store=None, agent_runtime=agent)
    answer = svc.run("年假和病假", mode="auto")
    assert agent.calls == 1
    assert answer.code in (0, 21001)
    assert answer.termination_reason == "fallback_reflect"


def test_agent_failure_marks_fallback_reflect():
    svc = _build(_Agent(raises=True))
    answer = svc.run("年假怎么申请", mode="agentic")
    assert answer.code == 0 and answer.termination_reason == "fallback_reflect"
    assert answer.text.startswith("年假")


def test_normal_reflect_termination_is_answered():
    svc = _build(_Agent())
    answer = svc.run("年假怎么申请", mode="reflect")
    assert answer.termination_reason == "answered"


def test_agent_plan_error_falls_back_to_reflect_with_marker():
    from app.core.errors import AgentPlanError

    class _PlanFailAgent:
        def __init__(self):
            self.calls = 0

        def run(self, **kwargs):
            self.calls += 1
            raise AgentPlanError("非法计划")

    agent = _PlanFailAgent()
    svc = _build(agent)
    answer = svc.run("年假怎么申请", mode="agentic")
    assert agent.calls == 1
    assert answer.code == 0 and answer.termination_reason == "fallback_reflect"


def test_reflect_clarify_does_not_escalate():
    agent = _Agent()
    svc = _build(agent)
    from app.core.domain import Answer
    svc._run_reflect = lambda *a, **k: Answer(text="请补充范围", code=21003, need_clarify=True)
    answer = svc.run("年假和病假", mode="auto")
    assert agent.calls == 0 and answer.code == 21003


def test_unexpected_agent_exception_falls_back_reflect():
    class _RuntimeErrorAgent:
        def __init__(self):
            self.calls = 0

        def run(self, **kwargs):
            self.calls += 1
            raise RuntimeError("boom")

    agent = _RuntimeErrorAgent()
    svc = _build(agent)
    answer = svc.run("年假怎么申请", mode="agentic")
    assert agent.calls == 1
    assert answer.code == 0 and answer.text.startswith("年假")
    assert answer.termination_reason == "fallback_reflect"


def test_auto_agent_receives_remaining_deadline():
    import time as _time

    class _DeadlineAgent(_Agent):
        def __init__(self):
            super().__init__()
            self.deadline = None

        def run(self, **kwargs):
            self.deadline = kwargs.get("deadline")
            return super().run(**kwargs)

    class _FailingEval:
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.1, "below")

    agent = _DeadlineAgent()
    config = load_config()
    config.orchestration.agent.enabled = True
    config.greeting.enabled = False
    config.clarification.enabled = False
    config.orchestration.agent.request_timeout_seconds = 2
    svc = OrchestrationService(
        config=config, retriever=_Retriever(), reranker=_Reranker(),
        evaluator=_FailingEval(), rewriter=_Rewrite(), detector=_Detect(),
        llm=_LLM(), vector_store=None, agent_runtime=agent)
    start = _time.time()
    svc.run("年假和病假", mode="auto")
    assert agent.deadline is not None
    assert agent.deadline <= start + 2.2
