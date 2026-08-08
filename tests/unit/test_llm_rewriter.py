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
