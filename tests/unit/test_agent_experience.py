"""A 组智能体体验增强单测（A1 记忆压缩 / A2 澄清 / A5 web / A6 熔断）。

注：A4 多库自动路由已于 2026-08-30 移除（docs/dev-tasks/05），相关用例随之删除。
"""
from __future__ import annotations

import io

import pytest

from app.adapters.llm.multi_llm import MultiCandidateLLM
from app.core.errors import LLMUnavailableError
from app.services.orchestration import OrchestrationService, _summarize_history
from app.core.config import load_config
from app.core.domain import Chunk, EvaluationResult, ScoredChunk


# -- A1 记忆压缩 ------------------------------------------------------------------

class _StubLLM:
    def __init__(self, answer="ok"):
        self.answer = answer
        self.calls = []

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.calls.append(messages)
        return self.answer


class _R:
    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        return [_scored("年假申请流程说明")]


class _E:
    def evaluate(self, query, chunks):
        return EvaluationResult(True, 0.9, "ok")


class _Det:
    def detect(self, answer, context):
        from app.core.domain import DetectionResult

        return DetectionResult(verified=True, suspected=[], verified_rate=1.0)


def _scored(text):
    return ScoredChunk(
        chunk=Chunk(chunk_id="c1", document_id="d", text=text, order=0, parent_id=None,
                    metadata={"source": "a.md", "kind": "md", "section": "年假"}),
        score=1.0, notion="rrf")


def _svc(llm, config=None):
    config = config or load_config()
    class _Rerank:
        def rerank(self, query, chunks):
            return list(chunks)

    return OrchestrationService(config=config, retriever=_R(), reranker=_Rerank(),
                                evaluator=_E(), rewriter=None, detector=_Det(), llm=llm,
                                vector_store=None)


def test_a1_summary_helper_returns_summary():
    llm = _StubLLM("摘要：员工询问年假规则，结论需提前三天申请。")
    history = [{"role": "user", "content": f"问题{i}"} for i in range(3)]
    summary = _summarize_history(llm, history)
    assert summary and "年假" in summary
    assert len(llm.calls) == 1


def test_a1_history_compressed_when_over_threshold():
    llm = _StubLLM("（历史摘要）年假相关问答")
    history = [{"role": "user", "content": f"早期问题{i}"} for i in range(10)]
    svc = _svc(llm)
    svc.run("年假怎么申请", history=history)
    sent = llm.calls[-1]
    joined = " ".join(m.get("content", "") for m in sent)
    assert "历史摘要" in joined, "超阈值历史必须被压缩为摘要注入"
    assert "早期问题0" not in joined, "早期轮次不应原样传入"


def test_a1_summary_failure_falls_back_to_truncation():
    class _BoomThenOk(_StubLLM):
        def __init__(self):
            super().__init__("生成回答")
            self.calls = 0

        def chat(self, messages, temperature=0.1, max_tokens=1024):
            self.calls += 1
            if self.calls == 1:      # 首次（摘要）失败
                raise LLMUnavailableError("down")
            return self.answer

    llm = _BoomThenOk()
    history = [{"role": "user", "content": f"早期问题{i}"} for i in range(10)]
    svc = _svc(llm)
    answer = svc.run("年假怎么申请", history=history)
    assert answer.code == 0, "摘要失败回退截断后生成链路不受影响"


# -- A2 澄清 ----------------------------------------------------------------------

def test_a2_clarify_when_low_scores_with_candidates():
    """A2：检索有候选但连续低分 → 21003 主动澄清（13.12.5 模板化：短查询 → no_entity 模板）。"""
    class _LowE(_E):
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.25, "below_threshold")

    llm = _StubLLM("您是指年假还是加班？")
    config = load_config()
    assert config.clarification.use_llm is False, "默认模板化反问（LLM 仅可选增强）"
    class _Rewrite:
        def rewrite(self, query, feedback):
            return query   # 保持短查询（走换路），断言 no_entity 模板

    class _Rerank:
        def rerank(self, query, chunks):
            return list(chunks)

    svc = OrchestrationService(config=config, retriever=_R(), reranker=_Rerank(),
                               evaluator=_LowE(), rewriter=_Rewrite(), detector=_Det(), llm=llm,
                               vector_store=None)
    answer = svc.run("这咋整")
    assert answer.code == 21003
    assert answer.need_clarify is True
    assert answer.text == config.clarification.templates["no_entity"]


def test_a2_clarify_llm_enhance_when_configured():
    """13.12.5：use_llm=true 时先 LLM 生成，失败回退模板。"""
    class _LowE(_E):
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.25, "below_threshold")

    llm = _StubLLM("您是指年假还是加班？")
    config = load_config()
    config.clarification.use_llm = True

    class _Rewrite:
        def rewrite(self, query, feedback):
            return query + " 年假"

    class _Rerank:
        def rerank(self, query, chunks):
            return list(chunks)

    svc = OrchestrationService(config=config, retriever=_R(), reranker=_Rerank(),
                               evaluator=_LowE(), rewriter=_Rewrite(), detector=_Det(), llm=llm,
                               vector_store=None)
    answer = svc.run("这咋整")
    assert answer.code == 21003
    assert answer.text == "您是指年假还是加班？"


