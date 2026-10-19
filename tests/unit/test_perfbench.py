"""性能基准 CLI 纯函数单测（8.5.2：percentile / scales 解析 / 合成语料 / 报告渲染 / 基线解析）。"""
from __future__ import annotations

import argparse
import random

import pytest

from app.cli.perfbench import (
    DocSpec,
    ScaleResult,
    _parse_baseline,
    _scales_label,
    _scale_rows,
    build_query_set,
    generate_corpus,
    make_doc,
    parse_scales,
    percentile,
    render_report,
    stage_diffs,
    stage_share,
    summary_metrics,
)


# -- percentile（线性插值）------------------------------------------------------

def test_percentile_known_array():
    """[1..100]：p50=50.5、p90=90.1、p95=95.05（线性插值口径）。"""
    values = list(range(1, 101))
    assert percentile(values, 50) == 50.5
    assert percentile(values, 90) == pytest.approx(90.1)
    assert percentile(values, 95) == pytest.approx(95.05)


def test_percentile_edges():
    """边界：空列表报错、单元素恒定、p=0 取最小、p=100 取最大。"""
    with pytest.raises(ValueError):
        percentile([], 50)
    assert percentile([7.0], 0) == 7.0
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 100) == 7.0
    assert percentile([3.0, 1.0, 2.0], 0) == 1.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0
    with pytest.raises(ValueError):
        percentile([1.0], 101)
    with pytest.raises(ValueError):
        percentile([1.0], -1)


# -- scales 解析 ----------------------------------------------------------------

def test_parse_scales_ok():
    assert parse_scales("50,150,300") == [50, 150, 300]
    assert parse_scales("50") == [50]
    assert parse_scales(" 50 , 100 ") == [50, 100]
    assert parse_scales("300,50") == [50, 300], "无序输入应升序归一"


@pytest.mark.parametrize("raw", ["", "   ", "abc", "50,", "0", "-5", "50,50", "5.0"])
def test_parse_scales_invalid(raw):
    """空/非整数/非正/重复/小数均报 ValueError。"""
    with pytest.raises(ValueError):
        parse_scales(raw)


# -- 阶段差分 -------------------------------------------------------------------

def test_stage_diffs_cumulative_to_per_stage():
    """stage_timings 为自 t0 累计 ms → 相邻差分（ingestion.py _stage 口径）。"""
    timings = {"parse": 10, "clean": 12, "chunk": 20, "embed": 90, "index": 92}
    assert stage_diffs(timings) == {"parse": 10, "clean": 2, "chunk": 8, "embed": 70, "index": 2}


def test_stage_diffs_missing_keys_and_order():
    """缺失阶段按 0 计且不影响后续差分基准；缺省键也按 STAGE_ORDER 输出。"""
    assert stage_diffs({"parse": 10, "embed": 30}) == {
        "parse": 10, "clean": 0, "chunk": 0, "embed": 20, "index": 0}
    assert set(stage_diffs({})) == {"parse", "clean", "chunk", "embed", "index"}


# -- 合成语料 -------------------------------------------------------------------

def test_corpus_deterministic_and_count():
    """同 seed 完全一致、文档数正确；不同 seed 有差异。"""
    a = generate_corpus(42, 7)
    b = generate_corpus(42, 7)
    assert len(a) == 7
    assert [(d.filename, d.text, d.question) for d in a] == [(d.filename, d.text, d.question) for d in b]
    assert [(d.filename, d.text) for d in a] != [(d.filename, d.text) for d in generate_corpus(43, 7)]


def test_corpus_prefix_property():
    """前 N 份与更大语料前 N 份逐字节一致（查询集跨规模点复用的前提）。"""
    whole = generate_corpus(42, 20)
    part = generate_corpus(42, 6)
    assert [(d.filename, d.text) for d in whole[:6]] == [(d.filename, d.text) for d in part]


