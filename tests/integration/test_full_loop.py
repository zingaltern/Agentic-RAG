"""全链路集成测试（DEVELOPMENT.md 8.2）：上传 → job 轮询 → 检索命中 → 问答 [1] 引用 → 删除。

- 临时 Chroma（tempdir 持久化）+ 本地 BGE（真实嵌入，依赖本机 HF 缓存，不联网下载）。
- LLM 用 Stub 返回固定答案（带 [1] 引用），不依赖外网/真 key。
- 默认 pytest 不执行（Makefile test 用 -m "not integration"）；单独验证：
    .venv/bin/python -m pytest -m integration
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config

pytestmark = pytest.mark.integration

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"

ANSWER = "根据员工手册，年假需提前 3 个工作日通过 OA 系统申请[1]。"


class StubLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return ANSWER


@pytest.fixture(scope="module")
def integration_client(tmp_path_factory):
    """真实 BGE（本机缓存）+ 临时 Chroma + Stub LLM + identity 重排（不下载 reranker 模型）。"""
    from app.adapters.reranker.identity import IdentityReranker

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path_factory.mktemp("chroma"))
    app = create_app(config=config, components=True, llm=StubLLM(),
                     reranker=IdentityReranker(rerank_top_k=5))
    with TestClient(app) as client:
        yield client


def _wait_job(client, job_id: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    body = {}
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["data"]["status"] in ("success", "failed"):
            return body
        time.sleep(0.2)
    return body


def test_full_loop_upload_retrieve_query_delete(integration_client):
    client = integration_client
    deps = client.app.state.deps

    # 1) 上传（真实解析 + 真实 BGE 嵌入 + Chroma 写入）
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("员工手册.md", SAMPLE.read_bytes(), "text/markdown")},
    )
    assert resp.json()["code"] == 0
    job = _wait_job(client, resp.json()["data"]["job_id"])
    assert job["code"] == 0
    assert job["data"]["status"] == "success", f"入库失败: {job['data'].get('error_code')}"
    assert job["data"]["chunk_count"] >= 1
    doc_id = job["data"]["document_id"]

    # 2) 检索命中正确来源
    hits = deps.retriever.retrieve("年假怎么申请？", k=5)
    assert hits, "检索必须返回候选"
    assert hits[0].chunk.metadata["source"] == "员工手册.md"

    # 3) 问答出 [1] 引用 + 来源
    q = client.post("/api/query", json={"query": "年假怎么申请？", "debug": True})
    body = q.json()
    assert body["code"] == 0
    data = body["data"]
    assert "[1]" in data["answer"]
    assert data["sources"], "必须返回来源"
    assert data["sources"][0]["filename"] == "员工手册.md"
    assert data["citations"]["used"] == ["[1]"]
    assert data["elapsed_ms"] >= 0

    # 4) 删除 → 级联清向量块 → 链路空
    deleted = client.delete(f"/api/documents/{doc_id}").json()
    assert deleted["code"] == 0
    assert deleted["data"]["deleted_chunks"] == job["data"]["chunk_count"]
    assert deps.vector_store.count() == 0
    assert client.get(f"/api/documents/{doc_id}").json()["code"] == 40401
