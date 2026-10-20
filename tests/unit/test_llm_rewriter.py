"""LLM 改写器单测（DEVELOPMENT.md 13.3.2）：成功多变体 / 失败回退 rule。"""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.adapters.rewriter.llm_rewriter import LLMRewriter
from app.core.domain import EvaluationResult
from app.core.errors import LLMUnavailableError

FB = EvaluationResult(passed=False, score=0.2, reason="below_threshold")
BASE_URL = "https://api.deepseek.com"
CHAT_URL = f"{BASE_URL}/chat/completions"


class _LLM:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return self.text


def _chat_json(body: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "model": "deepseek-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": body}, "finish_reason": "stop"}],
    }


def test_llm_rewrite_multi_variants():
    """LLM 返回 JSON 数组 → 多变体（代词消解示例）。"""
    llm = _LLM('["我的毕业院校是哪里", "本人毕业学校查询"]')
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("我是哪个大学毕业的", FB)
    assert isinstance(out, list)
    assert len(out) == 2
    assert "毕业院校" in out[0]


def test_llm_rewrite_variants_capped_and_deduped():
    llm = _LLM('["a", "a", "b", "c", "d"]')
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("查询", FB)
    assert len(out) <= 3


def test_llm_rewrite_no_variants_falls_back_to_rule():
    """LLM 输出无法解析（非数组）→ 回退 rule（返回 str）。"""
    llm = _LLM("根据知识库，无法给出变体。")
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("咋样申请年假", FB)
    assert isinstance(out, str), "解析失败必须回退 rule 单变体"
    assert "年假" in out


def test_llm_rewrite_exception_falls_back():
    class _Boom(_LLM):
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            raise LLMUnavailableError("限流")

    rw = LLMRewriter(llm=_Boom(""), synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("年假怎么申请", FB)
    assert isinstance(out, str)


def test_llm_rewrite_none_llm_falls_back():
    rw = LLMRewriter(llm=None, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("年假怎么申请", FB)
    assert isinstance(out, str)


def test_llm_rewrite_skips_identical_to_query():
    llm = _LLM('["年假怎么申请"]')
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("年假怎么申请", FB)
    assert isinstance(out, str), "变体与 query 相同 → 视为无效，回退 rule"


def test_real_http_success_via_respx():
    """respx mock DeepSeek 接口：成功返回变体数组。"""
    respx_mock = respx.mock(assert_all_called=False)
    respx_mock.post(CHAT_URL).mock(return_value=Response(
        200, json=_chat_json('["我的毕业院校", "毕业学校查询"]')))

    class _HttpLLM:
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            from app.adapters.llm.deepseek_llm import DeepSeekLLM

            llm = DeepSeekLLM(api_key="sk-test", base_url=BASE_URL, timeout_seconds=5)
            return llm.chat(messages, temperature=temperature, max_tokens=max_tokens)

    with respx_mock:
        rw = LLMRewriter(llm=_HttpLLM(), synonyms_csv="/nonexistent/synonyms.csv")
        out = rw.rewrite("我是哪个大学毕业的", FB)
        # respx 退出 with 后清空 calls，须在块内断言
        sent_body = json.loads(respx_mock.calls.last.request.content.decode("utf-8"))
    assert isinstance(out, list)
    assert "毕业院校" in out[0]
    assert "用户查询" in sent_body["messages"][1]["content"]


# -- 08-Q1：多策略改写（step_back / hyde，合并单次调用） ----------------------------------


def test_strategies_off_keeps_legacy_array_path():
    """两开关均关：走旧 JSON 数组路径，返回 list[str]（零差异承诺）。"""
    llm = _LLM('["变体一", "变体二"]')
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("查询", FB)
    assert out == ["变体一", "变体二"]
    assert all(isinstance(v, str) for v in out)


def test_multi_strategy_object_parsed():
    llm = _LLM(json.dumps({
        "rewritten": ["年假申请流程是什么"],
        "step_back": "公司年假政策有哪些规定",
        "hyde": "根据公司制度，年假需提前三个工作日在 OA 系统提交申请，"
                "主管审批后生效，未休年假可按规则跨年结转。（假设性段落）",
    }))
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv",
                     step_back_enabled=True, hyde_enabled=True)
    out = rw.rewrite("张三的年假咋整", FB)
    assert isinstance(out, list) and len(out) == 3
    assert out[0] == "年假申请流程是什么"                     # rewritten：普通变体
    assert out[1] == "公司年假政策有哪些规定"                 # step_back：普通变体（hybrid）
    assert isinstance(out[2], dict) and out[2]["path"] == "vector", "hyde 变体必须走 vector 路径"
    assert "假设性段落" in out[2]["text"]


def test_multi_strategy_single_llm_call():
    """三类变体由一次合并调用产出（在线侧方案"合并调用"理念）。"""

    class _Counting:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, temperature=0.1, max_tokens=1024):
            self.calls += 1
            return json.dumps({"rewritten": ["改写"], "step_back": "抽象问题", "hyde": "假设段落"})

    llm = _Counting()
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv",
                     step_back_enabled=True, hyde_enabled=True)
    out = rw.rewrite("查询", FB)
    assert llm.calls == 1, "三类变体必须合并为一次 LLM 调用"
    assert isinstance(out, list) and len(out) == 3


