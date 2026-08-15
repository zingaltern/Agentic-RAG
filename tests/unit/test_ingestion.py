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
