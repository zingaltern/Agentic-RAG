"""P0-P2 质量链路单测：入库 → 编排（完整上下文）→ IR/兜底指标（Stub 嵌入，无需真实模型）。"""
from __future__ import annotations

from pathlib import Path

from app.core.config import load_config
from app.core.registry import wire
from app.eval.metrics import aggregate_retrieval
from tests.unit.fakes import StubEmbedding

FIXTURE = Path(__file__).resolve().parent.parent / "quality" / "fixtures" / "员工手册.md"


class StubLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据员工手册，年假需提前 3 个工作日通过 OA 系统申请[1]。"


class IdentityReranker:
    def rerank(self, query, chunks):
        return list(chunks)


def _deps(tmp_path):
    from app.adapters.evaluator.rule_evaluator import RuleEvaluator
    from app.services.documents_store import DocumentsStore
    from app.services.jobs_store import JobStore
    from app.services.kb_store import KbStore

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    return wire(config, embedding=StubEmbedding(dimension=128), llm=StubLLM(),
                reranker=IdentityReranker(),
                evaluator=RuleEvaluator(rule_threshold=0.45, top_n=5),
                documents=DocumentsStore(persist_file=str(tmp_path / "documents.json")),
                jobs=JobStore(persist_file=str(tmp_path / "jobs.json")),
                kbs=KbStore(persist_file=str(tmp_path / "kb.json")))


def test_quality_flow_captures_full_context_and_ir_metrics(tmp_path):
    deps = _deps(tmp_path)
    collection = deps.kbs.collection_of("default")
    job = deps.pipeline.ingest(FIXTURE.read_bytes(), FIXTURE.name,
                               kb_id="default", collection=collection)
    assert job.status == "success", job.error_code

    result = deps.orchestration.run("年假怎么申请？", collection=collection,
                                    capture_contexts=True)
    assert result.code == 0
    assert result.contexts, "quality 模式必须拿到完整上下文"
    assert all(c["text"] and c["chunk_id"] for c in result.contexts)
    # 完整上下文长度不得小于 API sources 的 300 字截断版
    assert len(result.contexts[0]["text"]) >= len(result.sources[0]["chunk"])

    retrieved = [s["filename"] for s in result.sources]
    agg = aggregate_retrieval([{"retrieved": retrieved, "gold": ["员工手册.md"]}])
    assert agg["hit@5"] == 1.0 and agg["recall@5"] == 1.0
