"""内容审核层单测（P-C，入库安全）：词表加载兜底 / 命中判定 / reject|flag 接线。"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from app.adapters.content_moderation.keyword_moderator import (
    KeywordModerator,
    _load_keywords,
)
from app.core.config import load_config
from app.services.documents_store import DocumentsStore
from app.services.ingestion import IngestionPipeline
from app.services.jobs_store import JobStore
from tests.unit.fakes import StubEmbedding


class _FakeVectorStore:
    def __init__(self):
        self.chunks = []

    def add_chunks(self, chunks, collection="kb"):
        self.chunks.extend(chunks)

    def count(self, collection="kb"):
        return len(self.chunks)

    def get_document_id_by_hash(self, content_hash, collection="kb"):
        return None


def test_keyword_hit():
    result = KeywordModerator().moderate("页面含敏感政治议题相关内容")
    assert result.verdict == "reject"
    assert any(r["category"] == "政治" for r in result.matched_rules)
    assert "敏感政治议题" in result.snippet


def test_keyword_miss():
    result = KeywordModerator().moderate("年假申请流程说明")
    assert result.verdict == "ok"
    assert result.matched_rules == []


def test_empty_text():
    result = KeywordModerator().moderate("")
    assert result.verdict == "ok"


def test_csv_load_roundtrip(tmp_path):
    csv_file = tmp_path / "moderation_keywords.csv"
    csv_file.write_text("# 注释行\n政治,测试词A\n违法,测试词B\n", encoding="utf-8")
    keywords = _load_keywords(str(csv_file))
    assert ("政治", "测试词A") in keywords
    assert ("违法", "测试词B") in keywords


def test_csv_missing_falls_back():
    keywords = _load_keywords("/nonexistent/moderation_keywords.csv")
    assert keywords, "缺失时用内置最小词表兜底"


def test_csv_corrupt_falls_back(tmp_path):
    csv_file = tmp_path / "moderation_keywords.csv"
    csv_file.write_bytes(b"\xff\xfe broken")
    keywords = _load_keywords(str(csv_file))
    assert keywords, "损坏时用内置最小词表兜底，不崩溃"


def test_csv_blank_lines_skipped(tmp_path):
    csv_file = tmp_path / "moderation_keywords.csv"
    csv_file.write_text("\n\n# 注释\n政治,词A\n\n", encoding="utf-8")
    keywords = _load_keywords(str(csv_file))
    assert len(keywords) == 1


def _config(tmp_path, moderation_enabled=True, mode="reject"):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.moderation.enabled = moderation_enabled
    config.moderation.mode = mode
    return config


def _ingest_with(config, raw_text: str, filename: str = "审核样本.md"):
    tmp = pathlib.Path(tempfile.mkdtemp())
    jobs = JobStore(persist_file=str(tmp / "jobs.json"))
    docs = DocumentsStore(persist_file=str(tmp / "documents.json"))
    store = _FakeVectorStore()
    pipeline = IngestionPipeline(config=config, embedding=StubEmbedding(dimension=128),
                                 vector_store=store, jobs=jobs, documents=docs,
                                 start_workers=False)
    return pipeline, pipeline.ingest(raw_text.encode("utf-8"), filename), store


def test_reject_mode_marks_job_failed_40003(tmp_path):
    config = _config(tmp_path, mode="reject")
    pipeline, job, store = _ingest_with(config, "普通文本，含敏感政治议题一句。")
    assert job.status == "failed"
    assert job.error_code == "40003"


def test_flag_mode_ingests(tmp_path):
    config = _config(tmp_path, mode="flag")
    pipeline, job, store = _ingest_with(config, "普通文本，含敏感政治议题一句。")
    assert job.status == "success", "flag 模式不拒收"
    assert job.error_code is None
    # flag 模式核心行为：chunk metadata 必须带 moderation_flags 标记
    metas = [dict(chunk.metadata) for chunk, _ in store.chunks]
    assert any("moderation_flags" in m for m in metas), "命中文本的 chunk 必须写入 moderation_flags"
    assert metas[0]["moderation_flags"][0]["category"] == "政治"


def test_disabled_zero_regression(tmp_path):
    """默认关闭：含敏感词文档正常入库（验收标准 1）。"""
    config = _config(tmp_path, moderation_enabled=False)
    pipeline, job, store = _ingest_with(config, "敏感政治议题这类词不影响入库。")
    assert job.status == "success"
