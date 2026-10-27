"""P-D 工具单测（DEVELOPMENT.md 17.4）：参数校验、作用域、证据入账。"""
from __future__ import annotations

from app.adapters.tools import (
    AskUserTool, KbFetchTool, KbListDocumentsTool, KbSearchTool, ToolRegistry,
)
from app.core.domain import AgentState, Chunk, Evidence, ScoredChunk, ToolCall, ToolContext, ToolResult


def _chunk(cid: str, text: str, doc: str = "doc1", order: int = 0,
           parent: str | None = None, source: str = "员工手册.md") -> Chunk:
    return Chunk(chunk_id=cid, document_id=doc, text=text, order=order,
                 parent_id=parent, metadata={"source": source, "page": 3,
                                             "section": "年假", "kind": "md"})


class _Retriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append({"query": query, "k": k, "filters": filters,
                           "path": path, "collection": collection})
        return [ScoredChunk(chunk=c, score=0.8, notion="rrf")
                for c in self.chunks[:k]]


class _Reranker:
    def rerank(self, query, chunks):
        return list(reversed(chunks))


class _VectorStore:
    def __init__(self, chunks):
        self.chunks = chunks

    def get_all_chunks(self, collection="kb"):
        return list(self.chunks)


class _Documents:
    def list(self, page=1, size=20, kb_id=None):
        return [{"document_id": "doc1", "filename": "员工手册.md",
                 "chunk_count": 2, "page_count": 5},
                {"document_id": "doc2", "filename": "差旅政策.md",
                 "chunk_count": 1, "page_count": 2}]


def _ctx(**kw):
    base = dict(trace_id="t1", kb_id="default", collection="rag_default",
                filters={"metadata": {"kind": "md"}}, allowed_tools={"kb_search"})
    base.update(kw)
    return ToolContext(**base)


def test_kb_search_returns_evidence_and_passes_scope():
    retriever = _Retriever([_chunk("c1", "年假 5 天"), _chunk("c2", "病假 3 天")])
    tool = KbSearchTool(retriever, _Reranker(), default_k=2, max_k=5)
    result = tool.invoke(_ctx(), tool.args_model(query="年假", k=2))
    assert result.ok and len(result.evidence) == 2
    assert result.evidence[0].source == "员工手册.md"
    assert retriever.calls[0]["collection"] == "rag_default"
    assert retriever.calls[0]["filters"] == {"metadata": {"kind": "md"}}


def test_tool_registry_validates_args_and_unknown_tool():
    registry = ToolRegistry([KbSearchTool(_Retriever([]), None)])
    bad = registry.invoke(ToolCall("c1", "kb_search", {"query": "", "extra": 1}), _ctx())
    assert not bad.ok and bad.error_code == "TOOL_INVALID_ARGS"
    unknown = registry.invoke(ToolCall("c2", "nope", {}), _ctx())
    assert not unknown.ok and unknown.error_code == "TOOL_NOT_FOUND"


def test_tool_registry_denies_tool_outside_allowlist():
    registry = ToolRegistry([AskUserTool()])
    result = registry.invoke(ToolCall("c1", "ask_user", {"question": "请补充范围"}),
                             _ctx(allowed_tools={"kb_search"}))
    assert not result.ok and result.error_code == "TOOL_SCOPE_DENIED"


def test_kb_fetch_parent_and_document_mode():
    child = _chunk("child", "年假 5 天", parent="parent")
    parent = _chunk("parent", "年假条款全文：入职满一年 5 天……")
    store = _VectorStore([child, parent])
    tool = KbFetchTool(store, max_chars=1000)
    result = tool.invoke(_ctx(allowed_tools={"kb_fetch"}),
                         tool.args_model(chunk_id="child"))
    assert result.ok and "年假条款全文" in result.evidence[0].text
    doc = tool.invoke(_ctx(allowed_tools={"kb_fetch"}),
                      tool.args_model(document_id="doc1"))
    assert doc.ok and doc.evidence[0].document_id == "doc1"
    # 模型把文件名填进 document_id（P6 实测“未找到文档：差旅政策.md”）→ 按 source 兜底
    by_name = tool.invoke(_ctx(allowed_tools={"kb_fetch"}),
                          tool.args_model(document_id="员工手册.md"))
    assert by_name.ok and by_name.evidence[0].document_id == "doc1"
    assert by_name.data["document_id"] == "doc1"


