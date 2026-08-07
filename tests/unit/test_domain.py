"""数据模型契约测试（DEVELOPMENT.md 2.1、2.3）。"""
from __future__ import annotations

import re

from app.core.domain import Chunk, DetectionResult, Document, EvaluationResult, Job, ScoredChunk

CHUNK_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _chunk(**kw):
    base = dict(chunk_id="a" * 16, document_id="doc_x", text="内容", order=0, parent_id=None, metadata={})
    base.update(kw)
    return Chunk(**base)


def test_chunk_required_fields():
    c = _chunk()
    assert c.parent_id is None
    assert c.order == 0
    assert c.metadata == {}


def test_scored_chunk_notion_default():
    sc = ScoredChunk(chunk=_chunk(), score=0.5)
    assert sc.notion == "rrf"


def test_document_defaults():
    d = Document(document_id="doc_abc", filename="a.txt", title="a", content_hash="h")
    assert d.text == ""
    assert d.metadata == {}
    assert d.chunks == []


def test_job_defaults():
    j = Job(job_id="ab12cd34", filename="f.md", status="pending")
    assert j.document_id is None
    assert j.chunk_count == 0
    assert j.error_code is None
    assert j.created_at == 0.0


def test_evaluation_result_reasons():
    for reason in ("ok", "below_threshold", "empty_context", "query_too_long"):
        r = EvaluationResult(passed=True, score=1.0, reason=reason)
        assert r.reason == reason


def test_detection_result():
    r = DetectionResult(verified=True, suspected=[], verified_rate=1.0)
    assert r.verified and r.verified_rate == 1.0
    assert isinstance(r.suspected, list)


def test_chunk_id_format_from_chunker():
    """2.1：chunk_id 为 sha256 前 16 位（16 个十六进制字符）。"""
    from app.adapters.chunker import split_chunks

    doc = Document(
        document_id="doc_abc", filename="a.md", title="a", content_hash="h",
        text="第一句。第二句！\n第三段内容。",
    )
    chunks = split_chunks(doc)
    assert chunks
    for c in chunks:
        assert CHUNK_ID_RE.match(c.chunk_id)
        assert c.document_id == "doc_abc"