# -- A5 web 检索 -------------------------------------------------------------------

def test_a5_web_retriever_parses_results(monkeypatch):
    import json

    from app.adapters.retriever.web_retriever import WebRetriever

    def fake_urlopen(req, timeout=None):
        body = json.dumps({"results": [
            {"title": "年假新规", "url": "https://example.com/a", "content": "2026年起年假10天", "score": 0.9},
            {"title": "无内容", "url": "https://example.com/b", "content": ""},
        ]})
        return io.BytesIO(body.encode("utf-8"))

    monkeypatch.setenv("RAG_TAVILY_API_KEY", "sk-test-tavily")
    monkeypatch.setattr("app.adapters.retriever.web_retriever.urllib.request.urlopen", fake_urlopen)
    hits = WebRetriever().retrieve("年假新政策")
    assert len(hits) == 1
    assert hits[0].notion == "web"
    assert hits[0].chunk.metadata["source_type"] == "web"
    assert "https://example.com/a" in hits[0].chunk.metadata["source"]


def test_a5_web_retriever_no_key_returns_empty(monkeypatch):
    from app.adapters.retriever.web_retriever import WebRetriever

    monkeypatch.delenv("RAG_TAVILY_API_KEY", raising=False)
    assert WebRetriever().retrieve("年假") == []


def test_a5_web_retriever_failure_returns_empty(monkeypatch):
    import urllib.error

    from app.adapters.retriever.web_retriever import WebRetriever

    def boom(req, timeout=None):
        raise urllib.error.URLError("net down")

    monkeypatch.setenv("RAG_TAVILY_API_KEY", "sk-test-tavily")
    monkeypatch.setattr("app.adapters.retriever.web_retriever.urllib.request.urlopen", boom)
    assert WebRetriever().retrieve("年假") == []


# -- A6 熔断 -----------------------------------------------------------------------

class _FailN:
    def __init__(self, fails):
        self.fails = fails
        self.calls = 0

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.calls += 1
        if self.calls <= self.fails:
            raise LLMUnavailableError("fail")
        return "ok"


class _AlwaysFail:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.calls += 1
        raise LLMUnavailableError("always down")


def test_a6_falls_back_to_candidate():
    primary = _FailN(fails=1)
    backup = _StubLLM("backup 回答")
    mllm = MultiCandidateLLM([primary, backup], fail_threshold=3, cooldown_seconds=60)
    out = mllm.chat([{"role": "user", "content": "hi"}])
    assert out == "backup 回答"
    assert primary.calls == 1


def test_a6_circuit_breaks_after_threshold():
    primary = _AlwaysFail()
    backup = _AlwaysFail()
    mllm = MultiCandidateLLM([primary, backup], fail_threshold=2, cooldown_seconds=60)
    with pytest.raises(LLMUnavailableError):
        mllm.chat([{"role": "user", "content": "1"}])
    # 第一次：两候选各失败 1 次；第二次：各失败 2 次触发熔断；第三次：全部熔断立即抛
    with pytest.raises(LLMUnavailableError):
        mllm.chat([{"role": "user", "content": "2"}])
    before = primary.calls
    with pytest.raises(LLMUnavailableError):
        mllm.chat([{"role": "user", "content": "3"}])
    assert primary.calls == before, "熔断期间不得再调用"


def test_a6_recovers_after_cooldown():
    primary = _FailN(fails=2)
    mllm = MultiCandidateLLM([primary], fail_threshold=2, cooldown_seconds=1)
    with pytest.raises(LLMUnavailableError):
        mllm.chat([{"role": "user", "content": "1"}])   # 第 1 次失败
    with pytest.raises(LLMUnavailableError):
        mllm.chat([{"role": "user", "content": "2"}])   # 第 2 次失败 → 触发熔断
    import time

    time.sleep(1.2)
    assert mllm.chat([{"role": "user", "content": "3"}]) == "ok", "冷却后熔断解除可恢复"


def test_a6_empty_candidates_rejected():
    with pytest.raises(ValueError):
        MultiCandidateLLM([])


# -- A5 前端开关 web_search 透传 -------------------------------------------------------------

class _WebRetriever:
    def __init__(self):
        self.called = 0

    def retrieve(self, query):
        self.called += 1
        return [_scored("【网页】示例(https://example.com)\n网页内容")]


def test_a5_web_search_request_flag_triggers(monkeypatch):
    """前端 web_search=true（服务端 web.enabled=false）→ 首轮拉取 web 候选。"""
    wb = _WebRetriever()
    monkeypatch.setattr("app.adapters.retriever.web_retriever.WebRetriever", lambda **kw: wb)
    config = load_config()
    config.web.enabled = False
    llm = _StubLLM()
    svc = _svc(llm, config=config)
    answer = svc.run("年假新政策", web_search=True)
    assert wb.called == 1, "web_search=true 时必须触发联网检索"
    assert answer.code == 0, "联网候选存在时应直接采信进入生成，不再追问意图"