def test_corpus_doc_shape_and_unique_fact():
    """每份 ~600-1200 字、含唯一编号实体事实（实体编号全局唯一、事实句在正文且仅一次）。"""
    docs = generate_corpus(42, 12)
    codes = []
    for d in docs:
        assert 500 <= len(d.text) <= 1300, f"{d.filename} 长度越界：{len(d.text)}"
        assert d.fact in d.text
        assert d.text.count(d.fact) == 1
        # 从事实句提取实体编号（HR-xxx / 基准-XX-nnn / OPS-nnnn）
        code = d.filename.rsplit(".", 1)[0]
        codes.append(code)
        assert code in d.fact, "实体编号必须出现在事实句中"
    assert len(set(codes)) == len(codes), "编号实体必须全局唯一"
    assert {d.filename.split(".")[-1] for d in docs} <= {"txt", "md"}


def test_make_doc_topics_rotate():
    """制度/产品/运维三类主题轮转，产品口径对齐 8.5.2 示例句式。"""
    product = make_doc(random.Random(0), 1)
    assert "保修期" in product.fact and "个月" in product.fact
    policy = make_doc(random.Random(0), 0)
    assert "制度编号" in policy.fact
    ops = make_doc(random.Random(0), 2)
    assert "工单" in ops.fact and "处置时长" in ops.fact


# -- 查询集 ---------------------------------------------------------------------

def test_query_set_expected_sources_within_corpus():
    """查询集 expected_sources 必须落在语料文件名集合内，且问题非空、按 id 递增。"""
    docs = generate_corpus(42, 9)
    queries = build_query_set(docs, 5)
    filenames = {d.filename for d in docs}
    assert len(queries) == 5
    for k, q in enumerate(queries):
        assert q["id"] == f"P{k:03d}"
        assert q["query"] == docs[[d.filename for d in docs].index(q["expected_sources"][0])].question
        assert set(q["expected_sources"]) <= filenames
    assert len({q["query"] for q in queries}) == 5, "direct 问题应互不相同"


def test_query_set_cap_and_errors():
    """超出语料数时截断；n<=0 报错。"""
    docs = generate_corpus(42, 3)
    assert len(build_query_set(docs, 10)) == 3
    with pytest.raises(ValueError):
        build_query_set(docs, 0)


# -- 报告渲染 -------------------------------------------------------------------

def _sample_result(scale: int) -> ScaleResult:
    return ScaleResult(scale=scale, docs=scale, chunks=scale * 2, ingest_seconds=10.0,
                       stage_ms={"parse": 500, "clean": 100, "chunk": 200, "embed": 2000, "index": 200},
                       latencies_ms=[100.0, 200.0, 300.0, 400.0, 500.0],
                       queries=5, hits=4, errors=0, failed_docs=0)


def test_summary_metrics_and_stage_share():
    r = _sample_result(10)
    sm = summary_metrics(r)
    assert sm["入库吞吐(docs/s)"] == pytest.approx(1.0)
    assert sm["avg(ms)"] == pytest.approx(300.0)
    assert sm["P50(ms)"] == pytest.approx(300.0)
    assert sm["max(ms)"] == pytest.approx(500.0)
    assert sm["Hit@5"] == pytest.approx(0.8)
    assert stage_share(r, "embed") == pytest.approx(2000 / 3000)
    assert stage_share(r, "parse") == pytest.approx(500 / 3000)


