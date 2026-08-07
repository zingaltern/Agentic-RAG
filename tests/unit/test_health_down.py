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


def test_health_down_when_component_fails(tmp_path):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    app = create_app(config=config, components=True,
                     embedding=_BrokenEmbedding(), vector_store=_BrokenStore())
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["code"] == 0
    checks = body["data"]["checks"]
    assert checks["embedding"] == "down"
    assert checks["vector_store"] == "down"
    assert body["data"]["status"] == "down"


def test_health_not_loaded_when_embedding_unready(tmp_path):
    """embedding.ready() 返回 False（如加载失败未抛异常）→ not_loaded + degraded。"""

    class _Unready:
        def ready(self):
            return False

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    app = create_app(config=config, components=True, embedding=_Unready())
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["data"]["checks"]["embedding"] == "not_loaded"
    assert body["data"]["status"] == "degraded"
