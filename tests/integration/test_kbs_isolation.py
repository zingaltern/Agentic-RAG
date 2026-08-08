"""多知识库隔离集成测试（DEVELOPMENT.md 13.5 D2 三场景）。

A/B 两库入库语义相同文档 → A 库提问不得返回 B 库 sources；
跨库操作（查/传不存在的库）返回参数错误；default 删除被拒。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.services.kb_store import KbStore
from tests.unit.fakes import StubEmbedding

pytestmark = pytest.mark.integration

DOC_A = "# 年假制度\n年假需提前三个工作日通过 OA 申请，入职满一年年假五天。\n"
DOC_B = "# 年假制度\n年假需提前三个工作日通过 OA 申请，入职满一年年假五天。\n"  # 语义相同（同文）


class StubLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据知识库，年假需提前三个工作日申请[1]。"


@pytest.fixture(scope="module")
def iso_client(tmp_path_factory):
    from app.adapters.evaluator.rule_evaluator import RuleEvaluator
    from app.adapters.reranker.identity import IdentityReranker

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path_factory.mktemp("chroma"))
    app = create_app(config=config, components=True, llm=StubLLM(),
                     embedding=StubEmbedding(dimension=128),
                     reranker=IdentityReranker(rerank_top_k=5),
                     evaluator=RuleEvaluator(rule_threshold=0.45, top_n=5),
                     kbs=KbStore(persist_file=str(tmp_path_factory.mktemp("kb") / "kb.json")))
    with TestClient(app) as client:
        yield client


def _wait_job(client, job_id: str, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["data"]["status"] in ("success", "failed"):
            return body
        time.sleep(0.05)
    return body


def test_default_delete_rejected(iso_client):
    body = iso_client.delete("/api/kbs/default").json()
    assert body["code"] == 40001, "default 删除必须被拒（D2-3）"


def test_unknown_kb_operations_rejected(iso_client):
    # 跨库操作：查询/上传到不存在的库 → 参数错误（D2-2）
    q = iso_client.post("/api/query", json={"query": "年假", "kb_id": "ghost"}).json()
    assert q["code"] == 40001
    up = iso_client.post("/api/documents/upload",
                         files={"file": ("a.md", b"x", "text/markdown")},
                         data={"kb_id": "ghost"}).json()
    assert up["code"] == 40001


def test_a_b_isolation(iso_client):
    client = iso_client
    # 建 A/B 两库
    for name in ("kb_a", "kb_b"):
        assert client.post("/api/kbs", json={"name": name}).json()["code"] == 0
    # 两库入库语义相同的文档（同文不同名）
    for kb in ("kb_a", "kb_b"):
        resp = client.post(
            "/api/documents/upload",
            files={"file": ("年假制度.md", DOC_A.encode("utf-8"), "text/markdown")},
            data={"kb_id": kb},
        ).json()
        assert resp["code"] == 0
        job = _wait_job(client, resp["data"]["job_id"])
        assert job["data"]["status"] == "success", f"{kb} 入库失败"
    assert client.get("/api/documents").json()["data"]["total"] >= 2

    # A 库提问 → sources 只含 A 库文档（不含 B 库同名文档）（D2-1）
    qa = client.post("/api/query", json={"query": "年假需提前几天申请？", "kb_id": "kb_a"}).json()
    assert qa["code"] == 0
    sources_a = [s["filename"] for s in qa["data"]["sources"]]
    assert sources_a, "A 库必须返回来源"
    # 通过 metadata.kb_id + collection 断言库内隔离（BM25/向量双路都在 collection 作用域内）
    store = client.app.state.deps.vector_store
    chunks_a = store.get_all_chunks("kb_a")
    assert chunks_a and all(c.metadata.get("kb_id") == "kb_a" for c in chunks_a)
    chunks_b = store.get_all_chunks("kb_b")
    assert chunks_b and all(c.metadata.get("kb_id") == "kb_b" for c in chunks_b)
    doc_ids_b = {c.document_id for c in chunks_b}
    for s in qa["data"]["sources"]:
        assert s["document_id"] not in doc_ids_b, "A 库提问不得返回 B 库 sources（D2-1）"