def test_kb_fetch_resolves_evidence_id_and_legacy_e_id():
    """模型把 E1/E2 填进 chunk_id 时，服务端按账本反查实际 chunk。"""
    child = _chunk("child", "年假 5 天", parent="parent")
    parent = _chunk("parent", "年假条款全文：入职满一年 5 天……")
    tool = KbFetchTool(_VectorStore([child, parent]), max_chars=1000)
    ctx = _ctx(allowed_tools={"kb_fetch"}, evidence_index={"E1": "child"})
    explicit = tool.invoke(ctx, tool.args_model(evidence_id="E1"))
    assert explicit.ok and "年假条款全文" in explicit.evidence[0].text
    legacy = tool.invoke(ctx, tool.args_model(chunk_id="E1"))
    assert legacy.ok and legacy.evidence[0].chunk_id == "child"
    missing = tool.invoke(ctx, tool.args_model(evidence_id="E9"))
    assert not missing.ok and missing.error_code == "TOOL_NOT_FOUND"


def test_evidence_ledger_backfills_duplicate_id():
    """重复命中时 result.evidence 原对象也要拿到账本 ID，避免空串。"""
    state = AgentState(run_id="r", trace_id="t", query="q", normalized_query="q",
                       mode="agentic")
    first, created = state.add_evidence(
        Evidence(chunk_id="c1", document_id="d1", text="短", source="s"))
    assert created and first.evidence_id == "E1"
    duplicate = Evidence(chunk_id="c1", document_id="d1", text="更长的正文", source="s")
    stored, created2 = state.add_evidence(duplicate)
    assert created2 is False
    assert stored.evidence_id == "E1"
    assert duplicate.evidence_id == "E1"


def test_kb_list_documents_filters_name():
    tool = KbListDocumentsTool(_Documents(), max_items=10)
    result = tool.invoke(_ctx(allowed_tools={"kb_list_documents"}),
                         tool.args_model(name_contains="员工"))
    assert result.ok and result.data["count"] == 1
    assert result.data["documents"][0]["filename"] == "员工手册.md"


def test_ask_user_returns_clarify_signal():
    tool = AskUserTool()
    result = tool.invoke(_ctx(allowed_tools={"ask_user"}),
                         tool.args_model(question="您指哪个制度？", options=["差旅", "报销"]))
    assert result.ok and result.data["clarify"] is True
    assert result.data["options"] == ["差旅", "报销"]


def test_evidence_ledger_assigns_stable_ids_and_promotes_full_text():
    state = AgentState(run_id="r", trace_id="t", query="q", normalized_query="q",
                       mode="agentic")
    first = state.add_evidence(
        Evidence(chunk_id="c1", document_id="d1", text="短", source="s", score=0.5))
    assert first[0].evidence_id == "E1" and first[1] is True
    second = state.add_evidence(
        Evidence(chunk_id="c1", document_id="d1", text="更长的父块正文",
                 source="s", score=0.4))
    assert second[1] is False
    assert state.evidence["E1"].text == "更长的父块正文"


def test_kb_outline_groups_sections():
    from app.adapters.tools import KbOutlineTool

    chunks = [
        _chunk("c1", "年假", order=0),
        _chunk("c2", "年假续", order=1),
        _chunk("c3", "病假", order=2),
    ]
    tool = KbOutlineTool(_VectorStore(chunks), max_sections=10)
    result = tool.invoke(_ctx(allowed_tools={"kb_outline"}),
                         tool.args_model(document_id="doc1"))
    assert result.ok and result.data["sections"][0]["section"] == "年假"


def test_web_search_requires_explicit_scope():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            return [ScoredChunk(chunk=Chunk("w1", "web", "网页内容", 0, None,
                                            {"source": "https://example.com", "title": "网页"}),
                                score=0.9, notion="web")]

    tool = WebSearchTool(_Web(), allowed_domains=["example.com"])
    denied = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=False),
                         tool.args_model(query="年假"))
    assert not denied.ok and denied.error_code == "TOOL_SCOPE_DENIED"
    ok = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                     tool.args_model(query="年假"))
    assert ok.ok and ok.evidence[0].is_web is True


def test_web_search_blocks_private_ip_urls():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            return [ScoredChunk(chunk=Chunk("w1", "web", "内网内容", 0, None,
                                            {"source": "http://127.0.0.1/admin", "title": "内网"}),
                                score=0.9, notion="web")]

    tool = WebSearchTool(_Web(), allowed_domains=["example.com"])
    result = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                         tool.args_model(query="年假"))
    assert result.ok and result.data["count"] == 0