def test_a5_no_web_search_no_fetch(monkeypatch):
    """未开启 web_search 且服务端关 → 不拉取。"""
    wb = _WebRetriever()
    monkeypatch.setattr("app.adapters.retriever.web_retriever.WebRetriever", lambda **kw: wb)
    config = load_config()
    config.web.enabled = False
    svc = _svc(_StubLLM(), config=config)
    svc.run("年假怎么申请")
    assert wb.called == 0, "默认不联网"


def test_a5_web_candidate_bypasses_clarify():
    """A5 修复：web_search=true 且 web 候选存在时，即使评估低分也直接生成（不触发 21003 澄清）。"""
    wb = _WebRetriever()
    monkeypatch_obj = __import__("pytest").MonkeyPatch()
    monkeypatch_obj.setattr("app.adapters.retriever.web_retriever.WebRetriever", lambda **kw: wb)

    class _LowE(_E):
        def evaluate(self, query, chunks):
            return EvaluationResult(False, 0.25, "below_threshold")

    class _Rewrite:
        def rewrite(self, query, feedback):
            return query + " 年假"

    class _Rerank:
        def rerank(self, query, chunks):
            return list(chunks)

    config = load_config()
    config.web.enabled = False
    llm = _StubLLM("根据网页，2026年个税起征点为 X 元[1]。")
    svc = OrchestrationService(config=config, retriever=_R(), reranker=_Rerank(),
                               evaluator=_LowE(), rewriter=_Rewrite(), detector=_Det(),
                               llm=llm, vector_store=None)
    answer = svc.run("2026年个税起征点", web_search=True)
    monkeypatch_obj.undo()
    assert answer.code == 0, "联网请求不得再反问意图"
    assert any(s.get("filename") or s.get("source") for s in answer.sources), "来源应含 web 候选"


# -- A5 冲突优先级（KB 优先于联网） ---------------------------------------------------

def test_a5_kb_priority_ranks_kb_first(monkeypatch):
    """冲突优先级：即使 web 相关度更高，KB 块仍排在最前；web 仅作补充保留。"""
    class _WebStub:
        def __init__(self):
            self.called = 0

        def retrieve(self, query):
            self.called += 1
            return [ScoredChunk(
                chunk=Chunk(chunk_id="web_0", document_id="web", text="网页说法…", order=0,
                            parent_id=None,
                            metadata={"source": "https://x.com", "source_type": "web", "title": "网页标题"}),
                score=1.0, notion="web")]

    wb = _WebStub()
    monkeypatch.setattr("app.adapters.retriever.web_retriever.WebRetriever", lambda **kw: wb)

    class _RerankHighWeb:
        """模拟 web 相关度更高：web 分数高于 KB。"""
        def rerank(self, query, chunks):
            out = []
            for c in chunks:
                is_web = c.chunk.document_id == "web"
                out.append(ScoredChunk(chunk=c.chunk, score=(0.9 if is_web else 0.3), notion=c.notion))
            return sorted(out, key=lambda x: -x.score)

    config = load_config()
    config.web.enabled = False
    llm = _StubLLM("根据内部制度[1]…")
    svc = OrchestrationService(config=config, retriever=_R(), reranker=_RerankHighWeb(),
                               evaluator=_E(), rewriter=None, detector=_Det(), llm=llm,
                               vector_store=None)
    answer = svc.run("年假规定", web_search=True)
    assert answer.code == 0
    assert wb.called == 1, "并行提交仍只调用一次联网检索"
    assert answer.sources[0]["filename"] == "a.md", "KB 块应排在最前（冲突以知识库为准）"
    assert any(s.get("source_type") == "web" for s in answer.sources), "web 仍作为补充保留"


def test_a5_kb_priority_off_allows_web_first(monkeypatch):
    """kb_priority=False 时退回原混排：web 相关度更高可排前（验证开关可关）。"""
    class _WebStub:
        def retrieve(self, query):
            return [ScoredChunk(
                chunk=Chunk(chunk_id="web_0", document_id="web", text="网页说法…", order=0,
                            parent_id=None,
                            metadata={"source": "https://x.com", "source_type": "web", "title": "网页标题"}),
                score=1.0, notion="web")]

    wb = _WebStub()
    monkeypatch.setattr("app.adapters.retriever.web_retriever.WebRetriever", lambda **kw: wb)

    class _RerankHighWeb:
        def rerank(self, query, chunks):
            out = []
            for c in chunks:
                is_web = c.chunk.document_id == "web"
                out.append(ScoredChunk(chunk=c.chunk, score=(0.9 if is_web else 0.3), notion=c.notion))
            return sorted(out, key=lambda x: -x.score)

    config = load_config()
    config.web.enabled = False
    config.web.kb_priority = False
    llm = _StubLLM("根据网页[1]…")
    svc = OrchestrationService(config=config, retriever=_R(), reranker=_RerankHighWeb(),
                               evaluator=_E(), rewriter=None, detector=_Det(), llm=llm,
                               vector_store=None)
    answer = svc.run("年假规定", web_search=True)
    assert answer.code == 0
    assert answer.sources[0].get("source_type") == "web", "关闭优先级后 web 可排前"
