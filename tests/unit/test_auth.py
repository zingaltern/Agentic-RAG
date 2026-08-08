"""认证与 RBAC 单测（DEVELOPMENT.md 13.11.1 S1、13.11.3）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.services.rbac import Decision, RbacPolicy, get_rbac_policy, set_rbac_policy
from tests.unit.fakes import StubEmbedding


def _app(tmp_path, auth_enabled: bool, api_key: str | None = "sk-test-key"):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.api.auth_enabled = auth_enabled
    config.api.api_key = api_key
    return create_app(config=config, components=False)


def test_auth_disabled_allows_all(tmp_path):
    with TestClient(_app(tmp_path, auth_enabled=False)) as client:
        assert client.get("/api/query").status_code in (200, 405, 422)  # 路由可达（认证不拦截）
        assert client.post("/api/query", json={"query": "年假"}).json()["code"] in (0, 50001)


def test_auth_enabled_rejects_missing_key(tmp_path):
    with TestClient(_app(tmp_path, auth_enabled=True)) as client:
        body = client.post("/api/query", json={"query": "年假"}).json()
        assert body["code"] == 40101
        assert "X-API-Key" in body["message"]


def test_auth_enabled_rejects_wrong_key(tmp_path):
    with TestClient(_app(tmp_path, auth_enabled=True)) as client:
        body = client.post("/api/query", json={"query": "年假"},
                           headers={"X-API-Key": "wrong"}).json()
        assert body["code"] == 40101


def test_auth_enabled_accepts_valid_key(tmp_path):
    with TestClient(_app(tmp_path, auth_enabled=True)) as client:
        body = client.post("/api/query", json={"query": "年假"},
                           headers={"X-API-Key": "sk-test-key"}).json()
        assert body["code"] != 40101, "带正确 key 不得 401"


def test_auth_whitelist_health_and_plugins(tmp_path):
    """health/plugins 白名单放行（探活与 provider 状态不要求凭据）。"""
    with TestClient(_app(tmp_path, auth_enabled=True)) as client:
        assert client.get("/api/health").json()["code"] == 0
        assert client.get("/api/plugins/status").json()["code"] == 0
        assert client.get("/api/kbs").json()["code"] == 40101, "kbs 不在白名单"


def test_auth_enabled_without_configured_key_fails_closed(tmp_path):
    """启用认证但未配置 RAG_API_KEY → 拒绝（防开而不锁）。"""
    with TestClient(_app(tmp_path, auth_enabled=True, api_key=None)) as client:
        body = client.post("/api/query", json={"query": "年假"},
                           headers={"X-API-Key": "anything"}).json()
        assert body["code"] == 40101


def test_rbac_default_allow():
    policy = RbacPolicy()
    assert policy.resolve_user_kb("user_1", "default") == Decision.Allow
    assert policy.resolve_user_kb(None, "any_kb") == Decision.Allow
    assert policy.is_enforced() is False


def test_rbac_user_kb_map():
    policy = RbacPolicy(user_kb_map={"user_1": {"default"}})
    assert policy.resolve_user_kb("user_1", "default") == Decision.Allow
    assert policy.resolve_user_kb("user_1", "docs") == Decision.Deny
    assert policy.resolve_user_kb("user_2", "docs") == Decision.Allow, "未配置的用户默认放行"
    assert policy.is_enforced() is True


def test_rbac_settable_global():
    saved = get_rbac_policy()
    try:
        set_rbac_policy(RbacPolicy(user_kb_map={"u": {"default"}}))
        assert get_rbac_policy().is_enforced() is True
    finally:
        set_rbac_policy(saved)