def test_tool_registry_requires_kb_scope():
    registry = ToolRegistry([KbSearchTool(_Retriever([]), None)])
    result = registry.invoke(ToolCall("c1", "kb_search", {"query": "年假"}),
                             _ctx(collection="", allowed_tools={"kb_search"}))
    assert not result.ok and result.error_code == "TOOL_SCOPE_DENIED"


class _TypeErrorRetriever:
    """五参签名但运行期抛 TypeError：旧实现会无作用域重试，必须禁止。"""

    def __init__(self):
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append({"query": query, "k": k, "filters": filters,
                           "path": path, "collection": collection})
        raise TypeError("simulated retriever type error")


def test_kb_search_typeerror_does_not_retry_without_collection():
    retriever = _TypeErrorRetriever()
    tool = KbSearchTool(retriever, None)
    result = tool.invoke(_ctx(), tool.args_model(query="年假"))
    assert not result.ok
    assert result.evidence == []
    assert result.error_code in {"TOOL_SCOPE_DENIED", "TOOL_EXECUTION_ERROR"}
    assert len(retriever.calls) == 1, "TypeError 后不得再次调用 retrieve"
    assert retriever.calls[0]["collection"] == "rag_default"
    assert retriever.calls[0]["collection"] is not None


class _LegacyThreeArgRetriever:
    """旧三参契约：不能默认库检索，必须显式拒绝 kb 作用域。"""

    def __init__(self):
        self.calls = []

    def retrieve(self, query, k, filters=None):
        self.calls.append({"query": query, "k": k, "filters": filters})
        return [ScoredChunk(chunk=_chunk("c1", "默认库内容"), score=0.9, notion="rrf")]


def test_kb_search_legacy_three_arg_retriever_fails_closed():
    retriever = _LegacyThreeArgRetriever()
    tool = KbSearchTool(retriever, None)
    result = tool.invoke(_ctx(), tool.args_model(query="年假"))
    assert not result.ok
    assert result.error_code == "TOOL_SCOPE_DENIED"
    assert result.evidence == []
    assert retriever.calls == [], "不支持作用域的旧桩不得被调用"


def test_kb_search_passes_path_and_collection_at_init_probe():
    retriever = _Retriever([_chunk("c1", "年假 5 天")])
    tool = KbSearchTool(retriever, None)
    result = tool.invoke(_ctx(), tool.args_model(query="年假", path="bm25", k=1))
    assert result.ok and retriever.calls[0]["path"] == "bm25"
    assert retriever.calls[0]["collection"] == "rag_default"


def test_web_search_allowed_domains_exact_boundary():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            urls = [
                "https://example.com/x",
                "https://sub.example.com/x",
                "https://example.com.evil.org/x",
                "https://notexample.com/x",
            ]
            return [ScoredChunk(chunk=Chunk(f"w{i}", "web", "网页内容", i, None,
                                            {"source": u, "title": f"t{i}"}),
                                score=0.9, notion="web")
                    for i, u in enumerate(urls)]

    tool = WebSearchTool(_Web(), allowed_domains=["example.com"])
    result = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                         tool.args_model(query="年假"))
    assert result.ok
    hosts = {e.metadata["url"] for e in result.evidence}
    assert hosts == {"https://example.com/x", "https://sub.example.com/x"}


def test_web_search_rejects_empty_or_invalid_url():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            return [ScoredChunk(chunk=Chunk(f"w{i}", "web", "网页内容", i, None,
                                            {"source": u, "title": f"t{i}"}),
                                score=0.9, notion="web")
                    for i, u in enumerate(["", "not-a-url", "ftp://example.com/x"])]

    tool = WebSearchTool(_Web(), allowed_domains=["example.com"])
    result = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                         tool.args_model(query="年假"))
    assert result.ok and result.data["count"] == 0


def test_web_search_empty_allowlist_is_fail_closed():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            return [ScoredChunk(chunk=Chunk("w1", "web", "网页内容", 0, None,
                                            {"source": "https://example.com/x", "title": "网页"}),
                                score=0.9, notion="web")]

    tool = WebSearchTool(_Web(), allowed_domains=[])
    result = tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                         tool.args_model(query="年假"))
    assert not result.ok and result.error_code == "TOOL_SCOPE_DENIED"


class _ScopeLeakRetriever:
    """三参回退会返回默认库 secret；用于证明 TypeError 后不会泄露默认库。"""

    def __init__(self):
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append(collection)
        if collection is None:
            return [ScoredChunk(chunk=_chunk("secret", "default-secret"), score=1.0, notion="rrf")]
        raise TypeError("collection path unavailable")