def _args(**kw) -> argparse.Namespace:
    base = dict(kb="perf", scales=[10, 20], queries=5, seed=42, baseline=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_render_report_contains_key_lines():
    report = render_report(_args(), [_sample_result(10), _sample_result(20)])
    assert "# RAG 性能基准报告" in report and "## 规模点 10" in report
    for key in ("文档数", "chunks", "入库总耗时(s)", "入库吞吐(docs/s)", "embed占比",
                "parse占比", "avg(ms)", "P50(ms)", "P90(ms)", "P95(ms)", "max(ms)", "Hit@5"):
        assert f"| {key} |" in report, f"明细表缺指标行：{key}"
    assert "## 跨规模汇总" in report and "| 10 |" in report and "| 20 |" in report
    assert "80.0%" in report, "Hit@5 应以百分比渲染"
    assert "Δ" not in report, "无基线时不应出现 Δ 列"


def test_scales_label_accepts_raw_string():
    """回归：render_report 收到 argparse 原始 scales 字符串时不得逐字符 join。"""
    assert _scales_label("5,10") == "5, 10"
    assert _scales_label([50, 150]) == "50, 150"
    report = render_report(_args(scales="10,20"), [_sample_result(10)])
    assert "- 规模点：10, 20（文档数）" in report


def test_render_report_with_baseline_delta_columns():
    report = render_report(_args(baseline="reports/base.md"),
                           [_sample_result(10)], baseline={10: {
                               "入库吞吐(docs/s)": 1.5, "avg(ms)": 250.0, "P50(ms)": 250.0,
                               "P90(ms)": 450.0, "P95(ms)": 480.0, "max(ms)": 520.0, "Hit@5": 0.6}})
    assert "Δ吞吐" in report and "Δavg" in report and "ΔHit@5" in report
    assert "-0.50" in report, "吞吐 1.0 vs 基线 1.5 → -0.50"
    assert "+20.0pt" in report, "Hit@5 0.8 vs 基线 0.6 → +20pt"
    rows = _scale_rows(_sample_result(10))
    assert dict(rows)["Hit@5"] == "80.0%"


# -- 基线解析 -------------------------------------------------------------------

def test_parse_baseline_summary_table(tmp_path):
    """解析基线"跨规模汇总"表：数值按列读取、Hit@5 归一 0~1。"""
    base = tmp_path / "base.md"
    base.write_text(
        "# RAG 性能基准报告（8.5.2）\n\n"
        "## 规模点 50\n\n| 指标 | 值 |\n|---|---|\n| Hit@5 | 90.0% |\n\n"
        "## 跨规模汇总\n\n"
        "| 规模 | 入库吞吐(docs/s) | avg(ms) | P50(ms) | P90(ms) | P95(ms) | max(ms) | Hit@5 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 50 | 2.50 | 120.5 | 110.0 | 150.0 | 180.0 | 200.0 | 96.7% |\n"
        "| 150 | 2.10 | 135.0 | 120.0 | 160.0 | 190.0 | 210.0 | 93.3% |\n\n"
        "## 备注\n\n- 无\n",
        encoding="utf-8")
    parsed = _parse_baseline(base)
    assert set(parsed) == {50, 150}
    row = parsed[50]
    assert row["入库吞吐(docs/s)"] == pytest.approx(2.50)
    assert row["avg(ms)"] == pytest.approx(120.5)
    assert row["max(ms)"] == pytest.approx(200.0)
    assert row["Hit@5"] == pytest.approx(0.967), "Hit@5 应归一为 0~1 分数"
    assert parsed[150]["P95(ms)"] == pytest.approx(190.0)


def test_parse_baseline_skips_delta_columns(tmp_path):
    """带 Δ 列的报告亦可作基线（Δ 列跳过，不污染指标值）。"""
    base = tmp_path / "base.md"
    base.write_text(
        "## 跨规模汇总\n\n"
        "| 规模 | 入库吞吐(docs/s) | Δ吞吐 | avg(ms) | Δavg | Hit@5 | ΔHit@5 |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| 50 | 2.50 | -0.30 | 120.5 | +5.0 | 96.7% | +1.2pt |\n",
        encoding="utf-8")
    parsed = _parse_baseline(base)
    assert parsed[50]["入库吞吐(docs/s)"] == pytest.approx(2.50)
    assert parsed[50]["avg(ms)"] == pytest.approx(120.5)
    assert parsed[50]["Hit@5"] == pytest.approx(0.967)
    assert set(parsed[50]) == {"入库吞吐(docs/s)", "avg(ms)", "Hit@5"}, "Δ 列不得进入指标集"


def test_parse_baseline_missing_or_empty(tmp_path):
    assert _parse_baseline(tmp_path / "nope.md") == {}
    empty = tmp_path / "empty.md"
    empty.write_text("# 只有标题\n", encoding="utf-8")
    assert _parse_baseline(empty) == {}


# -- 数据结构烟囱 ----------------------------------------------------------------

def test_docspec_fields():
    d = DocSpec(filename="a.md", text="正文", fact="事实", question="问题")
    assert (d.filename, d.text, d.fact, d.question) == ("a.md", "正文", "事实", "问题")
