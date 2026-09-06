"""原件归档单测（P0-2，DEVELOPMENT.md 5.10）。

覆盖：归档布局与字节一致（sha256）/ duplicate 不重写 / disabled 不写 /
单删·批删·删库级联清文件 / 路径穿越文件名 / 写盘失败不阻塞入库。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.api.documents import get_document
from app.core.config import load_config
from app.core.registry import wire
from tests.unit.fakes import StubEmbedding

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"


@pytest.fixture()
def deps(tmp_path, isolated_stores):
    """装配：真实 Chroma（临时目录）+ 桩嵌入 + 归档目录指向 tmp_path。"""
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.ingestion.archive.enabled = True
    config.ingestion.archive.dir = str(tmp_path / "archive")
    return wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])


def test_archive_layout_and_sha256(deps):
    """上传成功后原件存在于 {dir}/{kb_id}/{document_id}{ext} 且字节与原件一致。"""
    raw = SAMPLE.read_bytes()
    job = deps.pipeline.ingest(raw, "员工手册.md")
    assert job.status == "success"
    entry = deps.documents.get(job.document_id)
    assert entry.archive_path is not None
    archived = Path(entry.archive_path)
    assert archived == Path(deps.config.ingestion.archive.dir) / "default" / f"{job.document_id}.md"
    assert archived.read_bytes() == raw, "归档字节必须与原件逐字节一致"
    assert hashlib.sha256(archived.read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()


def test_archive_disabled_no_file(tmp_path, isolated_stores):
    """归档关闭（默认）：不写盘、详情 archive_path 为 None。"""
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    assert config.ingestion.archive.enabled is False, "默认必须关闭"
    deps = wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    assert job.status == "success"
    entry = deps.documents.get(job.document_id)
    assert entry.archive_path is None
    assert not (tmp_path / "archive").exists(), "归档关闭不得创建归档目录"


def test_duplicate_does_not_rewrite_archive(deps):
    """重复上传（content_hash 命中）不重写归档文件（mtime 不变）。"""
    raw = SAMPLE.read_bytes()
    first = deps.pipeline.ingest(raw, "员工手册.md")
    archived = Path(deps.documents.get(first.document_id).archive_path)
    before = archived.stat().st_mtime_ns
    dup = deps.pipeline.ingest(raw, "员工手册副本.md")
    assert dup.document_id == first.document_id
    assert archived.stat().st_mtime_ns == before, "duplicate 命中不得重写归档"


def test_delete_document_cascades_archive(deps):
    """单删文档 → 归档原件同步删除。"""
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    archived = Path(deps.documents.get(job.document_id).archive_path)
    assert archived.exists()
    assert deps.documents.delete(job.document_id) is True
    assert not archived.exists(), "删除文档必须级联删除归档原件"


def test_delete_by_kb_cascades_archive(deps):
    """删库级联 → 该库全部归档原件删除。"""
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    archived = Path(deps.documents.get(job.document_id).archive_path)
    assert deps.documents.delete_by_kb("default") == 1
    assert not archived.exists(), "删库必须级联删除该库全部归档原件"


def test_batch_delete_cascades_archive(deps):
    """批量删除（API 层循环单删）→ 归档原件删除。"""
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    archived = Path(deps.documents.get(job.document_id).archive_path)
    assert deps.documents.delete(job.document_id) is True
    assert not archived.exists()


def test_path_traversal_filename_is_safe(deps):
    """恶意文件名（路径穿越形态）：ext 只取 suffix，落盘路径不得含 ..。"""
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "../../etc/passwd.txt")
    assert job.status == "success"
    entry = deps.documents.get(job.document_id)
    archived = Path(entry.archive_path)
    assert ".." not in archived.parts, "归档路径不得包含 ..（穿越段）"
    assert archived.name == f"{job.document_id}.txt", "文件名必须为 document_id + 受控后缀"
    assert archived.parent == Path(deps.config.ingestion.archive.dir) / "default"


def test_archive_write_failure_does_not_block_ingest(deps, monkeypatch):
    """归档写盘失败：仅告警不回滚入库，archive_path 置 None。"""
    import app.services.ingestion as ingestion_mod

    def boom(path, data, mode=0o600):
        raise OSError("模拟磁盘满")

    monkeypatch.setattr(ingestion_mod, "atomic_write_bytes", boom)
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    assert job.status == "success", "归档失败不得阻塞入库主流程"
    entry = deps.documents.get(job.document_id)
    assert entry.archive_path is None, "写失败时 archive_path 必须为 None（未归档语义）"


def test_detail_api_returns_archive_path(deps):
    """/api/documents/{id} 详情返回 archive_path 字段。"""
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    resp = get_document(job.document_id, request=None, deps=deps)
    assert resp["code"] == 0
    assert resp["data"]["archive_path"] == deps.documents.get(job.document_id).archive_path
