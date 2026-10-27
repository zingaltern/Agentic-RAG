"""并发加固单测（V5.1：C1-C6）。"""
from __future__ import annotations

import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import LocalModelAdapter
from app.adapters.retriever.hybrid_rrf import HybridRRFRetriever
from app.application import create_app
from app.core.config import load_config
from app.core.errors import EmbeddingError
from app.services.ingestion import IngestionPipeline
from app.services.jobs_store import JobStore
from tests.unit.fakes import StubEmbedding


# -- C1：LocalModelAdapter 懒加载互斥 -------------------------------------------------

class _CountingFactory:
    def __init__(self, fail_first: bool = False):
        self.calls = 0
        self.lock = threading.Lock()
        self.fail_first = fail_first

    def __call__(self):
        with self.lock:
            self.calls += 1
            first = self.calls == 1
        time.sleep(0.05)
        if self.fail_first and first:
            raise RuntimeError("首次加载失败")
        return object()


class _DummyAdapter(LocalModelAdapter):
    def _load_model(self):
        return object()


def test_c1_concurrent_first_ready_loads_once():
    factory = _CountingFactory()
    adapter = _DummyAdapter(model_name="stub", model_factory=factory)
    barrier = threading.Barrier(2)
    results = []

    def load():
        barrier.wait()
        try:
            results.append(adapter.ready())
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=load) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert factory.calls == 1, "并发首次 ready 模型工厂只能被调用 1 次"
    assert all(r is True for r in results)


def test_c1_failure_allows_retry():
    factory = _CountingFactory(fail_first=True)
    adapter = _DummyAdapter(model_name="stub", model_factory=factory)
    with pytest.raises(EmbeddingError):
        adapter.ready()
    assert factory.calls == 1
    assert adapter.ready() is True, "加载失败恢复 None 语义后应可重试成功"
    assert factory.calls == 2


# -- C2：BM25 索引缓存互斥 -------------------------------------------------------------

class _CountingStore:
    def __init__(self, chunks):
        self.chunks = chunks
        self.build_calls = 0
        self.lock = threading.Lock()

    def count(self, collection):
        return len(self.chunks)

    def get_all_chunks(self, collection):
        with self.lock:
            self.build_calls += 1
        time.sleep(0.05)
        return self.chunks

    def search(self, query_vector, k, filters=None, collection="kb"):
        return []


class _StubEmbedding2:
    def embed_query(self, text):
        return [0.0] * 4


def _chunk(cid, text):
    from app.core.domain import Chunk

    return Chunk(chunk_id=cid, document_id="d", text=text, order=0,
                 parent_id=None, metadata={"source": f"{cid}.md", "kind": "md"})


