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
    assert body["data"]["features"] == {
        "agent_enabled": False, "web_enabled": False, "llm_probe": False,
    }
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
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0
    # 服务端分页附带统计（顶部指标/分页总数都来自这里）
    assert body["data"]["stats"] == {"chunks": 0, "latest": 0}


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


# -- S3/S4：CORS 收紧与安全响应头（V5.1） --------------------------------------------------

def test_s3_cors_methods_and_headers_tightened(client):
    resp = client.options(
        "/api/query",
        headers={"Origin": "http://localhost:5173",
                 "Access-Control-Request-Method": "PATCH",
                 "Access-Control-Request-Headers": "X-Custom-Header"},
    )
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    allow_headers = resp.headers.get("access-control-allow-headers", "")
    assert "PATCH" not in allow_methods, "非白名单方法必须被 CORS 拒绝"
    assert "GET" in allow_methods and "POST" in allow_methods
    assert "X-Custom-Header" not in allow_headers
    assert "Content-Type" in allow_headers and "Authorization" in allow_headers


def test_s4_security_response_headers_present(client):
    resp = client.get("/api/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "unsafe-eval" not in csp, "CSP 不允许 unsafe-eval"


def test_spa_html_not_cached(client):
    """history 路由回退的 index.html 必须 no-store。

    否则 /documents 等子路由被浏览器缓存，重新构建后仍引用已删除的旧 chunk，
    直接刷新会出现整页空白（生产上已实际发生）。
    """
    for path in ("/", "/documents", "/kbs", "/settings"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers.get("cache-control") == "no-store", path
