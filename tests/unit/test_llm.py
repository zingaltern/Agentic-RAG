"""DeepSeek LLM 单测（DEVELOPMENT.md 3.8、5.7）：respx mock OpenAI 兼容接口。"""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.adapters.llm.deepseek_llm import DeepSeekLLM
from app.core.errors import LLMUnavailableError

BASE_URL = "https://api.deepseek.com"
CHAT_URL = f"{BASE_URL}/chat/completions"


def _chat_response(content: str, model: str = "deepseek-chat") -> dict:
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
    }


def _request_json(call) -> dict:
    """httpx.Request 无 .json()，用 json.loads(request.content)。"""
    return json.loads(call.request.content.decode("utf-8"))


def test_chat_success(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=Response(200, json=_chat_response("年假需提前 3 天申请[1]。")))
    llm = DeepSeekLLM(api_key="sk-test", model="deepseek-chat", base_url=BASE_URL, timeout_seconds=5)
    out = llm.chat([{"role": "user", "content": "怎么申请年假？"}], temperature=0.1, max_tokens=512)
    assert "年假" in out
    request = respx_mock.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test"
    body = _request_json(respx_mock.calls.last)
    assert body["model"] == "deepseek-chat"
    assert body["messages"] == [{"role": "user", "content": "怎么申请年假？"}]


def test_chat_uses_configured_model(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=Response(200, json=_chat_response("ok", model="deepseek-chat")))
    llm = DeepSeekLLM(api_key="sk-test", base_url=BASE_URL, timeout_seconds=5)
    llm.chat([{"role": "user", "content": "hi"}])
    assert _request_json(respx_mock.calls.last)["model"] == "deepseek-chat"


def test_chat_missing_api_key():
    """api_key 缺失 → LLMUnavailableError（清晰消息，30001）。"""
    llm = DeepSeekLLM(api_key=None, base_url=BASE_URL)
    with pytest.raises(LLMUnavailableError, match="RAG_DEEPSEEK_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_chat_http_error_raises_30001(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=Response(401, json={"error": "unauthorized"}))
    llm = DeepSeekLLM(api_key="sk-bad", base_url=BASE_URL, timeout_seconds=5)
    with pytest.raises(LLMUnavailableError) as ei:
        llm.chat([{"role": "user", "content": "hi"}])
    assert ei.value.code == 30001


def test_chat_empty_content_raises(respx_mock):
    respx_mock.post(CHAT_URL).mock(return_value=Response(200, json={
        "id": "x", "object": "chat.completion", "model": "deepseek-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": None}, "finish_reason": "stop"}],
    }))
    llm = DeepSeekLLM(api_key="sk-test", base_url=BASE_URL, timeout_seconds=5)
    with pytest.raises(LLMUnavailableError):
        llm.chat([{"role": "user", "content": "hi"}])
