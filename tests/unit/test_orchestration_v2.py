"""编排循环 v2 单测（DEVELOPMENT.md 13.3.3）：query 必变换路 / 多路融合 / wire 装配注入。"""
from __future__ import annotations

from app.core.config import load_config
from app.core.domain import Chunk, EvaluationResult, ScoredChunk
from app.services.orchestration import OrchestrationService
from tests.unit.fakes import StubEmbedding


def _scored(text: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(chunk_id=f"c{hash(text) % 10000}", document_id="doc_x", text=text,
                    order=0, parent_id=None, metadata={"source": "员工手册.md", "kind": "md"}),
        score=0.8, notion="rrf")


class _Retriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append({"query": query, "filters": filters, "path": path,
                           "collection": collection})
        return self.chunks[:k]


class _Eval:
    def __init__(self, results):
        self.results = list(results)

    def evaluate(self, query, chunks):
        return self.results.pop(0)


class _Reranker:
    def rerank(self, query, chunks):
        return list(chunks)


class _Rewrite:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def rewrite(self, query, feedback):
        self.calls.append(query)
        return self.outputs.pop(0)


class _Detect:
    def detect(self, answer, context):
        from app.core.domain import DetectionResult

        return DetectionResult(verified=True, suspected=[], verified_rate=1.0)


class _LLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "年假需提前 3 个工作日申请[1]。"


def _build(retriever, evalr, rewriter, config=None):
    config = config or load_config()
    return OrchestrationService(
        config=config, retriever=retriever, reranker=_Reranker(),
        evaluator=evalr, rewriter=rewriter, detector=_Detect(),
        llm=_LLM(), vector_store=None,
    )


def test_query_must_change_switch_path():
    """13.3.3-1：改写无变化 → 自动换路（去停用词 / BM25-only / 向量-only），主 query 与上轮不同。"""
    retriever = _Retriever([_scored("年假申请流程说明")])
    evaluator = _Eval([
        EvaluationResult(False, 0.2, "below_threshold"),
        EvaluationResult(True, 0.9, "ok"),
    ])
    rewriter = _Rewrite(["请问年假申请流程说明"])   # 改写结果与上轮"主 query 重切"相同 → 触发换路
    svc = _build(retriever, evaluator, rewriter)
    answer = svc.run("请问年假申请流程说明", debug=True)

    assert answer.code == 0
    calls = retriever.calls
    assert len(calls) >= 2, "至少两轮检索"
    assert calls[0]["path"] == "hybrid"
    # 第二轮走换路：去停用词重切(hybrid) + bm25 + vector 三路
    paths = [c["path"] for c in calls]
    assert "bm25" in paths and "vector" in paths, "换路必须含 BM25-only 与向量-only"
    # 每轮主 query 与上轮不同（13.3.3 硬约束：去停用词重切生效）
    queries = [c["query"] for c in calls]
    assert queries[0] == "请问年假申请流程说明"
    assert queries[1] != queries[0]
    trace = answer.debug_trace
    switched_steps = [t for t in trace if t.get("switched")]
    assert switched_steps, "debug_trace 必须记录换路"


def test_multi_variant_fusion():
    """13.3.2 多路融合：LLM 产出 2 变体 → 每轮 2 次检索后融合评估。"""
    retriever = _Retriever([_scored("年假申请需提前提交"), _scored("IT 账号开通")])
    evaluator = _Eval([
        EvaluationResult(False, 0.2, "below_threshold"),
        EvaluationResult(True, 0.9, "ok"),
    ])
    rewriter = _Rewrite([["年假申请流程", "年假 提交 审批"]])   # llm 风格多变体
    svc = _build(retriever, evaluator, rewriter)
    answer = svc.run("年假咋申请", debug=True)

    assert answer.code == 0
    calls = retriever.calls
    assert len(calls) == 3, "第一轮 1 变体 + 第二轮 2 变体 = 3 次检索"
    assert calls[1]["query"] == "年假申请流程"
    assert calls[2]["query"] == "年假 提交 审批"
    assert all(c["filters"] == {} for c in calls), "filters 贯穿所有变体"


def test_filters_through_all_variants():
    retriever = _Retriever([_scored("年假申请")])
    evaluator = _Eval([EvaluationResult(True, 0.9, "ok")])
    rewriter = _Rewrite([["年假申请流程", "年假提交"]])
    svc = _build(retriever, evaluator, rewriter)
    svc.run("年假怎么申请", filters={"metadata": {"kind": "md"}})
    assert all(c["filters"] == {"metadata": {"kind": "md"}} for c in retriever.calls)


def test_debug_trace_records_actual_query_and_variants():
    retriever = _Retriever([_scored("年假申请")])
    evaluator = _Eval([
        EvaluationResult(False, 0.2, "below_threshold"),
        EvaluationResult(True, 0.9, "ok"),
    ])
    rewriter = _Rewrite(["年假申请流程"])
    svc = _build(retriever, evaluator, rewriter)
    answer = svc.run("年假咋申请", debug=True)
    steps = [t for t in answer.debug_trace if "evaluator_score" in t]
    assert steps[0]["query"] == "年假咋申请"
    assert steps[1]["query"] == "年假申请流程"
    rewritten = [t for t in answer.debug_trace if "rewritten" in t]
    assert rewritten[0]["rewritten"] == ["年假申请流程"]


def test_wire_injects_embedding_and_llm(tmp_path, isolated_stores):
    """装配级：wire 把 embedding 注入 evaluator/detector，llm 注入 rewriter。"""
    from app.core.registry import wire

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    deps = wire(config, embedding=StubEmbedding(dimension=128), llm=_LLM(),
                reranker=_Reranker(), kbs=isolated_stores["kbs"],
                users=isolated_stores["users"], documents=isolated_stores["documents"])
    from app.adapters.evaluator.semantic_v2 import SemanticV2Evaluator
    from app.adapters.detector.semantic_verifier import SemanticVerifierDetector
    from app.adapters.rewriter.llm_rewriter import LLMRewriter

    assert isinstance(deps.evaluator, SemanticV2Evaluator)
    assert deps.evaluator.embedding is deps.embedding
    assert isinstance(deps.detector, SemanticVerifierDetector)
    assert deps.detector.embedding is deps.embedding
    assert isinstance(deps.rewriter, LLMRewriter)
    assert deps.rewriter.llm is deps.llm
    assert deps.orchestration is not None


def test_wire_fallback_providers_switchable(tmp_path, isolated_stores):
    """旧 provider 可切回：identity/rule/citation 装配正常。"""
    from app.core.registry import wire

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.reranker.provider = "identity"
    config.evaluator.provider = "rule"
    config.rewriter.provider = "rule"
    config.detector.provider = "citation"
    deps = wire(config, embedding=StubEmbedding(dimension=128), llm=_LLM(),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"])
    assert deps.reranker is not None and deps.evaluator is not None
    assert deps.rewriter is not None and deps.detector is not None
    assert deps.orchestration is not None
