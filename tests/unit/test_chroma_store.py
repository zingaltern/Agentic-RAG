"""Chroma 向量库单测（DEVELOPMENT.md 3.2）。"""
from __future__ import annotations

import math

import pytest

from app.adapters.vector_store.chroma_store import ChromaStore
from app.core.domain import Chunk
from tests.unit.fakes import StubEmbedding


def _chunk(chunk_id: str, text: str, **meta) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=meta.pop("document_id", "doc_x"),
        text=text,
        order=int(meta.pop("order", 0)),
        parent_id=meta.pop("parent_id", None),
        metadata=meta,
    )


@pytest.fixture()
def store(tmp_path):
    return ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")


@pytest.fixture()
def emb():
    return StubEmbedding(dimension=128)


def test_add_and_search_sorted_by_score(store, emb):
    texts = ["年假需提前三天申请", "IT 账号入职当天开通", "产品支持 MySQL 数据源"]
    chunks = [_chunk(f"c{i}", t, source=f"{t[:2]}.md", kind="md") for i, t in enumerate(texts)]
    vectors = emb.embed_documents(texts)
    store.add_chunks(zip(chunks, vectors))
    hits = store.search(emb.embed_query("年假申请"), k=3)
    assert hits[0].chunk.chunk_id == "c0", "与查询最相关的块应排第一"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "必须按相似度降序"


def test_identical_text_near_perfect_score(store, emb):
    """余弦相似度转换：相同向量 → distance 0 → score 1（query 向量用文档侧，避免指令前缀差异）。"""
    text = "年假需提前三个工作日通过 OA 申请"
    chunks = [_chunk("c1", text, source="a.md"), _chunk("c2", text + "（第二份）", source="b.md")]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    hits = store.search(emb.embed_documents([text])[0], k=2)
    assert hits[0].chunk.chunk_id == "c1"
    assert hits[0].score > 0.999, "相同文本余弦相似度应≈1"


def test_query_embedding_returns_above_zero_score(store, emb):
    """query 侧（带指令前缀）检索命中同主题文本，分数 > 0。"""
    text = "年假需提前三个工作日通过 OA 申请"
    store.add_chunks(zip([_chunk("c1", text)], emb.embed_documents([text])))
    hits = store.search(emb.embed_query("年假申请"), k=1)
    assert hits and hits[0].score > 0


def test_search_filters(store, emb):
    chunks = [
        _chunk("c1", "年假申请流程", kind="md", source="手册.md"),
        _chunk("c2", "年假申请流程", kind="txt", source="手册.txt"),
    ]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    hits = store.search(emb.embed_query("年假申请"), k=5, filters={"kind": "md"})
    assert [h.chunk.chunk_id for h in hits] == ["c1"]


def test_count_and_delete_document(store, emb):
    chunks = [
        _chunk("c1", "甲内容", document_id="doc_a"),
        _chunk("c2", "乙内容", document_id="doc_a"),
        _chunk("c3", "丙内容", document_id="doc_b"),
    ]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    assert store.count() == 3
    deleted = store.delete_document("doc_a")
    assert deleted == 2
    assert store.count() == 1
    assert store.delete_document("doc_zzz") == 0


@pytest.mark.integration
def test_drop_collection(store, emb):
    chunks = [_chunk("c1", "内容")]
    store.add_chunks(zip(chunks, emb.embed_documents(["内容"])))
    store.drop_collection("kb")
    assert store.count("kb") == 0


def test_none_metadata_stripped(store, emb):
    """Chroma 不允许 metadata=None，入库前剔除。"""
    chunks = [_chunk("c1", "内容", page=None, author=None, kind="pdf")]
    store.add_chunks(zip(chunks, emb.embed_documents(["内容"])))
    all_chunks = store.get_all_chunks()
    meta = all_chunks[0].metadata
    assert "page" not in meta and "author" not in meta
    assert meta["kind"] == "pdf"


def test_get_all_chunks_roundtrip(store, emb):
    chunks = [
        _chunk("c1", "甲", source="a.md", kind="md"),
        _chunk("c2", "乙", source="b.txt", kind="txt", parent_id="c1"),
    ]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    loaded = {c.chunk_id: c for c in store.get_all_chunks()}
    assert set(loaded) == {"c1", "c2"}
    assert loaded["c1"].metadata["source"] == "a.md"
    assert loaded["c2"].parent_id == "c1"
    assert loaded["c2"].metadata["kind"] == "txt"


def test_get_document_id_by_hash(store, emb):
    chunks = [_chunk("c1", "内容", document_id="doc_x", content_hash="abc123")]
    store.add_chunks(zip(chunks, emb.embed_documents(["内容"])))
    assert store.get_document_id_by_hash("abc123") == "doc_x"
    assert store.get_document_id_by_hash("nope") is None


def test_search_empty_collection(store, emb):
    hits = store.search(emb.embed_query("什么也没有"), k=3)
    assert hits == []
