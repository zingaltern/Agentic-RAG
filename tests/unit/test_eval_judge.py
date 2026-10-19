"""P0 Judge 骨架单测：JSON 解析、重试、异常降级（app/eval/judge.py）。"""
from __future__ import annotations

from app.eval.judge import LLMJudge


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_judge_parses_json_score():
    judge = LLMJudge(FakeLLM(['{"score": 0.75, "reason": "大部分有依据"}']))
    result = judge.faithfulness("q", "a", ["ctx"])
    assert result.score == 0.75
    assert result.passed is True
    assert "依据" in result.reason


def test_judge_retries_then_fails_closed():
    judge = LLMJudge(FakeLLM(["not json", "still not json"]), max_retries=1)
    result = judge.answer_correctness("q", "a", "ref")
    assert result.score == 0.0
    assert result.reason == "judge_parse_error"


def test_judge_exception_and_disabled():
    judge = LLMJudge(FakeLLM([RuntimeError("boom")]))
    assert judge.answer_relevancy("q", "a").reason.startswith("judge_error")
    disabled = LLMJudge(None)
    assert disabled.faithfulness("q", "a", []).score == 0.0
    assert disabled.faithfulness("q", "a", []).reason == "judge_disabled"


def test_judge_extracts_json_from_fence_and_multiple_objects():
    judge = LLMJudge(FakeLLM([
        '说明\n```json\n{"foo": 1}\n```\n{"score": 0.6, "reason": "有依据"}'
    ]))
    assert judge.faithfulness("q", "a", ["ctx"]).score == 0.6


def test_join_contexts_truncates_total_budget():
    text = "证据" * 3000
    joined = LLMJudge._join_contexts([text] * 10)
    assert len(joined) <= 8000 + 5


def test_judge_clamps_score():
    judge = LLMJudge(FakeLLM(['{"score": 3, "reason": "x"}']))
    assert judge.faithfulness("q", "a", []).score == 1.0
    judge = LLMJudge(FakeLLM(['{"score": -1, "reason": "x"}']))
    assert judge.faithfulness("q", "a", []).score == 0.0
