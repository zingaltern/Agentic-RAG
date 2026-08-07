"""评测 CLI 纯函数单测（防回归：响应体形状、Hit@5 判据、兜底判据）。"""
from __future__ import annotations

from app.cli.rageval import eval_pair, render_report, _aggregate


def _body(answer="年假需提前三天申请[1]。", sources=("员工手册.md",), code=0, iterations=0,
          hallucination="passed", elapsed=10) -> dict:
    return {
        "code": code,
        "data": {
            "answer": answer,
            "sources": [{"filename": s} for s in sources],
            "iterations": iterations,
            "hallucination_check": hallucination,
        },
        "elapsed_ms": elapsed,
    }


def test_direct_hit_and_metrics():
    pair = {"id": "Q1", "type": "direct", "query": "年假几天？", "expected_sources": ["员工手册.md"]}
    r = eval_pair(pair, _body(sources=("员工手册.md", "IT支持手册.md")))
    assert r.ok and r.hit
    assert r.context_recall == 1.0
    assert r.context_precision == 1.0
    assert r.answer_relevancy is True
    assert r.faithful is True


def test_direct_miss_reports_error():
    pair = {"id": "Q2", "type": "direct", "query": "q", "expected_sources": ["员工手册.md"]}
    r = eval_pair(pair, _body(sources=("IT支持手册.md",)))
    assert not r.ok and not r.hit
    assert "员工手册.md" in r.error


def test_no_answer_fallback():
    pair = {"id": "Q3", "type": "no_answer", "query": "q"}
    r = eval_pair(pair, _body(code=21001, answer="抱歉，暂未找到相关答案…", sources=(), iterations=3))
    assert r.ok is True, "21001 兜底应判通过"
    bad = eval_pair(pair, _body(code=0, answer="有的", sources=()))
    assert bad.ok is False


def test_in_process_body_shape_compatible():
    """进程内响应体与 HTTP 模式同形（{code, data}）—— 曾经的回归点。"""
    pair = {"id": "Q4", "type": "rewrite", "query": "q", "expected_sources": ["员工手册.md"]}
    r = eval_pair(pair, _body(sources=("员工手册.md",), iterations=1))
    assert r.iterations == 1
    assert r.elapsed_ms == 10


def test_aggregate_and_report_non_empty():
    results = [
        eval_pair({"id": "A", "type": "direct", "query": "q", "expected_sources": ["员工手册.md"]},
                  _body(sources=("员工手册.md",))),
        eval_pair({"id": "B", "type": "direct", "query": "q", "expected_sources": ["IT支持手册.md"]},
                  _body(sources=())),
    ]
    agg = _aggregate(results)
    assert agg["hit5"] == 0.5
    assert agg["total"] == 2 and agg["ok"] == 1
    import argparse

    args = argparse.Namespace(pairs="x.json", in_process=True, base_url="http://x")
    report = render_report(args, results, agg)
    assert "Hit@5" in report and "Q" not in report  # 非空且含聚合表
