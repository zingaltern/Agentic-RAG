"""wire() 持久化路径契约（4.2/13.5）：documents/kbs 必须读取配置，不写死 ./data。"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.config import load_config
from app.core.registry import wire


def test_wire_uses_configured_persist_files(tmp_path, monkeypatch):
    """CONF-02 回归：RAG_DOCUMENTS_PERSIST_FILE/RAG_KBS_PERSIST_FILE 必须生效。"""
    # 只验证装配契约：pipeline/orchestration 用无副作用替身，避免加载模型。
    monkeypatch.setattr("app.services.ingestion.IngestionPipeline",
                        lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr("app.services.orchestration.OrchestrationService",
                        lambda **kwargs: SimpleNamespace(**kwargs))

    config = load_config()
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.llm.candidates = []
    config.orchestration.agent.enabled = False

    deps = wire(
        config, components=True,
        embedding=SimpleNamespace(), vector_store=SimpleNamespace(),
        retriever=SimpleNamespace(), llm=SimpleNamespace(),
        reranker=SimpleNamespace(), evaluator=SimpleNamespace(),
        rewriter=SimpleNamespace(), detector=SimpleNamespace(),
    )

    assert deps.documents._persist_file == config.documents.persist_file
    assert deps.kbs._persist_file == config.kbs.persist_file

    # 行为验证：写入落在临时文件，而不是仓库 data/。
    deps.kbs.create("QA-Wire-Paths", scope="private")
    assert (tmp_path / "kb.json").exists()
    assert (tmp_path / "documents.json").exists() is False  # 未写文档前不创建
