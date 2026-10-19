"""P1 质量链路集成测试：真实 BGE + 临时 Chroma + Stub LLM，验证完整上下文与 IR 指标。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.reranker.identity import IdentityReranker
from app.core.config import load_config
from app.core.registry import wire
from app.eval.metrics import aggregate_retrieval
from app.services.documents_store import DocumentsStore
from app.services.jobs_store import JobStore
from app.services.kb_store import KbStore

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).resolve().parent.parent / "quality" / "fixtures" / "员工手册.md"


class StubLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据员工手册，年假需提前 3 个工作日通过 OA 系统申请[1]。"


def test_quality_loop_real_embedding(tmp_path):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    deps = wire(
        config, llm=StubLLM(), reranker=IdentityReranker(rerank_top_k=5),
        documents=DocumentsStore(persist_file=str(tmp_path / "documents.json")),
        jobs=JobStore(persist_file=str(tmp_path / "jobs.json")),
        kbs=KbStore(persist_file=str(tmp_path / "kb.json")),
    )
    collection = deps.kbs.collection_of("default")
    job = deps.pipeline.ingest(FIXTURE.read_bytes(), FIXTURE.name,
                               kb_id="default", collection=collection)
    assert job.status == "success", job.error_code

    result = deps.orchestration.run("年假怎么申请？", collection=collection,
                                    capture_contexts=True)
    assert result.code == 0
    assert result.contexts and all(c["text"] and c["chunk_id"] for c in result.contexts)
    assert len(result.contexts[0]["text"]) >= len(result.sources[0]["chunk"])

    agg = aggregate_retrieval([{"retrieved": [s["filename"] for s in result.sources],
                                "gold": ["员工手册.md"]}])
    assert agg["hit@5"] == 1.0
