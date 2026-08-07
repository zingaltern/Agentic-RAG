"""UI 骨架与 API 冒烟（第 7 章、2.2 统一响应包）。"""
from __future__ import annotations


def test_ui_returns_200(client):
    resp = client.get("/ui")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "问答" in resp.text          # 五区块之一存在
    assert "app.js" in resp.text


def test_ui_assets(client):
    assert client.get("/ui/style.css").status_code == 200
    assert client.get("/ui/app.js").status_code == 200


def test_health_envelope(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 0
    assert set(body["data"]["checks"]) == {"embedding", "vector_store", "llm"}
    assert body["data"]["status"] == "degraded"   # W1 组件未装配
    assert body["data"]["version"] == "1.0.0"


def test_plugins_status_envelope(client):
    body = client.get("/api/plugins/status").json()
    assert body["code"] == 0
    modules = body["data"]["modules"]
    assert set(modules) == {"embedding", "vector_store", "retriever", "reranker",
                            "evaluator", "rewriter", "detector", "llm"}
    assert modules["embedding"] == "local_bge_v15"
    assert modules["llm"] == "deepseek"


def test_documents_list_empty(client):
    body = client.get("/api/documents").json()
    assert body["code"] == 0
    assert body["data"] == {"items": [], "total": 0}


def test_documents_not_found_error_code(client):
    body = client.get("/api/documents/abc123").json()
    assert body["code"] == 40401
    body = client.delete("/api/documents/abc123").json()
    assert body["code"] == 40401


def test_jobs_not_found_error_code(client):
    body = client.get("/api/jobs/ab12cd34").json()
    assert body["code"] == 40401


def test_query_stub_error_code(client):
    body = client.post("/api/query", json={"query": "如何申请年假？"}).json()
    assert body["code"] == 50001


def test_upload_stub_error_code(client):
    body = client.post("/api/documents/upload", files={"file": ("a.txt", b"hi", "text/plain")}).json()
    assert body["code"] == 50001


def test_trace_id_header_present(client):
    resp = client.get("/api/health", headers={"X-Trace-Id": "trace-abc-123"})
    assert resp.headers.get("X-Trace-Id") == "trace-abc-123"
    resp2 = client.get("/api/health")
    assert resp2.headers.get("X-Trace-Id"), "未传 X-Trace-Id 时也应生成并回传"