def test_kb_search_typeerror_never_leaks_default_collection():
    retriever = _ScopeLeakRetriever()
    tool = KbSearchTool(retriever, None)
    result = tool.invoke(_ctx(), tool.args_model(query="年假"))
    assert not result.ok
    assert all("default-secret" not in e.text for e in result.evidence)
    assert retriever.calls == ["rag_default"]


def test_tool_timeout_isolated_from_fast_tool():
    import time
    from pydantic import BaseModel, Field
    from app.core.interfaces import Tool

    class Args(BaseModel):
        q: str = "x"

    class Slow(Tool):
        name = "slow"
        description = ""
        args_model = Args
        timeout_ms = 60
        max_result_chars = 10

        def invoke(self, ctx, args):
            time.sleep(0.3)
            return ToolResult(call_id="", name="slow", ok=True)

    class Fast(Tool):
        name = "fast"
        description = ""
        args_model = Args
        timeout_ms = 200
        max_result_chars = 10

        def invoke(self, ctx, args):
            return ToolResult(call_id="", name="fast", ok=True, summary="ok")

    registry = ToolRegistry([Slow(), Fast()], max_workers=1)
    ctx = _ctx(allowed_tools={"slow", "fast"})
    for i in range(3):
        assert registry.invoke(ToolCall(str(i), "slow", {}), ctx).error_code == "TOOL_TIMEOUT"
    fast = registry.invoke(ToolCall("fast", "fast", {}), ctx)
    assert fast.ok, fast.error_code


def test_call_timeout_caps_tool_timeout():
    import time
    from pydantic import BaseModel
    from app.core.interfaces import Tool

    class Args(BaseModel):
        q: str = "x"

    class Slow(Tool):
        name = "slow_cap"
        description = ""
        args_model = Args
        timeout_ms = 5000
        max_result_chars = 10

        def invoke(self, ctx, args):
            time.sleep(0.3)
            return ToolResult(call_id="", name="slow_cap", ok=True)

    registry = ToolRegistry([Slow()], call_timeout_seconds=0.05)
    result = registry.invoke(ToolCall("1", "slow_cap", {}),
                             _ctx(allowed_tools={"slow_cap"}))
    assert result.error_code == "TOOL_TIMEOUT"


def test_empty_allowed_tools_denies_and_specs_empty():
    registry = ToolRegistry([AskUserTool()])
    assert registry.specs(set()) == []
    denied = registry.invoke(ToolCall("1", "ask_user", {"question": "q"}),
                             _ctx(allowed_tools=set()))
    assert not denied.ok and denied.error_code == "TOOL_SCOPE_DENIED"
    allowed = registry.invoke(ToolCall("2", "ask_user", {"question": "q"}),
                              _ctx(allowed_tools=None))
    assert allowed.ok


def test_web_evidence_dedupes_same_url_across_queries():
    from app.adapters.tools import WebSearchTool

    class _Web:
        def retrieve(self, query):
            return [ScoredChunk(chunk=Chunk("w", "web", "同一网页", 0, None,
                                            {"source": "https://example.com/a", "title": "A"}),
                                score=0.9, notion="web")]

    tool = WebSearchTool(_Web(), allowed_domains=["example.com"])
    state = AgentState("r", "t", "q", "q", "agentic")
    for query in ("年假", "病假"):
        for e in tool.invoke(_ctx(allowed_tools={"web_search"}, web_search=True),
                             tool.args_model(query=query)).evidence:
            state.add_evidence(e)
    assert list(state.evidence) == ["E1"]
    assert set(state.evidence["E1"].metadata["queries"]) == {"年假", "病假"}


def test_web_private_ip_policy_is_documented_as_literal_only():
    from app.adapters.tools.web_search import is_public_web_url
    assert "字面量" in (is_public_web_url.__doc__ or "")
    assert "DNS" in (is_public_web_url.__doc__ or "")


def test_result_data_is_truncated_and_marked():
    from pydantic import BaseModel
    from app.core.interfaces import Tool

    class Args(BaseModel):
        q: str = "x"

    class HugeData(Tool):
        name = "huge_data"
        description = ""
        args_model = Args
        timeout_ms = 1000
        max_result_chars = 80

        def invoke(self, ctx, args):
            return ToolResult(call_id="", name="huge_data", ok=True,
                              summary="ok", data={"blob": "x" * 10000})

    result = ToolRegistry([HugeData()]).invoke(
        ToolCall("1", "huge_data", {}), _ctx(allowed_tools={"huge_data"}))
    assert result.ok and result.truncated
    assert result.data.get("_truncated") is True
    assert len(result.data.get("preview", "")) <= 80
