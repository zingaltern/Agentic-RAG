"""插件状态与热切换单测（6.6、5.11、13.11.3）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.adapters.reranker.identity import IdentityReranker
from tests.unit.fakes import StubEmbedding


def _app(tmp_path, auth_enabled: bool = False, isolated_stores=None):
    from app.services.documents_store import DocumentsStore
    from app.services.jobs_store import JobStore
    from app.services.kb_store import KbStore
    from app.services.user_store import UserStore

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.api.auth_enabled = auth_enabled
    if auth_enabled:
        config.auth.admin_user = "admin"
        config.auth.admin_password = "admin123"
        config.auth.secret = "test-secret"
    stores = isolated_stores or {
        "kbs": KbStore(persist_file=str(tmp_path / "kb.json")),
        "users": UserStore(persist_file=str(tmp_path / "users.json")),
        "documents": DocumentsStore(persist_file=str(tmp_path / "documents.json")),
        "jobs": JobStore(persist_file=str(tmp_path / "jobs.json")),
    }
    return create_app(config=config, components=True, prewarm=False, embedding=StubEmbedding(dimension=512),
                      kbs=stores["kbs"], users=stores["users"], documents=stores["documents"],
                      jobs=stores["jobs"])


def test_status_reports_providers(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/plugins/status").json()
        assert body["code"] == 0
        modules = body["data"]["modules"]
        assert modules["reranker"] == "bge_reranker"
        assert modules["llm"] == "deepseek"
        assert len(modules) == 8


def test_switch_to_identity_succeeds(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "reranker", "strategy": "identity"}).json()
        assert body["code"] == 0
        assert body["data"]["switched"] is True
        deps = client.app.state.deps
        assert isinstance(deps.reranker, IdentityReranker)
        assert deps.config.reranker.provider == "identity"


def test_switch_rejects_unknown_module(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "nope", "strategy": "x"}).json()
        assert body["code"] == 40001
        assert "未知模块" in body["message"]


def test_switch_rejects_unimplemented_provider(tmp_path):
    """白名单有 milvus 但未注册实现 → 拒绝且不切换。"""
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "vector_store", "strategy": "milvus"}).json()
        assert body["code"] == 40001
        assert client.app.state.config.vector_store.provider == "chroma"


def test_switch_rolls_back_on_wire_failure(tmp_path, monkeypatch):
    import app.api.plugins as plugins_mod

    def _boom(*a, **k):
        raise RuntimeError("装配失败")

    monkeypatch.setattr(plugins_mod, "wire", _boom)
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "reranker", "strategy": "identity"}).json()
        assert body["code"] == 50001
        assert "回滚" in body["message"]
        assert client.app.state.config.reranker.provider == "bge_reranker"


def test_switch_requires_auth_when_enabled(tmp_path, isolated_stores):
    with TestClient(_app(tmp_path, auth_enabled=True, isolated_stores=isolated_stores)) as client:
        no_key = client.post("/api/plugins/switch",
                             json={"module": "reranker", "strategy": "identity"}).json()
        assert no_key["code"] == 40101
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()
        token = login["data"]["token"]
        with_admin = client.post("/api/plugins/switch",
                                 json={"module": "reranker", "strategy": "identity"},
                                 headers={"Authorization": f"Bearer {token}"}).json()
        assert with_admin["code"] == 0


def test_status_whitelisted_when_auth_enabled(tmp_path, isolated_stores):
    with TestClient(_app(tmp_path, auth_enabled=True, isolated_stores=isolated_stores)) as client:
        body = client.get("/api/plugins/status").json()
        assert body["code"] == 0


# -- C5（V5.1）：switch config 白名单 + pydantic 模型级校验 -------------------------

def test_switch_config_rejects_secret_keys(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "llm", "strategy": "deepseek",
                                 "config": {"api_key": "LEAKED"}}).json()
        assert body["code"] == 40001
        assert "密钥" in body["message"]
        assert client.app.state.config.llm.api_key != "LEAKED"


def test_switch_config_invalid_type_rejected(tmp_path):
    """非法类型经 pydantic 模型重建拒绝（40001），而非逐键 setattr 后装配期裸异常。"""
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "reranker", "strategy": "bge_reranker",
                                 "config": {"rerank_top_k": "not-a-number"}}).json()
        assert body["code"] == 40001
        assert "非法配置项" in body["message"]
        assert client.app.state.config.reranker.rerank_top_k == 5


def test_switch_config_valid_field_applies(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = client.post("/api/plugins/switch",
                           json={"module": "reranker", "strategy": "bge_reranker",
                                 "config": {"rerank_top_k": 3}}).json()
        assert body["code"] == 0
        assert client.app.state.config.reranker.rerank_top_k == 3


# -- C4：热切换复用组件，避免 worker 线程/模型泄漏 ---------------------------------

def test_switch_reranker_reuses_pipeline_and_embedding(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        deps0 = client.app.state.deps
        emb0, pipe0 = deps0.embedding, deps0.pipeline
        r1 = client.post("/api/plugins/switch", json={"module": "reranker", "strategy": "identity"}).json()
        assert r1["code"] == 0
        r2 = client.post("/api/plugins/switch", json={"module": "reranker", "strategy": "identity"}).json()
        assert r2["code"] == 0
        deps2 = client.app.state.deps
        assert deps2.embedding is emb0, "切换 reranker 不得重载 embedding"
        assert deps2.pipeline is pipe0, "切换 reranker 不得重建 pipeline（避免 worker 线程泄漏）"


def test_switch_reranker_no_worker_thread_leak(tmp_path):
    import threading

    with TestClient(_app(tmp_path)) as client:
        before = threading.active_count()
        for _ in range(3):
            r = client.post("/api/plugins/switch",
                            json={"module": "reranker", "strategy": "identity"}).json()
            assert r["code"] == 0
        after = threading.active_count()
        assert after <= before + 1, f"热切换泄漏 worker 线程（{before} → {after}）"


def test_switch_vector_store_rebuilds_retriever_and_pipeline(tmp_path):
    """C4：vector_store 变更必须重建 retriever（否则查询仍走旧库）+ 重建 pipeline 并关闭旧的。"""
    with TestClient(_app(tmp_path)) as client:
        deps0 = client.app.state.deps
        old_retriever, old_pipeline = deps0.retriever, deps0.pipeline
        body = client.post("/api/plugins/switch",
                           json={"module": "vector_store", "strategy": "chroma"}).json()
        assert body["code"] == 0
        deps1 = client.app.state.deps
        assert deps1.retriever is not old_retriever, "vector_store 变更必须重建 retriever，否则查询走旧库"
        assert deps1.pipeline is not old_pipeline, "vector_store 变更必须重建 pipeline"
        assert old_pipeline._closing, "旧 pipeline 必须进入关闭态（拒绝新提交）"
