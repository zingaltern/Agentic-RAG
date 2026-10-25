"""V5.2 增量/全量更新与一致性巡检单测（DEVELOPMENT.md 5.10/6.2）。

覆盖：
- DocumentsStore：by_filename / replace（版本递增 + 旧幂等映射清理）/ 幂等映射巡检与清理；
- API upsert：同文件名新版本替换（单一逻辑文档、新块生效、旧向量清空、Job 记录 replaced）；
- API force：同内容强制重入库（跳过幂等短路，版本递增）；
- doctor CLI：幽灵文档 / 孤儿向量的检测与 --fix 修复。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config
from app.core.domain import Chunk, Document
from app.core.registry import wire
from app.services.documents_store import DocumentsStore
from tests.unit.fakes import StubEmbedding

KB = "default"


def _deps(tmp_path, isolated_stores):
    config = load_config()
    config.api.auth_enabled = False
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    return wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])


@pytest.fixture()
def client(tmp_path, isolated_stores) -> TestClient:
    config = load_config()
    config.api.auth_enabled = False
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(config=config, components=True, prewarm=False,
                     embedding=StubEmbedding(dimension=512),
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


def _upload(client: TestClient, name: str, content: bytes, **form) -> dict:
    resp = client.post("/api/documents/upload",
                       files={"file": (name, content, "text/markdown")},
                       data=form or {"mode": "add"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- DocumentsStore（版本与替换） ------------------------------------------------------

def _doc(doc_id: str, filename: str, text: str, kb: str = KB) -> Document:
    return Document(document_id=doc_id, filename=filename, title=filename.split(".")[0],
                    content_hash=f"h-{doc_id}", text=text,
                    metadata={"source": filename, "kind": "md", "kb_id": kb})


def test_store_by_filename_and_replace_version(tmp_path):
    store = DocumentsStore(persist_file=str(tmp_path / "documents.json"))
    store.register(_doc("d1", "手册.md", "v1"), chunk_count=2)
    assert store.by_filename(KB, "手册.md").document_id == "d1"
    assert store.by_filename(KB, "其他.md") is None

    entry = store.replace("d1", _doc("d2", "手册.md", "v2"), chunk_count=3, archive_path=None)
    assert entry.version == 2, "替换后版本递增"
    assert store.get("d1") is None
    assert store.by_filename(KB, "手册.md").document_id == "d2"
    assert store.by_hash("h-d2", KB) == "d2", "新内容幂等映射已写入"
    assert store.by_hash("h-d1", KB) is None, "旧内容幂等映射已清理"


def test_store_hash_map_audit_and_prune(tmp_path):
    path = tmp_path / "documents.json"
    entry = asdict(_doc("d1", "手册.md", "v1"))
    payload = {"docs": [entry], "by_hash": [[KB, "h-dead", "missing-id"]]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    store = DocumentsStore(persist_file=str(path))
    assert store.audit_hash_map() == [(KB, "h-dead", "missing-id")]
    assert store.prune_hash_map() == 1
    assert store.audit_hash_map() == []
    assert store.get("d1") is not None, "清理幂等映射不得误删文档条目"


# -- API upsert / force --------------------------------------------------------------

def test_api_upsert_replaces_same_filename(client):
    v1 = "# 员工手册\n年假为五天。".encode("utf-8")
    v2 = "# 员工手册\n年假为十天（新规生效）。".encode("utf-8")

    first = _upload(client, "员工手册.md", v1, mode="add")
    job1 = _wait_job(client, first["data"]["job_id"])
    doc1 = job1["data"]["document_id"]
    assert job1["data"]["status"] == "success"

    second = _upload(client, "员工手册.md", v2, mode="upsert")
    assert second["data"]["replaced_document_id"] == doc1, "响应需回传被替换的旧版本"
    job2 = _wait_job(client, second["data"]["job_id"])
    assert job2["data"]["status"] == "success"
    assert job2["data"]["replaced_document_id"] == doc1
    doc2 = job2["data"]["document_id"]
    assert doc2 != doc1, "新版本使用新 document_id（先写后删）"

    # 单一逻辑文档 + 版本递增
    items = client.get("/api/documents", params={"kb_id": KB}).json()["data"]["items"]
    same = [i for i in items if i["filename"] == "员工手册.md"]
    assert len(same) == 1 and same[0]["document_id"] == doc2
    assert same[0]["version"] == 2

    # 旧向量清空、新块生效
    deps = client.app.state.deps
    chunks = deps.vector_store.get_all_chunks("kb")
    assert not [c for c in chunks if c.document_id == doc1], "旧版本向量必须被清理"
    texts = "".join(c.text for c in chunks if c.document_id == doc2)
    assert "十天" in texts and "五天" not in texts
    assert client.get(f"/api/documents/{doc1}").json()["code"] == 40401


def test_api_force_reingest_same_content(client):
    raw = "# 制度\n报销需发票。".encode("utf-8")
    first = _upload(client, "制度.md", raw, mode="add")
    doc1 = _wait_job(client, first["data"]["job_id"])["data"]["document_id"]

    dup = _upload(client, "制度.md", raw, mode="add")
    assert dup["data"].get("duplicate") is True, "默认幂等短路"

    forced = _upload(client, "制度.md", raw, mode="add", force="true")
    assert forced["data"]["replaced_document_id"] == doc1
    job = _wait_job(client, forced["data"]["job_id"])
    assert job["data"]["status"] == "success"
    items = client.get("/api/documents", params={"kb_id": KB}).json()["data"]["items"]
    same = [i for i in items if i["filename"] == "制度.md"]
    assert len(same) == 1 and same[0]["version"] == 2


def test_api_upload_mode_invalid(client):
    body = _upload(client, "x.md", b"# x\nhello world.", mode="replace")
    assert body["code"] == 40001


def test_api_add_mode_keeps_two_versions_side_by_side(client):
    a = _upload(client, "重名.md", b"# v1\nfirst content", mode="add")
    b = _upload(client, "重名.md", b"# v2\nsecond content", mode="add")
    _wait_job(client, a["data"]["job_id"])
    _wait_job(client, b["data"]["job_id"])
    items = client.get("/api/documents", params={"kb_id": KB}).json()["data"]["items"]
    assert len([i for i in items if i["filename"] == "重名.md"]) == 2, "add 模式保持旧行为（不替换）"


# -- doctor CLI ----------------------------------------------------------------------

def test_doctor_detects_and_fixes(tmp_path, isolated_stores, capsys):
    from app.cli.doctor import run_doctor

    deps = _deps(tmp_path, isolated_stores)
    good = deps.pipeline.ingest(b"# good\nok content", "good.md")
    assert good.status == "success"

    # 幽灵条目：注册但无任何向量
    deps.documents.register(_doc("ghost0000000", "ghost.md", "ghost"), chunk_count=0)
    # 孤儿向量：集合中有块但索引无条目
    orphan = Chunk(chunk_id="orphan1234567890", document_id="orphan_doc_id",
                   text="orphan text", order=0, parent_id=None,
                   metadata={"source": "orphan.md", "kb_id": KB})
    deps.vector_store.add_chunks([(orphan, [0.0] * 512)])

    report = run_doctor(deps=deps)
    assert report["ok"] is False
    assert any(g["document_id"] == "ghost0000000" for g in report["ghost_entries"])
    assert any(o["document_id"] == "orphan_doc_id" for o in report["orphan_chunks"])
    assert deps.documents.get("ghost0000000") is not None, "默认只读"

    fixed = run_doctor(fix=True, deps=deps)
    assert fixed["fixed"]["ghost_entries"] == 1
    assert fixed["fixed"]["orphan_chunks"] == 1
    assert deps.documents.get("ghost0000000") is None
    assert not [c for c in deps.vector_store.get_all_chunks("kb")
                if c.document_id == "orphan_doc_id"]
    assert deps.vector_store.get_all_chunks("kb"), "正常文档不受影响"
    capsys.readouterr()

    again = run_doctor(deps=deps)
    assert again["ok"] is True, "修复后复检应通过"
