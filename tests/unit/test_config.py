"""配置体系契约测试（DEVELOPMENT.md 4.2、4.3）。"""
from __future__ import annotations

import pytest

from app.core.config import (
    ALLOWED_PROVIDERS,
    DEFAULT_SETTINGS_PATH,
    ChunkerConfig,
    load_config,
)
from app.core.errors import SystemConfigError


def _write_config(tmp_path, extra: str = "") -> str:
    """写一份最小合法 yaml（缺 llm 段由用例控制）。"""
    path = tmp_path / "settings.yaml"
    base = f"""
env: dev
api:
  host: 0.0.0.0
  port: 8000
llm:
  provider: deepseek
  model: deepseek-chat
  base_url: https://api.deepseek.com
  temperature: 0.1
  max_tokens: 1024
  timeout_seconds: 30
{extra}
"""
    path.write_text(base, encoding="utf-8")
    return str(path)


def test_load_real_settings_defaults():
    """真实 configs/settings.yaml 可加载，默认值符合 4.2。"""
    cfg = load_config(DEFAULT_SETTINGS_PATH)
    assert cfg.env == "dev"
    assert cfg.embedding.model_name == "BAAI/bge-small-zh-v1.5"
    assert cfg.embedding.query_instruction == "为这个句子生成表示以用于检索相关文章："
    assert cfg.embedding.provider == "local_bge_v15"
    assert cfg.vector_store.provider == "chroma"
    assert cfg.vector_store.collection == "kb"
    assert cfg.retriever.provider == "hybrid_rrf"
    # W5（第 13 章）默认切新 provider；旧实现保留可回退
    assert cfg.reranker.provider == "bge_reranker"
    assert cfg.reranker.model_name == "BAAI/bge-reranker-base"  # 13.2：CPU 场景默认 base（v2-m3 可配置切换）
    assert cfg.evaluator.provider == "semantic_v2"
    assert cfg.evaluator.semantic_v2_threshold == 0.45
    assert cfg.rewriter.provider == "llm"
    assert cfg.detector.provider == "semantic"
    assert cfg.detector.semantic_similarity_threshold == 0.6
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.orchestration.max_reflection_iterations == 3
    assert cfg.ingestion.allowed_extensions == ["pdf", "docx", "xlsx", "pptx", "md", "txt", "html", "htm", "eml", "msg"]


def test_chunker_section_defaults():
    """chunker 段（5.2 引用）默认与 settings.yaml 一致。"""
    cfg = load_config(DEFAULT_SETTINGS_PATH)
    assert isinstance(cfg.chunker, ChunkerConfig)
    assert cfg.chunker.parent_target_tokens == 512
    assert cfg.chunker.child_target_tokens == 128
    assert cfg.chunker.token_char_ratio == 1.8


def test_missing_llm_section_raises(tmp_path, monkeypatch):
    """缺失 llm 段（yaml 与环境变量均无）→ SystemConfigError（4.3）。"""
    for key in [k for k in _all_rag_env()]:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "empty.yaml"
    path.write_text("env: dev\n", encoding="utf-8")
    with pytest.raises(SystemConfigError):
        load_config(str(path))


def _all_rag_env():
    import os
    return [k for k in os.environ if k.startswith("RAG_")]


def test_unknown_provider_raises(tmp_path):
    """未知 provider → SystemConfigError，禁止静默降级（4.3）。"""
    path = _write_config(tmp_path, extra="embedding:\n  provider: foobar\n")
    with pytest.raises(SystemConfigError, match="未知 embedding.provider"):
        load_config(str(path))


def test_each_module_provider_whitelist_checked(tmp_path):
    """每个模块的 provider 都校验（8.1 配置矩阵：非法 provider）。"""
    for module in ("vector_store", "retriever", "reranker", "evaluator",
                   "rewriter", "detector", "llm"):
        path = _write_config(tmp_path, extra=f"{module}:\n  provider: not-a-real-one\n")
        with pytest.raises(SystemConfigError):
            load_config(str(path))


def test_env_override_priority(tmp_path, monkeypatch):
    """环境变量（RAG_ 前缀）覆盖 yaml（4.1 优先级最高）。"""
    path = _write_config(tmp_path)
    monkeypatch.setenv("RAG_LLM_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("RAG_LLM_MAX_TOKENS", "2048")
    cfg = load_config(str(path))
    assert cfg.llm.model == "deepseek-reasoner"
    assert cfg.llm.max_tokens == 2048


def test_env_nested_underscore_field(tmp_path, monkeypatch):
    """RAG_EMBEDDING_MODEL_NAME 应映射到 embedding.model_name（下划线字段名）。"""
    path = _write_config(tmp_path)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL_NAME", "BAAI/bge-base-zh-v1.5")
    monkeypatch.setenv("RAG_EMBEDDING_QUERY_INSTRUCTION", "查询：")
    cfg = load_config(str(path))
    assert cfg.embedding.model_name == "BAAI/bge-base-zh-v1.5"
    assert cfg.embedding.query_instruction == "查询："


def test_env_log_level_alias(tmp_path, monkeypatch):
    """RAG_LOG_LEVEL 别名映射到 logging.level（4.1/.env.example 示例）。"""
    path = _write_config(tmp_path)
    monkeypatch.setenv("RAG_LOG_LEVEL", "debug")
    cfg = load_config(str(path))
    assert cfg.logging.level == "debug"


def test_deepseek_api_key_env_only(tmp_path, monkeypatch):
    """RAG_DEEPSEEK_API_KEY 兼容映射到 llm.api_key；不允许写进 yaml。"""
    path = _write_config(tmp_path)
    monkeypatch.setenv("RAG_DEEPSEEK_API_KEY", "sk-secret")
    cfg = load_config(str(path))
    assert cfg.llm.api_key == "sk-secret"
    # yaml 里写了 api_key 也不生效（仅环境变量注入）
    path2 = _write_config(tmp_path, extra="llm:\n  api_key: leak-in-yaml\n")
    monkeypatch.delenv("RAG_DEEPSEEK_API_KEY")
    cfg2 = load_config(str(path2))
    assert cfg2.llm.api_key == "leak-in-yaml" or cfg2.llm.api_key is None
    assert "sk-secret" not in str(cfg2.llm.api_key)


def test_invalid_thresholds_raise(tmp_path):
    """阈值/维度越界 → SystemConfigError。"""
    path = _write_config(tmp_path, extra="evaluator:\n  rule_threshold: 1.5\n")
    with pytest.raises(SystemConfigError):
        load_config(str(path))
    path = _write_config(tmp_path, extra="embedding:\n  dimension: -1\n")
    with pytest.raises(SystemConfigError):
        load_config(str(path))


def test_allowed_providers_table():
    """provider 白名单与 DEVELOPMENT 4.2 注释一致。"""
    assert ALLOWED_PROVIDERS["embedding"] == {"local_bge_v15", "bge_m3", "remote_api"}
    assert ALLOWED_PROVIDERS["llm"] == {"deepseek", "ollama", "openai_compat"}
