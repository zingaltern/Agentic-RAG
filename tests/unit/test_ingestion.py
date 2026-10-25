"""上传入库流水线单测（DEVELOPMENT.md 5.10）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.core.config import load_config
from app.core.registry import wire
from app.services.ingestion import compute_content_hash
from tests.unit.fakes import StubEmbedding

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"


@pytest.fixture()
def deps(tmp_path, isolated_stores):
    """装配：真实 Chroma（临时目录）+ 桩嵌入，不触发模型下载。"""
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    return wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])


def test_ingest_success_job_and_documents(deps):
    raw = SAMPLE.read_bytes()
    job = deps.pipeline.ingest(raw, "员工手册.md")
    assert job.status == "success"
    assert job.document_id
    assert job.chunk_count > 0
    assert deps.documents.get(job.document_id).filename == "员工手册.md"
    assert deps.vector_store.count() == job.chunk_count


def test_ingest_chunks_retrievable(deps):
    raw = SAMPLE.read_bytes()
    deps.pipeline.ingest(raw, "员工手册.md")
    results = deps.retriever.retrieve("年假怎么申请？", k=5)
    assert results
    assert results[0].chunk.metadata["source"] == "员工手册.md"


def test_duplicate_upload_idempotent(deps):
    raw = SAMPLE.read_bytes()
    job1 = deps.pipeline.ingest(raw, "员工手册.md")
    job2 = deps.pipeline.ingest(raw, "员工手册副本.md")
    assert job2.status == "success"
    assert job2.document_id == job1.document_id, "相同 content_hash 必须复用既有 document_id"
    assert deps.vector_store.count() == job1.chunk_count, "不得重复入库"


def test_duplicate_detection_via_hash(deps):
    raw = SAMPLE.read_bytes()
    first = deps.pipeline.ingest(raw, "员工手册.md")
    content_hash = compute_content_hash(raw)
    found = deps.pipeline.find_document_id_by_hash(content_hash)
    assert found == first.document_id


def test_ingest_parse_error_marks_job_failed(deps):
    job = deps.pipeline.ingest(b"this is not a real pdf", "broken.pdf")
    assert job.status == "failed"
    assert job.error_code == "30003"
    assert deps.jobs.get(job.job_id).error_code == "30003"


def test_ingest_unsupported_extension(deps):
    job = deps.pipeline.ingest(b"data", "evil.xyz")
    assert job.status == "failed"
    assert job.error_code == "30003"


def test_submit_async_polls_to_success(deps):
    raw = SAMPLE.read_bytes()
    job = deps.jobs.create("员工手册.md")
    deps.pipeline.submit(raw, "员工手册.md", job)
    deadline = time.time() + 10
    while time.time() < deadline:
        current = deps.jobs.get(job.job_id)
        if current.status in ("success", "failed"):
            break
        time.sleep(0.05)
    assert current.status == "success"
    assert current.document_id and current.chunk_count > 0


def test_filters_apply_to_ingested_docs(deps):
    deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    results = deps.retriever.retrieve("年假", k=5, filters={"metadata": {"kind": "txt"}})
    assert results == [], "不存在的 kind 过滤应返回空（两路都过滤）"
    results = deps.retriever.retrieve("年假", k=5, filters={"metadata": {"source": "员工手册.md"}})
    assert results and all(r.chunk.metadata["source"] == "员工手册.md" for r in results)


# -- V5.2：分块精确计量接线 ---------------------------------------------------------

LONG_TEXT = ("\n".join(f"第{i}段。" + "内容说明" * 40 for i in range(60))).encode("utf-8")


class _CountingEmbedding(StubEmbedding):
    """提供 count_tokens（1 字符=1 token 的确定性计数）的桩嵌入。"""

    def __init__(self, dimension: int = 512):
        super().__init__(dimension=dimension)
        self.calls = 0

    def count_tokens(self, text: str) -> int | None:
        self.calls += 1
        return len(text)


def _make_deps(tmp_path, isolated_stores, *, measure: str, embedding):
    """独立存储装配（避免同一 raw 命中幂等表导致第二次装配跳过入库）。"""
    from app.services.documents_store import DocumentsStore
    from app.services.jobs_store import JobStore
    from app.services.kb_store import KbStore
    from app.services.user_store import UserStore

    tag = f"{measure}-{id(embedding)}"
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / f"chroma-{tag}")
    config.documents.persist_file = str(tmp_path / f"documents-{tag}.json")
    config.kbs.persist_file = str(tmp_path / f"kb-{tag}.json")
    config.chunker.measure = measure
    return wire(config, embedding=embedding,
                kbs=KbStore(persist_file=str(tmp_path / f"kb-{tag}.json")),
                users=UserStore(persist_file=str(tmp_path / f"users-{tag}.json")),
                documents=DocumentsStore(persist_file=str(tmp_path / f"documents-{tag}.json")),
                jobs=JobStore(persist_file=str(tmp_path / f"jobs-{tag}.json")))


def test_measure_auto_uses_engine_token_counter(tmp_path, isolated_stores):
    """V5.2：measure=auto 且引擎有 count_tokens → 精确计量（父块数多于 ratio 口径）。"""
    exact_emb = _CountingEmbedding()
    deps_exact = _make_deps(tmp_path, isolated_stores, measure="auto", embedding=exact_emb)
    job_exact = deps_exact.pipeline.ingest(LONG_TEXT, "长文.md")
    assert job_exact.status == "success" and job_exact.chunk_count > 0
    assert exact_emb.calls > 0, "引擎计数器必须被调用"

    deps_ratio = _make_deps(tmp_path, isolated_stores, measure="ratio",
                            embedding=StubEmbedding(dimension=512))
    job_ratio = deps_ratio.pipeline.ingest(LONG_TEXT, "长文.md")
    assert job_ratio.status == "success"
    assert job_exact.chunk_count > job_ratio.chunk_count, \
        "1 字符/token 的中文文本：精确口径必须切得更碎（防截断）"


def test_measure_ratio_ignores_engine_counter(tmp_path, isolated_stores):
    """V5.2：measure=ratio 强制旧口径（引擎计数器不被调用）。"""
    emb = _CountingEmbedding()
    deps = _make_deps(tmp_path, isolated_stores, measure="ratio", embedding=emb)
    job = deps.pipeline.ingest(LONG_TEXT, "长文.md")
    assert job.status == "success" and emb.calls == 0
