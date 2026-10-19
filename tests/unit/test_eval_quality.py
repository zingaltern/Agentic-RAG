"""质量评测 CLI 纯函数单测（P0-P2）：聚合、报告、生成/judge 解耦、兜底口径。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cli.eval_quality import (
    _refusal_passed,
    aggregate_quality,
    render_report,
    run_quality,
)
from app.eval.schema import case_from_dict


def _record(**kwargs):
    base = {"id": "Q1", "type": "direct", "variant": "", "source": "auto_converted",
            "query": "q", "code": 0, "answer": "a", "retrieved": ["a.md"],
            "gold": ["a.md"], "elapsed_ms": 12}
    base.update(kwargs)
    return base


def test_aggregate_quality_ir_and_refusal():
    records = [
        _record(id="Q1"),
        _record(id="Q2", retrieved=["b.md"]),
        _record(id="N1", type="no_answer", gold=[], refusal_ok=True),
    ]
    agg = aggregate_quality(records)
    assert agg["total"] == 3
    assert agg["retrieval_n"] == 2
    assert agg["hit@5"] == 0.5
    assert agg["refusal_rate"] == 1.0
    assert agg["elapsed_ms_avg"] == 12


def test_aggregate_quality_judge_ci_key_matches_render(monkeypatch):
    import app.cli.eval_quality as eq

    monkeypatch.setattr(eq, "bootstrap_ci", lambda values: (0.25, 0.75))
    records = [_record(judge_faithfulness=1.0), _record(judge_faithfulness=0.0)]
    agg = aggregate_quality(records)
    assert agg["judge_faithfulness"] == 0.5
    assert agg["judge_faithfulness_ci"] == (0.25, 0.75)
    args = argparse.Namespace(pairs="p.jsonl", docs="d", kb="quality_smoke")
    report = render_report(agg, records, args)
    assert "95% CI 25.00%~75.00%" in report, "render 必须读取 judge_*_ci"


def test_judge_disabled_renders_na():
    records = [_record()]
    agg = aggregate_quality(records)
    args = argparse.Namespace(pairs="p.jsonl", docs="d", kb="quality_smoke")
    report = render_report(agg, records, args)
    assert "-（未启用/无样本）" in report
    assert "auto_converted" in report


def test_retrieved_ids_falls_back_to_contexts():
    """兜底/澄清路径 sources 为空，但 contexts 有 filename：不能误记为检索 0 条。"""
    from app.cli.eval_quality import _retrieved_ids

    case = case_from_dict({"id": "Q1", "type": "direct", "query": "q",
                           "expected_sources": ["a.md"]})
    resp = {"code": 21001, "data": {"sources": [],
                                    "contexts": [{"filename": "a.md", "chunk_id": "c1"}]}}
    assert _retrieved_ids(case, resp) == ["a.md"]


def test_refusal_failure_is_visible():
    records = [_record(id="N1", type="no_answer", gold=[], refusal_ok=False)]
    agg = aggregate_quality(records)
    args = argparse.Namespace(pairs="p.jsonl", docs="d", kb="quality_smoke")
    report = render_report(agg, records, args)
    assert "FAIL" in report, "兜底失败不能显示为 '-'"


def test_refusal_stub_only_shortcut():
    case = case_from_dict({"id": "N1", "type": "no_answer", "query": "q"})
    resp = {"code": 0, "data": {"answer": "编造内容", "hallucination_check": "suspected",
                                "detection_rate": 0.0}}
    assert _refusal_passed(case, resp, generation_is_stub=True) is True
    assert _refusal_passed(case, resp, generation_is_stub=False) is False


class _FakeLLM:
    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return '{"score": 1.0, "reason": "ok"}'


def test_judge_does_not_toggle_generation(monkeypatch):
    """JUDGE=1 只启用 judge，生成仍必须是 Stub；REAL=1 才切换生成。"""
    import app.cli.eval_quality as eq

    fake_llm = _FakeLLM()
    calls: list[object] = []
    monkeypatch.setattr(eq, "_build_llm", lambda config: fake_llm)

    def fake_run(config, docs, pairs, **kwargs):
        calls.append(kwargs.get("llm"))
        return [{"code": 0, "data": {"answer": "a[1]", "sources": [],
                                     "contexts": [], "hallucination_check": "passed"},
                 "elapsed_ms": 1} for _ in pairs]

    monkeypatch.setattr("app.cli.rageval._run_in_process", fake_run)
    cases = [case_from_dict({"id": "Q1", "type": "direct", "query": "q",
                             "expected_sources": ["a.md"], "golden_answer": "a"})]

    _, agg = run_quality(cases, SimpleNamespace(), Path("."), "quality_smoke", use_judge=True)
    assert calls[-1] is None, "仅 JUDGE=1 时不得把真实 LLM 用于生成"
    assert agg["generation_mode"] == "stub" and agg["judge_mode"] == "real"

    _, agg = run_quality(cases, SimpleNamespace(), Path("."), "quality_smoke", use_real_llm=True)
    assert calls[-1] is fake_llm, "REAL=1 时必须用真实 LLM 生成"
    assert agg["generation_mode"] == "real" and agg["judge_mode"] == "off"


def test_judge_failures_are_not_fake_zero(monkeypatch):
    """judge 调用失败必须标 degraded，不能把失败伪装成 0 分。"""
    import app.cli.eval_quality as eq

    class BadLLM:
        def chat(self, messages, temperature=0.1, max_tokens=1024):
            raise RuntimeError("api down")

    monkeypatch.setattr(eq, "_build_llm", lambda config: BadLLM())
    monkeypatch.setattr("app.cli.rageval._run_in_process",
                        lambda config, docs, pairs, **kw: [
                            {"code": 0, "data": {"answer": "a", "sources": [], "contexts": []},
                             "elapsed_ms": 1} for _ in pairs])
    cases = [case_from_dict({"id": "Q1", "type": "direct", "query": "q",
                             "expected_sources": ["a.md"], "golden_answer": "a"})]
    records, agg = run_quality(cases, SimpleNamespace(), Path("."), "quality_smoke", use_judge=True)
    assert agg["judge_mode"] == "degraded"
    assert agg["judge_failures"] == agg["judge_calls"] > 0
    assert "judge_faithfulness" not in records[0]


def test_report_notes_stub_judge_and_unavailable():
    records = [_record(judge_faithfulness=0.0)]
    agg = aggregate_quality(records)
    agg.update(generation_mode="stub", judge_mode="real", judge_requested=True)
    args = argparse.Namespace(pairs="p.jsonl", docs="d", kb="quality_smoke")
    report = render_report(agg, records, args)
    assert "仅验证判分链路" in report

    agg.update(judge_mode="unavailable")
    report = render_report(agg, records, args)
    assert "judge 已关闭" in report


def test_limit_and_fresh_default_guards(monkeypatch):
    import app.cli.eval_quality as eq

    base = ["eval-quality", "--pairs", "p", "--docs", "d"]
    monkeypatch.setattr(sys, "argv", base + ["--limit", "0"])
    with pytest.raises(SystemExit):
        eq.main()
    monkeypatch.setattr(sys, "argv", base + ["--kb", "default", "--fresh-kb"])
    with pytest.raises(SystemExit):
        eq.main()
