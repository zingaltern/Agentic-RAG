"""P0 rageval 修复单测：耗时形状、完整上下文、基线解析、入库文件过滤。"""
from __future__ import annotations

import sys

import pytest

from app.cli.rageval import (
    _doc_files,
    _metric_values,
    _parse_baseline,
    eval_pair,
)


def _pair():
    return {"id": "T1", "type": "direct", "query": "q", "expected_sources": ["a.md"]}


def test_elapsed_reads_both_response_shapes():
    """进程内 elapsed 在顶层、HTTP 在 data 内，两种都不得为 0。"""
    in_process = {"code": 0, "data": {"answer": "a[1]", "sources": [{"filename": "a.md"}],
                                      "hallucination_check": "passed"},
                  "elapsed_ms": 123}
    http = {"code": 0, "data": {"answer": "a[1]", "sources": [{"filename": "a.md"}],
                                "hallucination_check": "passed", "elapsed_ms": 456}}
    assert eval_pair(_pair(), in_process).elapsed_ms == 123
    assert eval_pair(_pair(), http).elapsed_ms == 456


def test_eval_pair_captures_contexts():
    resp = {"code": 0, "data": {
        "answer": "a[1]", "sources": [{"filename": "a.md"}],
        "contexts": [{"chunk_id": "c1", "filename": "a.md", "text": "完整证据"}],
        "hallucination_check": "passed",
    }}
    assert eval_pair(_pair(), resp).contexts[0]["chunk_id"] == "c1"


def test_parse_baseline_supports_percent_and_fraction(tmp_path):
    path = tmp_path / "base.md"
    path.write_text(
        "| 通过率 | 18/21 | x |\n| Hit@1 | 90.00% | x |\n| 无效行 | abc | x |\n",
        encoding="utf-8",
    )
    parsed = _parse_baseline(path)
    assert abs(parsed["通过率"] - 18 / 21) < 1e-9
    assert parsed["Hit@1"] == 0.9
    assert "无效行" not in parsed


def test_parse_baseline_skips_existing_diff_section(tmp_path):
    path = tmp_path / "base.md"
    path.write_text(
        "## 与基线 diff（13.6 对比模式）\n\n"
        "| 指标 | 基线 | 现状 | Δ |\n|---|---|---|---|\n"
        "| Hit@1 | 80.00% | 90.00% | +10% |\n\n"
        "## 库 complex 聚合\n\n| 指标 | 值 |\n|---|---|\n| Hit@1 | 90.00% |\n",
        encoding="utf-8",
    )
    assert _parse_baseline(path)["Hit@1"] == 0.9, "不得把 diff 表的现状当基线"


def test_eval_pair_uses_contexts_when_sources_missing():
    """兜底/澄清路径 sources 为空：用 contexts.filename 参与 IR，避免误记未命中。"""
    resp = {"code": 21001, "data": {"answer": "未找到", "sources": [],
                                    "contexts": [{"filename": "a.md", "chunk_id": "c1"}],
                                    "hallucination_check": "suspected"}}
    r = eval_pair(_pair(), resp)
    assert r.sources == ["a.md"]
    assert r.hit is True


def test_eval_pair_carries_gold_fields():
    pair = {"id": "T2", "type": "direct", "query": "q", "expected_sources": ["a.md"],
            "golden_answer": "标准答案", "expected_behavior": "answer_found"}
    resp = {"code": 0, "data": {"answer": "a[1]", "sources": [{"filename": "a.md"}],
                                "hallucination_check": "passed"}}
    r = eval_pair(pair, resp)
    assert r.expected_sources == ["a.md"]
    assert r.reference_answer == "标准答案"
    assert r.expected_behavior == "answer_found"


def test_parse_baseline_keeps_first_library_table(tmp_path):
    """多库报告含多张聚合表：diff 只取第一张（与 render_report 取首库口径一致）。"""
    path = tmp_path / "base.md"
    path.write_text(
        "| 通过率 | 88/90 | x |\n| Hit@1 | 90.00% | x |\n\n"
        "| 通过率 | 86/86 | x |\n| Hit@1 | 100.00% | x |\n",
        encoding="utf-8",
    )
    parsed = _parse_baseline(path)
    assert abs(parsed["通过率"] - 88 / 90) < 1e-9
    assert parsed["Hit@1"] == 0.9


def test_metric_values_includes_pass_rate():
    agg = {"total": 4, "ok": 3, "hit1": 0.9, "hit5": 1.0, "context_precision": 0.8,
           "context_recall": 0.7, "answer_relevancy": 1.0, "faithfulness": 0.5,
           "faithfulness_direct": 0.5, "fallback_rate": 1.0}
    assert _metric_values(agg)["通过率"] == 0.75


def test_export_jsonl_rejected_in_http_mode(monkeypatch):
    import app.cli.rageval as r

    monkeypatch.setattr(sys, "argv", ["rageval", "--export-jsonl", "x.jsonl"])
    with pytest.raises(SystemExit):
        r.main()


def test_fresh_default_kb_rejected(monkeypatch):
    import app.cli.rageval as r

    monkeypatch.setattr(sys, "argv", ["rageval", "--fresh-kb", "--kb", "default", "--in-process"])
    with pytest.raises(SystemExit):
        r.main()


def test_doc_files_filters_hidden_and_unsupported(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.PDF").write_text("x", encoding="utf-8")
    (tmp_path / "c.xyz").write_text("x", encoding="utf-8")
    (tmp_path / ".DS_Store").write_text("x", encoding="utf-8")
    names = [p.name for p in _doc_files(tmp_path)]
    assert names == ["a.md", "b.PDF"]
