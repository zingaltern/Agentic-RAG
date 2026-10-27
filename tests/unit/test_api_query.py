"""API /api/query 单测（DEVELOPMENT.md 6.4）：桩嵌入 + 桩 LLM，真实编排链路。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.core.domain import DetectionResult
from tests.unit.fakes import StubEmbedding

ANSWER = "根据员工手册，年假需提前 3 个工作日通过 OA 系统申请[1]。"


class StubLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return ANSWER


class StubDetector:
    def detect(self, answer, context):
        return DetectionResult(verified=True, suspected=[], verified_rate=1.0)


@pytest.fixture()
def q_client(tmp_path, isolated_stores):
    """组件全装配（真实 Chroma 临时目录）+ 桩 embedding/llm/detector/reranker。

    评估/改写/检测用确定性旧实现（rule/rule/citation），聚焦路由与编排契约；
    semantic 系 provider 的行为由 test_semantic_* / test_orchestration_v2 覆盖。
    """
    from app.adapters.detector.citation_tracing import CitationTracingDetector
    from app.adapters.evaluator.rule_evaluator import RuleEvaluator
    from app.adapters.reranker.identity import IdentityReranker

    config = load_config()
    config.api.auth_enabled = False   # 与仓库 settings.yaml 解耦：这些用例按个人模式契约编写
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(
        config=config, components=True, prewarm=False,
        embedding=StubEmbedding(dimension=512),
        llm=StubLLM(),
        detector=StubDetector(),
        reranker=IdentityReranker(rerank_top_k=5),
        evaluator=RuleEvaluator(rule_threshold=0.45, top_n=5),
        kbs=isolated_stores["kbs"], users=isolated_stores["users"],
        documents=isolated_stores["documents"], jobs=isolated_stores["jobs"],
    )
    # 入库一份文档供检索
    sample = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"
    deps = app.state.deps
    deps.pipeline.ingest(sample.read_bytes(), "员工手册.md")
    with TestClient(app) as c:
        yield c


def test_query_full_response_shape(q_client):
    resp = q_client.post("/api/query", json={"query": "年假怎么申请？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert set(data) >= {"answer", "sources", "citations", "hallucination_check",
                         "iterations", "elapsed_ms"}
    assert data["answer"] == ANSWER
    assert "[1]" in data["answer"]
    assert data["citations"]["used"] == ["[1]"]
    assert data["citations"]["confirmed"] == 1.0
    assert data["hallucination_check"] == "passed"
    assert data["iterations"] == 0
    assert isinstance(data["elapsed_ms"], int)
    assert data["sources"], "必须返回来源"
    assert data["sources"][0]["filename"] == "员工手册.md", "filename 必须等于入库文件名"


def test_query_debug_trace(q_client):
    body = q_client.post("/api/query", json={"query": "年假怎么申请？", "debug": True}).json()
    assert body["code"] == 0
    trace = body["data"].get("debug_trace")
    assert trace, "debug=true 必须返回 debug_trace"
    assert trace[0]["iter"] == 0 and "evaluator_score" in trace[0]


def test_query_error_mapping(q_client):
    """LLM 抛 30001 → 对外 code=30001（LLMUnavailableError）。"""
    from app.core.errors import LLMUnavailableError

    class _FailLLM(StubLLM):
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            raise LLMUnavailableError("模拟 LLM 不可用")

    q_client.app.state.deps.orchestration.llm = _FailLLM()
    body = q_client.post("/api/query", json={"query": "年假怎么申请？"}).json()
    assert body["code"] == 30001


def test_query_validation_missing_query(q_client):
    body = q_client.post("/api/query", json={}).json()
    assert body["code"] == 40001


def test_query_agentic_mode_and_debug_fields(q_client):
    """P-D：mode=agentic 透传；debug 时返回 plan/steps/agent_trace。"""
    from app.core.domain import Answer

    class _Agent:
        def __init__(self):
            self.calls = 0

        def run(self, **kwargs):
            self.calls += 1
            return Answer(
                text="agent 答案[1]", sources=[], mode="agentic",
                agent_steps=2, plan={"goal": "g", "subgoals": []},
                steps=[{"step": 1, "action": "kb_search", "summary": "检索",
                        "evidence_ids": ["E1"]}],
                agent_trace=[{"span_id": "s1", "type": "plan"}],
                debug_trace=[{"iter": 0, "action": "plan", "summary": "g"}],
                termination_reason="answered",
                usage={"llm_calls": 2, "tool_calls": 1},
            )

    deps = q_client.app.state.deps
    agent = _Agent()
    deps.config.orchestration.agent.enabled = True
    deps.orchestration.agent_runtime = agent
    body = q_client.post("/api/query", json={
        "query": "年假怎么申请？", "mode": "agentic", "debug": True,
    }).json()
    assert body["code"] == 0
    data = body["data"]
    assert agent.calls == 1
    assert data["mode"] == "agentic"
    assert data["agent_steps"] == 2
    assert data["termination_reason"] == "answered"
    assert data["plan"]["goal"] == "g"
    assert data["steps"][0]["action"] == "kb_search"
    assert data["agent_trace"][0]["type"] == "plan"
    assert data["usage"]["llm_calls"] == 2


def test_detection_payload_ignores_out_of_range_citation():
    """6.4：越界 [999] 不得计入 citations.used（前端不再生成可点击角标）。"""
    from app.api.query import _detection_payload

    answer = type("_A", (), {
        "text": "年假 5 天[1]，另见[2][999]。",
        "hallucination": DetectionResult(True, [], 0.5),
    })()
    payload = _detection_payload(answer, 2)
    assert payload["citations"]["used"] == ["[1]", "[2]"]
    assert payload["citations"]["confirmed"] == 0.5


def test_detection_payload_sorts_citations_numerically():
    from app.api.query import _detection_payload

    answer = type("_A", (), {
        "text": "见[10]与[2]与[1]。",
        "hallucination": DetectionResult(True, [], 1.0),
    })()
    payload = _detection_payload(answer, 10)
    assert payload["citations"]["used"] == ["[1]", "[2]", "[10]"]
