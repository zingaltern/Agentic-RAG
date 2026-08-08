"""评测 CLI 纯函数单测（防回归：响应体形状、Hit@1/Hit@5 判据、13.6 no_answer 判据）。"""
from __future__ import annotations

import argparse

from app.cli.rageval import (
    NO_ANSWER_PHRASES,
    _aggregate,
    _parse_baseline,
    eval_pair,
    no_answer_passed,
    render_report,
)


def _body(answer="年假需提前三天申请[1]。", sources=("员工手册.md",), code=0, iterations=0,
          hallucination="passed", elapsed=10, message="ok") -> dict:
    return {
        "code": code,
        "message": message,
        "data": {
            "answer": answer,
            "sources": [{"filename": s} for s in sources],
            "iterations": iterations,
            "hallucination_check": hallucination,
        },
        "elapsed_ms": elapsed,
    }


def test_no_answer_phrases_constant():
    """13.6 判据文案集合单一常量（口径统一）。"""
    assert "未找到相关依据" in NO_ANSWER_PHRASES
    assert "未找到相关答案" in NO_ANSWER_PHRASES
    assert len(NO_ANSWER_PHRASES) >= 3


def test_no_answer_passed_criteria():
    """13.6 修订判据：code0+未找到文案 ∨ 21001。"""
    assert no_answer_passed(21001, "") is True
    assert no_answer_passed(0, "知识库中未找到相关依据。") is True
    assert no_answer_passed(0, "根据知识库，年假 5 天[1]。") is False
    assert no_answer_passed(50001, "未找到相关依据") is False


def test_no_answer_fallback_21001():
    pair = {"id": "Q1", "type": "no_answer", "query": "q"}
    r = eval_pair(pair, _body(code=21001, answer="抱歉，暂未找到相关答案…", sources=(), iterations=3))
    assert r.ok is True


def test_no_answer_code0_with_phrase():
    """code=0 + answer 含未找到文案 → 通过（13.6 修订判据）。"""
    pair = {"id": "Q2", "type": "no_answer", "query": "q"}
    r = eval_pair(pair, _body(code=0, answer="知识库中未找到相关依据。", sources=(), iterations=0))
    assert r.ok is True


def test_no_answer_process_mode_message_fallback():
    """进程内兜底：无 answer 键时回退读 message（历史 3 条 no_answer 变绿的修复点）。"""
    pair = {"id": "Q3", "type": "no_answer", "query": "q"}
    resp = {"code": 0, "message": "未找到相关依据", "data": {}, "elapsed_ms": 4}
    r = eval_pair(pair, resp)
    assert r.ok is True


def test_no_answer_code0_plain_answer_fails():
    pair = {"id": "Q4", "type": "no_answer", "query": "q"}
    r = eval_pair(pair, _body(code=0, answer="有的，年假 5 天[1]。", sources=()))
    assert r.ok is False


def test_no_answer_process_mode_unsupported_answer_passes():
    """进程内 Stub 模式补强：回答与上下文完全无支撑（suspected + verified_rate=0）→ 视同拒答。"""
    pair = {"id": "Q5", "type": "no_answer", "query": "q"}
    resp = {"code": 0, "message": "ok", "data": {
        "answer": "根据知识库上下文，请参考编号 [1] 对应来源。",
        "sources": [{"filename": "员工手册.md"}],
        "iterations": 0, "hallucination_check": "suspected", "detection_rate": 0.0,
    }, "elapsed_ms": 5}
    r = eval_pair(pair, resp)
    assert r.ok is True
    # 有支撑（rate>0）→ 视为有依据回答，不通过
    resp["data"]["detection_rate"] = 0.8
    assert eval_pair(pair, resp).ok is False


def test_direct_hit_metrics_and_variant():
    pair = {"id": "D1", "type": "direct", "variant": "table",
            "query": "住宿标准多少？", "expected_sources": ["差旅政策.md"]}
    r = eval_pair(pair, _body(sources=("差旅政策.md", "报销制度.md")))
    assert r.ok and r.hit and r.hit1
    assert r.variant == "table"
    assert r.context_recall == 1.0 and r.context_precision == 1.0
    assert r.faithful is True


def test_direct_miss_reports_error():
    pair = {"id": "D2", "type": "direct", "query": "q", "expected_sources": ["员工手册.md"]}
    r = eval_pair(pair, _body(sources=("IT支持手册.md",)))
    assert not r.ok and not r.hit
    assert "员工手册.md" in r.error


def test_no_invent_passed_when_fallback():
    pair = {"id": "N1", "type": "no_invent", "query": "q", "expected_sources": ["报销制度.md"]}
    r = eval_pair(pair, _body(code=21001, answer="抱歉，暂未找到相关答案…", sources=()))
    assert r.ok is True


def test_aggregate_and_report_non_empty():
    results = [
        eval_pair({"id": "A", "type": "direct", "query": "q", "expected_sources": ["员工手册.md"]},
                  _body(sources=("员工手册.md",))),
        eval_pair({"id": "B", "type": "direct", "query": "q", "expected_sources": ["IT支持手册.md"]},
                  _body(sources=())),
        eval_pair({"id": "C", "type": "no_answer", "query": "q"},
                  _body(code=0, answer="知识库中未找到相关依据。", sources=())),
        eval_pair({"id": "D", "type": "rewrite", "variant": "spoken", "query": "q",
                   "expected_sources": ["差旅政策.md"]},
                  _body(sources=("差旅政策.md",))),
    ]
    agg = _aggregate(results)
    assert agg["hit5"] == 2 / 3
    assert agg["total"] == 4 and agg["ok"] == 3
    assert agg["fallback_rate"] == 1.0

    args = argparse.Namespace(in_process=True, base_url="http://x")
    report = render_report(args, {"default": results}, {"default": agg}, baseline=None)
    assert "Hit@1" in report and "按库汇总" in report
    assert "特征分布" in report, "含多特征（spoken）时必须渲染特征分布区"
    assert report.count("PASS") == 3


def test_baseline_diff_parsing(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("| Hit@1 | 100.00% | 说明 |\n| Faithfulness(direct) | 0.00% | x |\n",
                    encoding="utf-8")
    parsed = _parse_baseline(base)
    assert parsed["Hit@1"] == 1.0
    assert parsed["Faithfulness(direct)"] == 0.0


def test_baseline_diff_appears_in_report(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("| Hit@1 | 90.00% | x |\n", encoding="utf-8")
    results = [eval_pair({"id": "A", "type": "direct", "query": "q",
                          "expected_sources": ["员工手册.md"]},
                         _body(sources=("员工手册.md",)))]
    agg = _aggregate(results)
    args = argparse.Namespace(in_process=True, base_url="http://x")
    report = render_report(args, {"default": results}, {"default": agg},
                           baseline=_parse_baseline(base))
    assert "与基线 diff" in report and "+10%" in report