def test_c2_concurrent_first_retrieve_builds_once():
    store = _CountingStore([_chunk("c1", "年假申请流程")])
    retriever = HybridRRFRetriever(embedding=_StubEmbedding2(), vector_store=store,
                                   top_k=5, candidates=10, rr_fused_k=60)
    barrier = threading.Barrier(2)

    def search():
        barrier.wait()
        return retriever.retrieve("年假", k=5, collection="kb")

    threads = [threading.Thread(target=search) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.build_calls == 1, "并发 cache miss 只能构建一次 BM25 索引"


def test_c2_filter_cache_keys_isolated():
    store = _CountingStore([_chunk("c1", "年假申请流程"), _chunk("c2", "IT 账号开通")])
    retriever = HybridRRFRetriever(embedding=_StubEmbedding2(), vector_store=store,
                                   top_k=5, candidates=10, rr_fused_k=60)
    retriever.retrieve("年假", k=5, collection="kb", filters={"metadata": {"kind": "md"}})
    retriever.retrieve("年假", k=5, collection="kb", filters={"metadata": {"kind": "txt"}})
    assert store.build_calls == 2, "不同 filter 是独立缓存键，各自构建一次"
    retriever.retrieve("年假", k=5, collection="kb", filters={"metadata": {"kind": "md"}})
    assert store.build_calls == 2, "同键命中缓存不再构建"


class _EpochStore(_CountingStore):
    """支持 mutation_epoch 的存储：add/delete 递增纪元（count 可能不变）。"""

    def __init__(self, chunks):
        super().__init__(chunks)
        self._epoch = 0

    def mutation_epoch(self, collection="kb"):
        return self._epoch

    def add_chunks(self, chunks, collection="kb"):
        self.chunks.extend(chunks)
        self._epoch += 1

    def delete_chunk(self, chunk_id):
        self.chunks = [c for c in self.chunks if c.chunk_id != chunk_id]
        self._epoch += 1


def test_c2_epoch_invalidation_rebuilds_after_delete_plus_add():
    """C2：删 N 块 + 增 N 块（count 不变）→ epoch 变化强制重建缓存，已删文档不得残留可检索。"""
    store = _EpochStore([_chunk("c1", "年假申请流程"), _chunk("c2", "IT 账号开通"), _chunk("c3", "差旅报销")])
    retriever = HybridRRFRetriever(embedding=_StubEmbedding2(), vector_store=store,
                                   top_k=5, candidates=10, rr_fused_k=60)
    retriever.retrieve("年假", k=5, collection="kb")
    assert store.build_calls == 1
    store.delete_chunk("c1")                                   # 删 1 块
    store.add_chunks([_chunk("c4", "产品支持 MySQL 数据源")])   # 增 1 块 → count 回到 3
    assert store.count("kb") == 3, "count 不变，正是陈旧缓存的触发条件"
    retriever.retrieve("年假", k=5, collection="kb")
    assert store.build_calls == 2, "count 相同但 epoch 变化 → 缓存必须重建"
    import json as _json

    key = _json.dumps(["kb", []], ensure_ascii=False, sort_keys=True)
    _, _, _, corpus = retriever._bm25_cache[key]
    assert all(c.chunk_id != "c1" for c in corpus), "已删除块不得残留在 BM25 语料中"


# -- C3：并发信号量 43001 --------------------------------------------------------------

def _app_no_components(tmp_path):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.api.auth_enabled = False
    return create_app(config=config, components=False, prewarm=False)


def test_c3_semaphore_exhausted_returns_43001(tmp_path):
    app = _app_no_components(tmp_path)
    app.state.query_semaphore = threading.BoundedSemaphore(1)
    with TestClient(app) as client:
        assert app.state.query_semaphore.acquire(blocking=False), "占用信号量模拟并发中"
        try:
            body = client.post("/api/query", json={"query": "年假"}).json()
            assert body["code"] == 43001, "信号量耗尽应立即返回 43001"
        finally:
            app.state.query_semaphore.release()
        body = client.post("/api/query", json={"query": "年假"}).json()
        assert body["code"] != 43001, "释放后可恢复（进入查询链路，components=False 返回 50001 属预期）"


def test_c3_acquire_timeout_path(tmp_path):
    """等待超时（semaphore_acquire_timeout_seconds=0）时也返回 43001。"""
    app = _app_no_components(tmp_path)
    app.state.config.orchestration.semaphore_acquire_timeout_seconds = 0.0
    app.state.query_semaphore = threading.BoundedSemaphore(1)
    with TestClient(app) as client:
        app.state.query_semaphore.acquire(blocking=False)
        try:
            body = client.post("/api/query", json={"query": "年假"}).json()
            assert body["code"] == 43001
        finally:
            app.state.query_semaphore.release()


# -- C4：滑动窗口限速 -----------------------------------------------------------------

from app.core.rate_limit import SlidingWindowRateLimiter  # noqa: E402


def test_c4_rate_limiter_window_slides():
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=0.1)
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is False, "窗口内超限拒绝"
    time.sleep(0.15)
    assert limiter.allow("ip1") is True, "窗口滑动后恢复"


def test_c4_rate_limiter_per_key():
    limiter = SlidingWindowRateLimiter(limit=1)
    assert limiter.allow("a") is True and limiter.allow("b") is True
    assert limiter.allow("a") is False, "不同 key 互不影响"


