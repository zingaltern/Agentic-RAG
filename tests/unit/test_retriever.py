"""混合检索器单测（DEVELOPMENT.md 5.4、3.3）。"""
from __future__ import annotations

import pytest

from app.adapters.retriever.hybrid_rrf import HybridRRFRetriever
from app.adapters.vector_store.chroma_store import ChromaStore
from app.core.domain import Chunk
from tests.unit.fakes import StubEmbedding

CORPUS = [
    # 员工手册相关
    ("p1", "年假申请需提前三个工作日通过 OA 系统提交，经部门负责人审批。", "员工手册.md", "md"),
    ("p2", "入职满一年年假五天，满两年及以上十天，最高累计二十天。", "员工手册.md", "md"),
    ("p3", "出差至一线城市伙食补贴每天一百五十元，其他城市每天一百元。", "员工手册.md", "md"),
    # IT 支持相关
    ("i1", "入职当天 IT 自动开通邮箱、VPN、IAM 账号，无需自行申请。", "IT支持手册.md", "md"),
    ("i2", "办公电脑故障请邮件 it-support@company.com 提交工单。", "IT支持手册.md", "md"),
    ("i3", "连续输错密码五次账号将被锁定，需联系 IT 部门解锁。", "IT支持手册.md", "md"),
]


def _chunk(chunk_id: str, text: str, source: str, kind: str, parent_id: str | None = None) -> Chunk:
    return Chunk(chunk_id=chunk_id, document_id=f"doc_{source[:2]}", text=text, order=0,
                 parent_id=parent_id, metadata={"source": source, "kind": kind})


@pytest.fixture()
def corpus_store(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = StubEmbedding(dimension=128)
    chunks = [_chunk(*c) for c in CORPUS]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    return store, emb


def _retriever(store, emb) -> HybridRRFRetriever:
    return HybridRRFRetriever(embedding=emb, vector_store=store, top_k=5, candidates=20, rr_fused_k=60)


def test_retrieve_hits_correct_document(corpus_store):
    store, emb = corpus_store
    results = _retriever(store, emb).retrieve("年假怎么申请，提前几天？", k=5)
    assert results, "必须返回候选"
    top_source = results[0].chunk.metadata["source"]
    assert top_source == "员工手册.md"
    assert all(r.notion == "rrf" for r in results)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True), "结果必须按分数降序"


def test_retrieve_k_limit(corpus_store):
    store, emb = corpus_store
    results = _retriever(store, emb).retrieve("公司福利政策内容信息", k=2)
    assert len(results) <= 2


def test_filters_applied_to_both_paths(corpus_store):
    """3.3 硬约束：filters 必须同时作用于 BM25 与向量两路。"""
    store, emb = corpus_store
    retriever = _retriever(store, emb)
    results = retriever.retrieve("年假 IT 账号 密码", k=5, filters={"metadata": {"kind": "md"}})
    assert results
    assert all(r.chunk.metadata["kind"] == "md" for r in results)
    # 换个不存在的 source 过滤 → 空结果（两路都被过滤）
    empty = retriever.retrieve("年假申请", k=5, filters={"metadata": {"source": "不存在.md"}})
    assert empty == []


def test_bm25_path_alone_works(corpus_store):
    """查询指令改变向量路，但 BM25 路仍应命中关键词文档。"""
    store, emb = corpus_store
    results = _retriever(store, emb).retrieve("加班 调休 年假", k=3)
    sources = [r.chunk.metadata["source"] for r in results]
    assert "员工手册.md" in sources


def test_parent_family_dedup(corpus_store):
    """5.4 第 4 步：同一 parent_id 家族最多取 2 条。"""
    store, emb = corpus_store
    children = [
        _chunk("cc1", "年假申请需提前三个工作日通过 OA 系统提交，经部门负责人审批后生效。", "员工手册.md", "md", parent_id="pp"),
        _chunk("cc2", "年假申请流程第二步：系统自动通知直属主管。", "员工手册.md", "md", parent_id="pp"),
        _chunk("cc3", "年假申请流程第三步：主管审批完成后生效。", "员工手册.md", "md", parent_id="pp"),
        _chunk("cc4", "年假申请补充说明：可合并请半天。", "员工手册.md", "md", parent_id="pp"),
    ]
    store.add_chunks(zip(children, emb.embed_documents([c.text for c in children])))
    retriever = _retriever(store, emb)
    results = retriever.retrieve("年假申请流程", k=10)
    family_hits = [r for r in results if r.chunk.parent_id == "pp"]
    assert len(family_hits) <= 2, "同一父块的子块最多进 2 条"


def test_empty_corpus_returns_empty(tmp_path):
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = StubEmbedding(dimension=128)
    assert _retriever(store, emb).retrieve("空库查询", k=5) == []
