"""RRF 融合单测（DEVELOPMENT.md 5.4，回归「key=lambda x」bug）。"""
from __future__ import annotations

import pytest

from app.adapters.retriever.hybrid_rrf import rrf_fusion


def test_rrf_sorted_by_score_desc():
    """融合结果必须按分数降序（RRF 分数 = 名次倒数加权和）。"""
    bm25 = {"a": 2.0, "b": 1.0}
    vector = {"b": 3.0}             # b 双路命中（1/62+1/61），a 仅 BM25 单路（1/61）
    fused = rrf_fusion(bm25, vector, k=60)
    scores = dict(fused)
    assert scores["b"] > scores["a"], "b 在两路都命中，融合分必须更高"
    assert [key for key, _ in fused] == ["b", "a"]


def test_rrf_not_sorted_by_tuple_key():
    """回归：禁止 sorted(key=lambda x: x)（按元组排会把 'a10' 排在 'a2' 前）。"""
    bm25 = {"a10": 5.0, "a2": 3.0}
    vector = {"a2": 9.0}            # a2 双路命中（1/62+1/61），a10 仅 BM25 单路（1/61）
    fused = rrf_fusion(bm25, vector, k=60)
    assert [key for key, _ in fused] == ["a2", "a10"], "必须按分数（item[1]）降序"


def test_rrf_fusion_formula():
    """5.4 伪代码逐项核对：1/(k+rank+1) 累积。"""
    fused = dict(rrf_fusion({"x": 1.0}, {"x": 1.0}, k=2))
    assert abs(fused["x"] - (1.0 / 3 + 1.0 / 3)) < 1e-9


def test_rrf_empty_scores():
    assert rrf_fusion({}, {}, k=60) == []


def test_rrf_single_side():
    fused = dict(rrf_fusion({}, {"a": 1.0, "b": 2.0}, k=60))
    assert [k for k, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)] == ["b", "a"]


def test_rrf_top_n_limit():
    bm25 = {f"k{i}": float(i) for i in range(10)}
    fused = rrf_fusion(bm25, {}, k=60, top_n=3)
    assert len(fused) == 3
    assert fused[0][0] == "k9"


def test_rrf_k_parameter():
    """k 越大，名次差异影响越小。"""
    low = dict(rrf_fusion({"a": 1.0, "b": 0.0}, {}, k=1))
    high = dict(rrf_fusion({"a": 1.0, "b": 0.0}, {}, k=1000))
    assert low["a"] / low["b"] > high["a"] / high["b"]


def test_rrf_missing_in_one_side_keeps_score():
    fused = dict(rrf_fusion({"only_bm25": 1.0}, {"only_vector": 1.0}, k=60))
    assert set(fused) == {"only_bm25", "only_vector"}
    assert pytest.approx(fused["only_bm25"]) == 1.0 / 61