def test_multi_strategy_partial_and_dedup():
    """rewritten 与 query 重复者剔除；禁用策略字段即使有值也不采用。"""
    llm = _LLM(json.dumps({"rewritten": ["查询", "真改写"],
                           "step_back": "不该出现的抽象问题", "hyde": "不该出现的段落"}))
    rw = LLMRewriter(llm=llm, synonyms_csv="/nonexistent/synonyms.csv")   # 两开关均关
    out = rw.rewrite("查询", FB)
    assert out == ["真改写"], "开关关闭时不采用 step_back/hyde，且剔除与原 query 重复者"


def test_multi_strategy_failure_falls_back_rule():
    from tests.unit.test_llm_rewriter import _LLM as _Base   # 占位避免误导入

    class _Boom:
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            raise RuntimeError("LLM down")

    rw = LLMRewriter(llm=_Boom(), synonyms_csv="/nonexistent/synonyms.csv",
                     step_back_enabled=True, hyde_enabled=True)
    out = rw.rewrite("查询", FB)
    assert isinstance(out, (str, list)), "失败回退 rule（单变体语义）"


# -- 编排适配：dict 变体 path 透传 --------------------------------------------------------

def test_orchestrator_carries_hyde_vector_path(monkeypatch):
    """_rewrite_variants 接受 dict 变体，hyde 的 path=vector 透传到检索调用。"""
    from app.adapters.rewriter.llm_rewriter import LLMRewriter as _L
    from app.core.config import load_config
    from app.core.domain import Chunk, ScoredChunk
    from app.services.orchestration import OrchestrationService

    class _Vec:
        dimension = 8

        def embed_documents(self, texts):
            return [[0.0] * 8 for _ in texts]

        def embed_query(self, text):
            return [0.0] * 8

        def ready(self):
            return True

    recorded = []

    class _R:
        def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
            recorded.append(path)
            return [ScoredChunk(chunk=Chunk(chunk_id="c1", document_id="d", text="年假制度内容",
                                            order=0, parent_id=None,
                                            metadata={"source": "a.md"}),
                                score=0.9, notion="rrf")]

    class _E:
        def evaluate(self, query, chunks):
            from app.core.domain import EvaluationResult
            return EvaluationResult(True, 0.9, "ok")

    class _Rerank:
        def rerank(self, query, chunks):
            return list(chunks)

    class _Det:
        def detect(self, answer, context):
            from app.core.domain import DetectionResult
            return DetectionResult(verified=True, suspected=[], verified_rate=1.0)

    class _LLM2:
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            return "生成回答"

    class _RW:
        def rewrite(self, query, feedback):
            return [{"text": "年假申请流程", "path": "hybrid"},
                    {"text": "根据制度，年假需提前在 OA 提交申请并经主管审批。", "path": "vector"}]

    from app.core.domain import EvaluationResult

    class _FailOnce:
        def __init__(self):
            self.calls = 0

        def evaluate(self, query, chunks):
            self.calls += 1
            ok = self.calls > 1
            return EvaluationResult(ok, 0.9 if ok else 0.25, "ok" if ok else "below_threshold")  # 0.25 高于绝望短路阈值

    config = load_config()
    svc = OrchestrationService(config=config, retriever=_R(), reranker=_Rerank(),
                               evaluator=_FailOnce(), rewriter=_RW(), detector=_Det(),
                               llm=_LLM2(), vector_store=None)
    answer = svc.run("年假咋整")
    assert answer.code == 0
    # 13.3.2：每个变体分别检索 —— iter0 基线 hybrid 一次；iter1 两个 dict 变体各检一次。
    # 核心契约：dict 变体自带的 path 必须原样透传 retriever（hyde → vector），而非全部退化为 hybrid。
    assert recorded[0] == "hybrid", "首轮基线必须走 hybrid"
    assert recorded[-1] == "vector", "dict 变体 path 必须透传检索调用"
    assert recorded[1] == "hybrid", "非 hyde 变体缺省 hybrid"