def test_c4_query_route_returns_43002(tmp_path):
    app = _app_no_components(tmp_path)
    with TestClient(app) as client:
        app.state.rate_limiters["query"] = SlidingWindowRateLimiter(limit=1)
        assert client.post("/api/query", json={"query": "a"}).json()["code"] != 43002
        body = client.post("/api/query", json={"query": "a"}).json()
        assert body["code"] == 43002, "超限返回 43002 契约包"


def test_c4_upload_route_returns_43002(tmp_path):
    app = _app_no_components(tmp_path)
    with TestClient(app) as client:
        app.state.rate_limiters["upload"] = SlidingWindowRateLimiter(limit=1)
        files = {"file": ("a.md", b"x", "text/markdown")}
        assert client.post("/api/documents/upload", files=files).json()["code"] != 43002
        body = client.post("/api/documents/upload", files={"file": ("b.md", b"y", "text/markdown")}).json()
        assert body["code"] == 43002


def test_c4_rate_limit_config_parses():
    config = load_config()
    assert config.api.query_rate_per_min == 20
    assert config.api.upload_rate_per_min == 10
    assert config.orchestration.max_concurrent_queries == 4


def test_c6_zero_value_configs_rejected():
    """C6：0/负数配置必须抛 SystemConfigError（0 会让 queue.Queue 无界/限速永久拒绝）。"""
    from app.core.errors import SystemConfigError

    config = load_config()
    config.api.query_rate_per_min = 0
    with pytest.raises(SystemConfigError):
        config.model_validate(config.model_dump())
    config.api.query_rate_per_min = 20
    config.api.upload_rate_per_min = 0
    with pytest.raises(SystemConfigError):
        config.model_validate(config.model_dump())
    config.api.upload_rate_per_min = 10
    config.ingestion.max_queue_size = 0
    with pytest.raises(SystemConfigError):
        config.model_validate(config.model_dump())
    config.ingestion.max_queue_size = 64
    config.orchestration.max_concurrent_queries = 0
    with pytest.raises(SystemConfigError):
        config.model_validate(config.model_dump())


# -- C5：有界入库队列 -----------------------------------------------------------------

def test_c5_queue_full_rejects_with_43003(tmp_path):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.ingestion.max_queue_size = 1
    jobs = JobStore(persist_file=str(tmp_path / "jobs.json"))
    pipeline = IngestionPipeline(config=config, embedding=StubEmbedding(dimension=128),
                                 vector_store=None, jobs=jobs, documents=None,
                                 start_workers=False)      # 测试：停 worker 以便制造队列满
    blocker = jobs.create("blocker.md")
    pipeline._queue.put((b"x", "blocker.md", blocker, "default", None))   # 占满队列
    pending = jobs.create("pending.md")
    ok = pipeline.submit(b"y", "pending.md", pending)
    assert ok is False, "队列满时 submit 必须拒绝"
    assert pending.status == "failed" and pending.error_code == "43003"
    assert pending.error_message == "入库队列已满，请稍后重试"
    # 队列空出后恢复
    pipeline._queue.get_nowait()
    pipeline._queue.task_done()
    ok2 = pipeline.submit(b"z", "ok.md", pending)
    assert ok2 is True


# -- C6：Job 重启标记中断 --------------------------------------------------------------

def test_c6_restart_marks_pending_jobs_interrupted(tmp_path):
    path = tmp_path / "jobs.json"
    store = JobStore(persist_file=str(path))
    running = store.create("a.md")
    store.update(running.job_id, status="running")
    done = store.create("b.md")
    store.update(done.job_id, status="success")
    # 模拟重启：新实例加载同一文件
    restarted = JobStore(persist_file=str(path))
    interrupted = restarted.get(running.job_id)
    assert interrupted is not None
    assert interrupted.status == "failed"
    assert interrupted.error_code == "50003"
    assert interrupted.error_message == "服务重启，任务中断"
    assert restarted.get(done.job_id) is None, "终态任务不入盘，重启后不存在"
