"""健康检查 down 分支单测（6.7：组件异常 → down 而非崩溃）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import load_config


class _BrokenEmbedding:
    def ready(self):
        raise RuntimeError("模型损坏")


class _BrokenStore:
    def count(self):
        raise RuntimeError("存储不可达")


def test_health_down_when_component_fails(tmp_path, isolated_stores):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(config=config, components=True, prewarm=False,
                     embedding=_BrokenEmbedding(), vector_store=_BrokenStore(),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["code"] == 0
    checks = body["data"]["checks"]
    assert checks["embedding"] == "down"
    assert checks["vector_store"] == "down"
    assert body["data"]["status"] == "down"


def test_health_failed_when_embedding_load_state_failed(tmp_path, isolated_stores):
    """模型预热失败必须显示 failed + 原因，不能永久伪装成“启动中”。"""

    class _FailedEmbedding:
        load_state = "failed"
        load_error = "模型加载超时（>120秒）"

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(config=config, components=True, prewarm=False,
                     embedding=_FailedEmbedding(), vector_store=_OkStore(),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["data"]["checks"]["embedding"] == "failed"
    assert body["data"]["errors"]["embedding"].startswith("模型加载超时")
    assert body["data"]["status"] == "down"


def test_health_not_loaded_when_embedding_unready(tmp_path, isolated_stores):
    """embedding.ready() 返回 False（如加载失败未抛异常）→ not_loaded + degraded。"""

    class _Unready:
        def ready(self):
            return False

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    app = create_app(config=config, components=True, prewarm=False, embedding=_Unready(),
                     kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                     documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["data"]["checks"]["embedding"] == "not_loaded"
    assert body["data"]["status"] == "degraded"


class _ReadyEmbedding:
    is_loaded = True


class _OkStore:
    def count(self):
        return 0


class _ProbeLLM:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls = 0

    def probe(self):
        self.calls += 1
        return self.ok, "" if self.ok else "401 Authentication Fails"

    def chat(self, *_a, **_k):
        return "ok"


def _probe_app(tmp_path, isolated_stores, llm):
    from app.api.health import _reset_probe_cache

    _reset_probe_cache()
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    return create_app(config=config, components=True, prewarm=False,
                      embedding=_ReadyEmbedding(), vector_store=_OkStore(),
                      llm=llm, kbs=isolated_stores["kbs"],
                      users=isolated_stores["users"],
                      documents=isolated_stores["documents"],
                      jobs=isolated_stores["jobs"])


def test_health_probe_marks_invalid_llm_key_down(tmp_path, isolated_stores):
    """6.7：设置页 probe=1 必须反映鉴权失败，不能只凭“已装配”亮绿灯。"""
    llm = _ProbeLLM(ok=False)
    app = _probe_app(tmp_path, isolated_stores, llm)
    with TestClient(app) as client:
        body = client.get("/api/health?probe=1").json()
    checks = body["data"]["checks"]
    assert checks["llm"] == "down"
    assert body["data"]["status"] == "down"
    assert body["data"]["features"]["llm_probe"] is True
    assert body["data"]["features"]["web_enabled"] is False


def test_health_default_mode_does_not_call_llm(tmp_path, isolated_stores):
    llm = _ProbeLLM(ok=False)
    app = _probe_app(tmp_path, isolated_stores, llm)
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["data"]["checks"]["llm"] == "ok"
    assert body["data"]["features"]["llm_probe"] is False
    assert llm.calls == 0


def test_health_probe_result_is_cached(tmp_path, isolated_stores):
    llm = _ProbeLLM(ok=True)
    app = _probe_app(tmp_path, isolated_stores, llm)
    with TestClient(app) as client:
        client.get("/api/health?probe=1")
        client.get("/api/health?probe=1")
    assert llm.calls == 1
