"""索引 DNA 与 rebuild CLI 单测（P1-3，DEVELOPMENT.md 5.10/13.5）。

覆盖：dna 纯函数稳定性/敏感性、collection metadata 写入与 legacy 容忍、
chunk/DocEntry 携带 dna、查询侧 audit/reject/关闭三态、KbStore.update、
rebuild 端到端（含回滚路径的缺失归档中止）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.vector_store.chroma_store import ChromaStore
from app.core.config import load_config
from app.core.errors import IndexDnaMismatchError
from app.core.index_dna import compute_index_dna
from app.core.registry import wire
from tests.unit.fakes import StubEmbedding

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"
SAMPLE2 = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "IT支持手册.md"


@pytest.fixture()
def deps(tmp_path, isolated_stores):
    """装配：真实 Chroma（临时目录）+ 桩嵌入（wire 会把 dna 写进新建集合 metadata）。"""
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.ingestion.archive.enabled = True
    config.ingestion.archive.dir = str(tmp_path / "archive")
    deps = wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    object.__setattr__(deps, "test_tmp_path", tmp_path)   # 供 legacy 用例另建 store
    return deps


def _rewire_with_mutation(deps, model_name: str):
    """模拟"改配置后重启服务"：同一持久化路径按新配置重新装配（dna 随之变化）。"""
    config = deps.config
    config.embedding.model_name = model_name
    return wire(config, embedding=StubEmbedding(dimension=512),
                kbs=deps.kbs, users=deps.users,
                documents=deps.documents, jobs=deps.jobs)


# -- dna 纯函数 -------------------------------------------------------------------

def test_dna_stable_and_sensitive():
    config = load_config()
    a = compute_index_dna(config)
    b = compute_index_dna(config)
    assert a["dna"] == b["dna"] and len(a["dna"]) == 12
    assert a["embedding_model"] == config.embedding.model_name

    changed = load_config()
    changed.embedding.model_name = "BAAI/bge-base-zh-v1.5"
    assert compute_index_dna(changed)["dna"] != a["dna"], "换 embedding 模型 dna 必须变化"

    changed2 = load_config()
    changed2.chunker.parent_target_tokens = 256
    assert compute_index_dna(changed2)["dna"] != a["dna"], "改分块参数 dna 必须变化"

    changed3 = load_config()
    changed3.cleaner.enabled = True
    assert compute_index_dna(changed3)["dna"] != a["dna"], "改清洗配置 dna 必须变化"


# -- collection metadata 与 legacy 容忍 -------------------------------------------

def test_new_collection_stamps_dna(deps):
    dna = compute_index_dna(deps.config)["dna"]
    deps.vector_store.count("kb")   # 触发集合创建
    assert deps.vector_store.get_collection_dna("kb") == dna


def test_legacy_store_without_dna_returns_none(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"))   # 不传 index_dna
    store.count("kb")
    assert store.get_collection_dna("kb") is None, "存量无 dna 集合按 legacy 语义返回 None"


# -- 入库携带 dna ------------------------------------------------------------------

def test_ingest_stamps_chunk_and_entry_dna(deps):
    dna = compute_index_dna(deps.config)["dna"]
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    assert job.status == "success"
    chunks = deps.vector_store.get_all_chunks()
    assert chunks and all(c.metadata.get("index_dna") == dna for c in chunks)
    assert deps.documents.get(job.document_id).index_dna == dna


# -- 查询侧校验三态 ----------------------------------------------------------------

def test_check_disabled_is_noop(deps):
    from app.api.query import _check_index_dna

    deps.config.index_dna.enabled = False
    assert _check_index_dna(deps, "kb") is False


def test_check_legacy_collection_passes(deps):
    """存量无 dna 集合：legacy 语义，不告警不拒绝。"""
    from app.api.query import _check_index_dna

    bare_store = ChromaStore(persist_dir=str(deps.test_tmp_path / "chroma_legacy"))
    deps2 = wire(deps.config, embedding=StubEmbedding(dimension=512),
                 vector_store=bare_store, kbs=deps.kbs, users=deps.users,
                 documents=deps.documents, jobs=deps.jobs)
    deps2.config.index_dna.enabled = True
    deps2.vector_store.count("kb")
    assert _check_index_dna(deps2, "kb") is False


def test_check_audit_flags_mismatch(deps):
    from app.api.query import _check_index_dna

    deps.vector_store.count("kb")   # 集合按旧 dna 创建
    deps.config.index_dna.enabled = True
    deps.config.index_dna.mode = "audit"
    deps.config.embedding.model_name = "BAAI/other-model"   # dna 变化（模拟重启后新配置）
    assert _check_index_dna(deps, "kb") is True, "audit 模式 dna 不一致应返回标记"


def test_check_reject_raises_mismatch(deps):
    from app.api.query import _check_index_dna

    deps.vector_store.count("kb")
    deps.config.index_dna.enabled = True
    deps.config.index_dna.mode = "reject"
    deps.config.embedding.model_name = "BAAI/other-model"
    with pytest.raises(IndexDnaMismatchError):
        _check_index_dna(deps, "kb")


# -- KbStore.update ----------------------------------------------------------------

def test_kb_update_switch_and_guards(deps):
    info = deps.kbs.create("重建测试库")
    expected_collection = f"{info.collection}__rebuild"   # update 原地修改对象，先取期望值
    updated = deps.kbs.update(info.kb_id, collection=expected_collection)
    assert updated.collection == expected_collection
    with pytest.raises(ValueError):
        deps.kbs.update(info.kb_id, collection="kb")     # 与默认库冲突
    with pytest.raises(ValueError):
        deps.kbs.update(info.kb_id, hack="x")            # 非法字段
    with pytest.raises(KeyError):
        deps.kbs.update("no_such_kb", collection="y")


# -- rebuild 端到端 -----------------------------------------------------------------

def test_rebuild_no_need_when_dna_same(deps):
    from app.cli.rebuild import rebuild_kb

    deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    result = rebuild_kb("default", yes=True, deps=deps)
    assert result["rebuilt"] is False, "dna 一致时不得重建"


def test_rebuild_end_to_end(deps):
    from app.cli.rebuild import rebuild_kb

    deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    deps.pipeline.ingest(SAMPLE2.read_bytes(), "IT支持手册.md")
    assert deps.documents.count("default") == 2
    old_collection = deps.kbs.get("default").collection
    old_dna = compute_index_dna(deps.config)["dna"]
    assert old_collection and old_dna

    # 模拟"改配置 + 重启服务"：换 embedding 模型名后按新配置重新装配
    deps2 = _rewire_with_mutation(deps, "BAAI/other-embed")
    new_dna = compute_index_dna(deps2.config)["dna"]
    assert new_dna != old_dna

    result = rebuild_kb("default", yes=True, deps=deps2)
    assert result["rebuilt"] is True
    assert result["documents"] == 2 and result["chunks"] > 0

    info = deps2.kbs.get("default")
    assert info.collection == result["new_collection"] == f"{old_collection}__rebuild"
    assert deps2.vector_store.get_collection_dna(info.collection) == new_dna
    assert deps2.vector_store.get_collection_dna(old_collection) is None, "旧集合必须删除"
    entries = [deps2.documents.get(d["document_id"]) for d in deps2.documents.list(page=1, size=10, kb_id="default")]
    assert len(entries) == 2 and all(e.index_dna == new_dna for e in entries)
    # 重建后检索可用：真实查询路径显式传 collection（orchestration 按 kb 指针解析），
    # BM25 索引对新 collection 按 epoch 首建
    results = deps2.retriever.retrieve("年假", k=3, collection=info.collection)
    assert results and all(c.chunk.metadata.get("index_dna") == new_dna for c in results)


def test_rebuild_aborts_when_archive_missing(deps):
    from app.cli.rebuild import rebuild_kb

    deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    entry = deps.documents.get(deps.documents.list(page=1, size=5, kb_id="default")[0]["document_id"])
    Path(entry.archive_path).unlink()   # 模拟归档丢失
    with pytest.raises(SystemExit):
        rebuild_kb("default", yes=True, deps=deps)
    # 旧世界不动：索引与集合保持原状
    assert deps.documents.count("default") == 1
    assert deps.kbs.get("default").collection != f"{deps.kbs.get('default').collection}__rebuild"
