"""DeepSeek 原生工具调用单测（DEVELOPMENT.md 17.3/9.3）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.llm.deepseek_llm import DeepSeekLLM
from app.core.errors import AgentProtocolError


def _client_with(message, finish_reason="tool_calls"):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: response)))


def test_chat_with_tools_parses_tool_calls_and_usage():
    llm = DeepSeekLLM(api_key="test", model="deepseek-chat")
    llm._client = _client_with(SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="kb_search", arguments='{"query":"年假","k":3}'),
        )],
    ))
    resp = llm.chat_with_tools(
        [{"role": "user", "content": "年假几天"}],
        [{"type": "function", "function": {"name": "kb_search", "parameters": {}}}],
    )
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].name == "kb_search"
    assert resp.tool_calls[0].arguments == {"query": "年假", "k": 3}
    assert resp.usage["total_tokens"] == 15


def test_chat_with_tools_returns_content_when_no_calls():
    llm = DeepSeekLLM(api_key="test")
    llm._client = _client_with(SimpleNamespace(content="答案[1]", tool_calls=None), "stop")
    resp = llm.chat_with_tools([{"role": "user", "content": "q"}], [])
    assert resp.content == "答案[1]"
    assert resp.tool_calls == []


def test_chat_with_tools_invalid_json_raises_protocol_error():
    llm = DeepSeekLLM(api_key="test")
    llm._client = _client_with(SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id="call_bad",
            function=SimpleNamespace(name="kb_search", arguments="{not json"),
        )],
    ))
    with pytest.raises(AgentProtocolError):
        llm.chat_with_tools([{"role": "user", "content": "q"}],
                            [{"type": "function", "function": {"name": "kb_search"}}])


def test_chat_with_tools_requires_api_key():
    llm = DeepSeekLLM(api_key=None)
    with pytest.raises(Exception):
        llm.chat_with_tools([], [])


def test_chat_with_tools_classifies_unsupported_and_unavailable():
    from app.core.errors import LLMToolsUnsupportedError, LLMUnavailableError

    class _Exc(Exception):
        def __init__(self, msg, status=None):
            super().__init__(msg)
            self.status_code = status

    class _Raising:
        def __init__(self, exc):
            self.exc = exc

        def create(self, **kwargs):
            raise self.exc

    def _llm(exc):
        llm = DeepSeekLLM(api_key="test")
        llm._client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=_Raising(exc).create)))
        return llm

    with pytest.raises(LLMToolsUnsupportedError):
        _llm(_Exc("invalid tools parameter", 400)).chat_with_tools(
            [{"role": "user", "content": "q"}], [{"type": "function"}])
    with pytest.raises(LLMUnavailableError):
        _llm(_Exc("timeout")).chat_with_tools([{"role": "user", "content": "q"}], [])
