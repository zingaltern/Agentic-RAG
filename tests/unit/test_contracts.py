"""接口契约测试（DEVELOPMENT.md 第 3 章、2.2 错误码表、5.11 注册表）。"""
from __future__ import annotations

import inspect

import pytest

from app.core.errors import (
    AuthError,
    ClarifyError,
    ContentModerationError,
    InjectionError,
    EmbeddingError,
    InternalError,
    LLMUnavailableError,
    NotFoundError,
    ParseError,
    RerankError,
    ReflectionLimitError,
    RequestTimeoutError,
    SystemConfigError,
    ValidationError,
)
from app.core.interfaces import (
    Evaluator,
    HallucinationDetector,
    LLM,
    Cleaner,
    Planner,
    QueryRewriter,
    Reflector,
    Reranker,
    Retriever,
    Synthesizer,
    Tool,
    ToolCallingLLM,
    VectorEngine,
    VectorStore,
    Verifier,
)
from app.core.registry import PluginRegistry

ABC_INTERFACES = (VectorEngine, VectorStore, Retriever, Reranker,
                  Evaluator, QueryRewriter, Cleaner, HallucinationDetector, LLM,
                  ToolCallingLLM, Tool, Planner, Reflector, Synthesizer, Verifier)


def test_interfaces_are_abc():
    for iface in ABC_INTERFACES:
        assert inspect.isabstract(iface), f"{iface.__name__} 必须是抽象类"


def _params(method) -> list[str]:
    return [p for p in inspect.signature(method).parameters if p != "self"]


def test_vector_engine_contract():
    assert _params(VectorEngine.embed_documents) == ["texts"]
    assert _params(VectorEngine.embed_query) == ["text"]
    assert isinstance(VectorEngine.dimension, property)


def test_vector_store_contract():
    assert _params(VectorStore.add_chunks) == ["chunks", "collection"]
    assert _params(VectorStore.search) == ["query_vector", "k", "filters", "collection"]
    assert _params(VectorStore.delete_document) == ["document_id", "collection"]
    assert _params(VectorStore.count) == ["collection"]
    assert _params(VectorStore.drop_collection) == ["collection"]


def test_retriever_contract():
    # P-D（17.3）：工具化需要 path/collection；两者有默认值，兼容旧三参调用
    assert _params(Retriever.retrieve) == ["query", "k", "filters", "path", "collection"]


def test_reranker_contract():
    assert _params(Reranker.rerank) == ["query", "chunks"]


def test_evaluator_contract():
    assert _params(Evaluator.evaluate) == ["query", "chunks"]


def test_rewriter_contract():
    assert _params(QueryRewriter.rewrite) == ["query", "feedback"]


def test_detector_contract():
    assert _params(HallucinationDetector.detect) == ["answer", "context"]


def test_cleaner_contract():
    # V5.2：kind 为可选类型参数（按类型豁免），缺省 None 时行为与旧版一致
    assert _params(Cleaner.clean) == ["text", "kind"]


def test_llm_contract():
    params = inspect.signature(LLM.chat).parameters
    assert list(params) == ["self", "messages", "temperature", "max_tokens"]
    assert params["temperature"].default == 0.1
    assert params["max_tokens"].default == 1024


def test_pure_function_signatures():
    """3.9：parse_file / split_chunks 签名固定。"""
    from app.adapters.parser import parse_file
    from app.adapters.chunker import split_chunks

    pf = inspect.signature(parse_file)
    assert list(pf.parameters)[:2] == ["file_stream", "filename"]
    for p in list(pf.parameters.values())[2:]:
        assert p.default is not inspect.Parameter.empty  # 其余为可选配置参数（S2 zip 上限等）
    sc = inspect.signature(split_chunks)
    assert list(sc.parameters)[:1] == ["doc"]
    for p in list(sc.parameters.values())[1:]:
        assert p.default is not inspect.Parameter.empty  # 其余为可选配置参数


def test_error_code_table():
    """2.2 错误码表：编号稳定且唯一。"""
    table = {
        0: "成功",
        40001: ValidationError,
        40002: InjectionError,           # P-C：提示注入拒收
        40003: ContentModerationError,   # P-C：内容审核拒收
        40101: AuthError,
        40401: NotFoundError,
        30001: LLMUnavailableError,
        30002: EmbeddingError,
        30003: ParseError,
        30004: RerankError,
        21001: ReflectionLimitError,
        21002: RequestTimeoutError,
        21003: ClarifyError,        # A2：主动澄清
        50001: InternalError,
    }
    codes = [c for c in table if c != 0]
    assert len(codes) == len(set(codes)), "错误码必须唯一"
    for code, exc in table.items():
        if code == 0:
            continue
        assert exc().code == code, f"{exc.__name__} 错误码应为 {code}"


def test_system_config_error_is_startup_grade():
    assert SystemConfigError.code == 50001


def test_plugin_registry_roundtrip():
    """5.11：register/get 往返（保存/恢复全局注册表，避免污染其他用例）。"""
    class _Dummy:
        def __init__(self, **cfg):
            self.cfg = cfg

    saved = dict(PluginRegistry._registry)
    try:
        PluginRegistry.clear()
        PluginRegistry.register("dummy", _Dummy)
        inst = PluginRegistry.get("dummy", x=1)
        assert isinstance(inst, _Dummy) and inst.cfg == {"x": 1}
        with pytest.raises(KeyError):
            PluginRegistry.get("no_such_plugin")
    finally:
        PluginRegistry._registry.clear()
        PluginRegistry._registry.update(saved)

def test_tool_contract():
    assert _params(Tool.invoke) == ["ctx", "args"]


def test_tool_calling_llm_contract():
    assert _params(ToolCallingLLM.chat_with_tools) == [
        "messages", "tools", "tool_choice", "temperature", "max_tokens"]


def test_planner_contract():
    assert _params(Planner.plan) == ["state", "budget"]
    assert _params(Planner.next_action) == ["state", "budget"]
    assert _params(Planner.replan) == ["state", "reflection"]


def test_reflector_contract():
    assert _params(Reflector.reflect) == ["state", "result"]
    assert _params(Reflector.sufficient) == ["state"]
    assert _params(Reflector.missing) == ["state"]


def test_synthesizer_contract():
    assert _params(Synthesizer.synthesize) == ["state"]
