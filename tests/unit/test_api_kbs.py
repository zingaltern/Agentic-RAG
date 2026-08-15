"""/api/kbs CRUD 单测（DEVELOPMENT.md 13.5）：码值 + 跨库报错。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.services.kb_store import KbStore
from tests.unit.fakes import StubEmbedding


@pytest.fixture()
def kb_client(tmp_path, isolated_stores):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    app = create_app(config=config, components=True, prewarm=False,
                     embedding=StubEmbedding(dimension=128),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"])
    with TestClient(app) as c:
        yield c


def test_list_includes_default(kb_client):
    body = kb_client.get("/api/kbs").json()
    assert body["code"] == 0
    kbs = body["data"]["kbs"]
    assert kbs[0]["kb_id"] == "default"
    assert kbs[0]["collection"] == "kb"
    assert body["data"]["total"] >= 1


def test_create_kb(kb_client):
    body = kb_client.post("/api/kbs", json={"name": "HR Policies", "description": "人事制度"}).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["kb_id"] == "hr_policies"
    assert data["collection"] == "hr_policies"
    assert data["description"] == "人事制度"
    # 列表可见 + 物理集合已建
    listing = kb_client.get("/api/kbs").json()["data"]["kbs"]
    assert any(k["kb_id"] == "hr_policies" for k in listing)


def test_create_kb_chinese_name(kb_client):
    body = kb_client.post("/api/kbs", json={"name": "报销制度"}).json()
    assert body["code"] == 0
    kb_id = body["data"]["kb_id"]
    assert kb_id and kb_id != "报销制度"
    assert kb_client.get(f"/api/kbs").json()["data"]["total"] >= 2


def test_create_kb_empty_name_400(kb_client):
    body = kb_client.post("/api/kbs", json={"name": " "}).json()
    assert body["code"] == 40001
    body2 = kb_client.post("/api/kbs", json={}).json()
    assert body2["code"] == 40001


@pytest.mark.integration
def test_delete_kb_cascades(kb_client):
    created = kb_client.post("/api/kbs", json={"name": "docs"}).json()["data"]
    # 往该库入库后删除 → 物理集合级联清空
    raw = "# 差旅政策\n出差补贴每天 100 元。".encode("utf-8")
    up = kb_client.post("/api/documents/upload", files={"file": ("差旅.md", raw, "text/markdown")},
                        data={"kb_id": "docs"}).json()
    assert up["code"] == 0
    deleted = kb_client.delete("/api/kbs/docs").json()
    assert deleted["code"] == 0
    assert deleted["data"]["kb_id"] == "docs"
    assert not any(k["kb_id"] == "docs" for k in kb_client.get("/api/kbs").json()["data"]["kbs"])
    # 映射已删 → 再上传到该库报参数错误
    again = kb_client.post("/api/documents/upload", files={"file": ("差旅.md", raw, "text/markdown")},
                           data={"kb_id": "docs"}).json()
    assert again["code"] == 40001


def test_delete_default_rejected(kb_client):
    body = kb_client.delete("/api/kbs/default").json()
    assert body["code"] == 40001
    assert kb_client.get("/api/kbs").json()["data"]["total"] >= 1


def test_delete_unknown_kb_404(kb_client):
    body = kb_client.delete("/api/kbs/no_such_kb").json()
    assert body["code"] == 40401


def test_upload_to_unknown_kb_400(kb_client):
    body = kb_client.post("/api/documents/upload", files={"file": ("a.md", b"x", "text/markdown")},
                          data={"kb_id": "ghost_kb"}).json()
    assert body["code"] == 40001


def test_query_unknown_kb_400(kb_client):
    body = kb_client.post("/api/query", json={"query": "年假", "kb_id": "ghost_kb"}).json()
    assert body["code"] == 40001
