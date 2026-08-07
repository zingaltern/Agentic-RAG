"""测试公共夹具。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 确保以仓库根为运行根（本地安装 app 包则无需）
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.adapters  # noqa: F401,E402  注册全部适配器（wire 装配需要）


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """单测不受本地 .env 影响：屏蔽 .env 读取与真实 RAG_ 环境变量（配置测试需要确定性）。"""
    import app.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_read_dotenv", lambda path=".env": {})
    for key in [k for k in os.environ if k.startswith("RAG_")]:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(scope="session")
def client() -> TestClient:
    """契约/UI 冒烟用应用（components=False：不加载嵌入模型，健康检查为 not_loaded）。"""
    from app.application import create_app

    app = create_app(components=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def stub_embedding():
    """确定性桩嵌入（同文本→同向量，语义近似→向量接近），避免下载真实模型。"""
    from tests.unit.fakes import StubEmbedding

    return StubEmbedding(dimension=512)
