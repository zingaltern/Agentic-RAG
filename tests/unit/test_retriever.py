"""混合检索器单测（DEVELOPMENT.md 5.4、3.3）。"""
from __future__ import annotations

import threading
import time

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


def test_cross_collection_isolation(tmp_path):
    """13.5 回归：retrieve(collection=B) 的 BM25 路不得泄露 A 库块（曾用构造默认 collection 查 BM25）。"""
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = StubEmbedding(dimension=128)
    chunk_a = _chunk("a1", "年假申请需提前三个工作日通过 OA 系统提交", "员工手册.md", "md")
    chunk_b = _chunk("b1", "差旅住宿费一线城市每晚五百元", "差旅政策.md", "md")
    store.add_chunks([(chunk_a, emb.embed_documents([chunk_a.text])[0])], collection="kb")
    store.add_chunks([(chunk_b, emb.embed_documents([chunk_b.text])[0])], collection="docs")

    retriever = _retriever(store, emb)
    hits_b = retriever.retrieve("住宿费一晚多少钱", k=5, collection="docs")
    assert hits_b, "B 库必须返回结果"
    for hit in hits_b:
        assert hit.chunk.metadata["source"] == "差旅政策.md", "BM25 路必须按 collection 作用域（防泄露）"
        assert hit.chunk.chunk_id != "a1"
    hits_a = retriever.retrieve("年假怎么申请", k=5, collection="kb")
    assert all(h.chunk.metadata["source"] == "员工手册.md" for h in hits_a)


# —— 2026-08-17：检索内并发 + query 级缓存 ——

class _CountingEmbedding(StubEmbedding):
    """统计 embed_query 调用次数，用于断言缓存命中跳过重算。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.query_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return super().embed_query(text)


def _counting_retriever(tmp_path) -> tuple[HybridRRFRetriever, _CountingEmbedding]:
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = _CountingEmbedding(dimension=128)
    chunks = [_chunk(*c) for c in CORPUS]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    return HybridRRFRetriever(embedding=emb, vector_store=store, top_k=5, candidates=20, rr_fused_k=60), emb


def test_query_cache_hit_skips_embedding(tmp_path):
    """相同 query 二次调用应命中缓存，不再调用 embed_query 与重检索。"""
    retriever, emb = _counting_retriever(tmp_path)
    q = "年假怎么申请"
    r1 = retriever.retrieve(q, k=5)
    calls_after_first = emb.query_calls
    assert calls_after_first >= 1
    r2 = retriever.retrieve(q, k=5)
    assert emb.query_calls == calls_after_first, "缓存命中不应再次调用 embed_query"
    assert [c.chunk.chunk_id for c in r1] == [c.chunk.chunk_id for c in r2], "命中缓存结果须一致"


def test_query_cache_reuse_across_k(tmp_path):
    """不同 k 应复用同一缓存（存完整结果，按 k 切片）。"""
    retriever, emb = _counting_retriever(tmp_path)
    q = "年假怎么申请"
    r1 = retriever.retrieve(q, k=2)
    r2 = retriever.retrieve(q, k=5)
    assert len(r1) <= 2 and len(r2) <= 5
    emb_after_r1 = emb.query_calls
    retriever.retrieve(q, k=3)
    assert emb.query_calls == emb_after_r1, "不同 k 应命中同一缓存"


def test_query_cache_disabled_when_maxsize_zero(tmp_path):
    """query_cache_maxsize=0 时退化为无缓存原行为，每次都重检索。"""
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = _CountingEmbedding(dimension=128)
    chunks = [_chunk(*c) for c in CORPUS]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    retriever = HybridRRFRetriever(embedding=emb, vector_store=store, top_k=5, candidates=20,
                                   rr_fused_k=60, query_cache_maxsize=0)
    q = "年假怎么申请"
    retriever.retrieve(q, k=5)
    n1 = emb.query_calls
    retriever.retrieve(q, k=5)
    assert emb.query_calls == n1 + 1, "关闭缓存后每次都应重新检索"


def test_query_cache_ttl_expiry(tmp_path):
    """TTL 过期后须重新检索（文档变更陈旧的兜底失效）。"""
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = _CountingEmbedding(dimension=128)
    chunks = [_chunk(*c) for c in CORPUS]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    retriever = HybridRRFRetriever(embedding=emb, vector_store=store, top_k=5, candidates=20,
                                   rr_fused_k=60, query_cache_ttl_seconds=1)
    q = "年假怎么申请"
    retriever.retrieve(q, k=5)
    n1 = emb.query_calls
    time.sleep(1.1)
    retriever.retrieve(q, k=5)
    assert emb.query_calls == n1 + 1, "TTL 过期后应重新检索"


def test_concurrent_retrieve_consistent(tmp_path):
    """8 线程并发 retrieve 同一 query，结果必须完全一致（无竞态 / 线程安全）。"""
    store = ChromaStore(persist_dir=str(tmp_path / "chroma"), collection="kb")
    emb = StubEmbedding(dimension=128)
    chunks = [_chunk(*c) for c in CORPUS]
    store.add_chunks(zip(chunks, emb.embed_documents([c.text for c in chunks])))
    retriever = HybridRRFRetriever(embedding=emb, vector_store=store, top_k=5, candidates=20, rr_fused_k=60)
    q = "年假怎么申请"
    results: list[list[str]] = []
    lock = threading.Lock()

    def worker():
        r = retriever.retrieve(q, k=5)
        with lock:
            results.append([c.chunk.chunk_id for c in r])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    first = results[0]
    assert all(r == first for r in results), "并发检索结果必须一致（无竞态）"
