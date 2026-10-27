"""阶段级指标单测（P2-7，DEVELOPMENT.md 10.2）。

覆盖：Counter/Gauge/Histogram 输出格式、多线程并发累加、Job 阶段计时、
jobs 旧记录兼容、/api/metrics 端点权限与输出。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.core import metrics
from app.core.config import load_config
from app.core.registry import wire
from tests.unit.fakes import StubEmbedding

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# -- 指标原语 --------------------------------------------------------------------------

def test_counter_labels_and_render():
    metrics.inc_counter("rag_ingest_total", {"result": "success"}, help_text="入库结果计数")
    metrics.inc_counter("rag_ingest_total", {"result": "success"})
    metrics.inc_counter("rag_ingest_total", {"result": "failed", "error_code": "30003"})
    out = metrics.render_exposition()
    assert "# HELP rag_ingest_total 入库结果计数" in out
    assert "# TYPE rag_ingest_total counter" in out
    assert 'rag_ingest_total{result="success"} 2' in out
    assert 'rag_ingest_total{error_code="30003",result="failed"} 1' in out


def test_gauge_last_write_wins():
    metrics.set_gauge("rag_ingest_queue_depth", 3, help_text="队列深度")
    metrics.set_gauge("rag_ingest_queue_depth", 7)
    out = metrics.render_exposition()
    assert "# TYPE rag_ingest_queue_depth gauge" in out
    assert "rag_ingest_queue_depth 7" in out


def test_histogram_buckets_count_sum():
    metrics.observe("rag_ingest_stage_seconds", 0.3, {"stage": "parse"}, help_text="阶段耗时")
    metrics.observe("rag_ingest_stage_seconds", 2.0, {"stage": "parse"})
    out = metrics.render_exposition()
    assert "# TYPE rag_ingest_stage_seconds histogram" in out
    assert 'rag_ingest_stage_seconds_bucket{stage="parse",le="0.5"} 1' in out
    assert 'rag_ingest_stage_seconds_bucket{stage="parse",le="2.5"} 2' in out
    assert 'rag_ingest_stage_seconds_bucket{stage="parse",le="+Inf"} 2' in out
    assert 'rag_ingest_stage_seconds_count{stage="parse"} 2' in out
    assert 'rag_ingest_stage_seconds_sum{stage="parse"} 2.3' in out


def test_counter_thread_safe():
    def hammer():
        for _ in range(500):
            metrics.inc_counter("rag_ingest_total", {"result": "success"})

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = metrics.render_exposition()
    assert 'rag_ingest_total{result="success"} 4000' in out, "8 线程 × 500 次累加必须无丢失"


# -- Job 阶段计时与入库打点 --------------------------------------------------------------

@pytest.fixture()
def deps(tmp_path, isolated_stores):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    return wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])


def test_ingest_success_carries_stage_timings(deps):
    job = deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    assert job.status == "success"
    assert set(job.stage_timings) == {"parse", "clean", "chunk", "embed", "index"}, \
        "五个阶段计时齐全（parse/clean/chunk/embed/index）"
    assert all(isinstance(v, int) and v >= 0 for v in job.stage_timings.values())
    stored = deps.jobs.get(job.job_id)
    assert stored.stage_timings == job.stage_timings, "JobStore 持久化携带阶段计时"


def test_ingest_counts_metrics(deps):
    deps.pipeline.ingest(SAMPLE.read_bytes(), "员工手册.md")
    deps.pipeline.ingest(b"not a pdf", "broken.pdf")     # failed 30003
    out = metrics.render_exposition()
    assert 'rag_ingest_total{result="success"} 1' in out
    assert 'rag_ingest_total{error_code="30003",result="failed"} 1' in out
    assert 'rag_ingest_stage_seconds_count{stage="parse"}' in out


def test_jobs_old_record_without_stage_timings(tmp_path):
    """旧 jobs.json（无 stage_timings 字段）加载兼容。"""
    import json

    from app.services.jobs_store import JobStore

    persist = tmp_path / "jobs.json"
    persist.write_text(json.dumps([{
        "job_id": "old1", "filename": "a.md", "status": "pending",
        "document_id": None, "chunk_count": 0, "error_code": None,
        "kb_id": "default", "created_at": 0.0, "updated_at": 0.0,
    }]), encoding="utf-8")
    store = JobStore(persist_file=str(persist))
    job = store.get("old1")
    assert job is not None and job.stage_timings == {}
    assert job.status == "failed" and job.error_code == "50003"
    assert job.error_message == "服务重启，任务中断"


# -- /api/metrics 端点 ------------------------------------------------------------------

def test_metrics_endpoint_admin_ok(client):
    metrics.inc_counter("rag_query_total", {"result": "ok"}, help_text="问答结果计数")
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    assert "rag_query_total" in resp.text
    assert resp.text.startswith("# HELP")


def test_metrics_endpoint_rejects_non_admin(monkeypatch):
    """非 admin：40101（直接调用端点函数，绕过 HTTP 栈）。"""
    from app.api.metrics import metrics_endpoint

    class _User:
        role = "user"

    class _State:
        user = _User()
        deps = None

    class _Request:
        state = _State()
        headers = {}

    class _Deps:
        config = load_config()

    monkeypatch.setattr("app.api.metrics.get_authorizer", lambda request: type(
        "A", (), {"is_admin": lambda self, user: False})())
    with pytest.raises(Exception) as exc_info:
        metrics_endpoint(_Request(), deps=_Deps())
    assert getattr(exc_info.value, "code", None) == 40101
