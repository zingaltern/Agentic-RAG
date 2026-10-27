"""W2 API 单测：上传 → job 轮询 → 列表/详情/删除 → 健康检查（桩嵌入 + 真实 Chroma 临时目录）。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from tests.unit.fakes import StubEmbedding

SAMPLE = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"


@pytest.fixture()
def w2_client(tmp_path, isolated_stores) -> TestClient:
    config = load_config()
    config.api.auth_enabled = False   # 与仓库 settings.yaml 解耦：这些用例按个人模式契约编写
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(config=config, components=True, prewarm=False, embedding=StubEmbedding(dimension=512),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    with TestClient(app) as c:
        yield c


def _wait_job(client: TestClient, job_id: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    body = {}
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["data"]["status"] in ("success", "failed"):
            return body
        time.sleep(0.05)
    return body


def test_upload_poll_success_and_documents_lifecycle(w2_client):
    resp = w2_client.post(
        "/api/documents/upload",
        files={"file": ("员工手册.md", SAMPLE.read_bytes(), "text/markdown")},
    )
    assert resp.json()["code"] == 0
    job = _wait_job(w2_client, resp.json()["data"]["job_id"])
    assert job["code"] == 0
    assert job["data"]["status"] == "success"
    doc_id = job["data"]["document_id"]
    assert job["data"]["chunk_count"] > 0

    docs = w2_client.get("/api/documents").json()
    assert docs["data"]["total"] == 1
    assert docs["data"]["items"][0]["filename"] == "员工手册.md"

    detail = w2_client.get(f"/api/documents/{doc_id}").json()
    assert detail["data"]["chunk_count"] == job["data"]["chunk_count"]

    health = w2_client.get("/api/health").json()
    assert health["data"]["checks"]["embedding"] == "ok"
    assert health["data"]["checks"]["vector_store"] == "ok"

    deleted = w2_client.delete(f"/api/documents/{doc_id}").json()
    assert deleted["code"] == 0
    assert deleted["data"]["deleted_chunks"] == job["data"]["chunk_count"]
    assert w2_client.get("/api/documents").json()["data"]["total"] == 0
    assert w2_client.get(f"/api/documents/{doc_id}").json()["code"] == 40401


def _upload_one(w2_client, name: str, raw: bytes = None) -> dict:
    resp = w2_client.post(
        "/api/documents/upload",
        files={"file": (name, raw if raw is not None else SAMPLE.read_bytes(), "text/markdown")},
    )
    job = _wait_job(w2_client, resp.json()["data"]["job_id"])
    assert job["data"]["status"] == "success"
    return job["data"]


def test_batch_delete_lifecycle(w2_client):
    doc_a = _upload_one(w2_client, "批量A.md")
    doc_b = _upload_one(w2_client, "批量B.md", SAMPLE.read_bytes() + "\n# 附录B\n内容不同。".encode())
    assert w2_client.get("/api/documents").json()["data"]["total"] == 2

    batch = w2_client.post("/api/documents/batch-delete", json={
        "document_ids": [doc_a["document_id"], doc_b["document_id"], "ghost_doc"],
    }).json()
    assert batch["code"] == 0
    assert [d["document_id"] for d in batch["data"]["deleted"]] == [
        doc_a["document_id"], doc_b["document_id"]]
    assert batch["data"]["deleted"][0]["deleted_chunks"] == doc_a["chunk_count"]
    assert batch["data"]["deleted"][1]["deleted_chunks"] == doc_b["chunk_count"]
    assert batch["data"]["failed"] == [{"document_id": "ghost_doc", "reason_code": 40401}]

    assert w2_client.get("/api/documents").json()["data"]["total"] == 0
    assert w2_client.get(f"/api/documents/{doc_a['document_id']}").json()["code"] == 40401
    assert w2_client.get(f"/api/documents/{doc_b['document_id']}").json()["code"] == 40401


def test_batch_delete_empty_and_oversize_rejected(w2_client):
    empty = w2_client.post("/api/documents/batch-delete", json={"document_ids": []}).json()
    assert empty["code"] == 40001
    oversize = w2_client.post("/api/documents/batch-delete", json={
        "document_ids": [f"doc_{i}" for i in range(201)],
    }).json()
    assert oversize["code"] == 40001


def test_upload_duplicate_idempotent(w2_client):
    raw = SAMPLE.read_bytes()
    first = w2_client.post("/api/documents/upload", files={"file": ("员工手册.md", raw, "text/markdown")}).json()
    job = _wait_job(w2_client, first["data"]["job_id"])
    doc_id = job["data"]["document_id"]
    second = w2_client.post("/api/documents/upload", files={"file": ("员工手册副本.md", raw, "text/markdown")}).json()
    assert second["code"] == 0
    assert second["data"]["duplicate"] is True
    assert second["data"]["document_id"] == doc_id
    assert w2_client.get("/api/documents").json()["data"]["total"] == 1, "重复上传不得再入库"


def test_upload_unsupported_extension(w2_client):
    resp = w2_client.post("/api/documents/upload", files={"file": ("evil.xyz", b"data", "application/octet-stream")})
    assert resp.json()["code"] == 40001


def test_upload_too_large(tmp_path, isolated_stores):
    config = load_config()
    config.api.auth_enabled = False   # 与仓库 settings.yaml 解耦：个人模式契约
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.ingestion.max_file_size_mb = 0   # 任何非空文件都超限
    app = create_app(config=config, components=True, prewarm=False, embedding=StubEmbedding(dimension=512),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    with TestClient(app) as c:
        resp = c.post("/api/documents/upload", files={"file": ("big.md", b"x" * 10, "text/markdown")})
        assert resp.json()["code"] == 40001


def test_upload_parse_error_marks_job_failed(w2_client):
    resp = w2_client.post("/api/documents/upload", files={"file": ("坏文件.pdf", b"not a pdf", "application/pdf")})
    job = _wait_job(w2_client, resp.json()["data"]["job_id"])
    assert job["data"]["status"] == "failed"
    assert job["data"]["error_code"] == "30003"
    assert "格式与扩展名不符" in job["data"]["error_message"]


def test_upload_msg_returns_eml_guidance(w2_client):
    """D01：.msg 失败时 Job API 必须返回“改用 .eml”的可行动原因。"""
    ole2 = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64
    resp = w2_client.post("/api/documents/upload",
                          files={"file": ("邮件.msg", ole2, "application/octet-stream")})
    job = _wait_job(w2_client, resp.json()["data"]["job_id"])
    assert job["data"]["status"] == "failed"
    assert job["data"]["error_code"] == "30003"
    assert "另存为" in job["data"]["error_message"]
    assert ".eml" in job["data"]["error_message"]
