"""P2 指标库单测：IR / claim / 引用 / 兜底 / Bootstrap（app/eval/metrics.py）。"""
from __future__ import annotations

from app.eval.metrics import (
    aggregate_retrieval,
    bootstrap_ci,
    citation_prf,
    claim_faithfulness,
    hit_at_k,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    refusal_accuracy,
)


def test_hit_recall_precision_mrr():
    retrieved = ["a", "b", "c", "d"]
    gold = ["c", "d"]
    assert hit_at_k(retrieved, gold, 2) == 0.0
    assert hit_at_k(retrieved, gold, 3) == 1.0
    assert recall_at_k(retrieved, gold, 4) == 1.0
    assert recall_at_k(retrieved, gold, 2) == 0.0
    assert precision_at_k(retrieved, gold, 4) == 0.5
    assert reciprocal_rank(retrieved, gold) == 1 / 3


def test_ndcg_binary():
    assert ndcg_at_k(["a", "b"], ["a"], 2) == 1.0
    assert ndcg_at_k(["b", "a"], ["a"], 2) < 1.0
    assert ndcg_at_k([], ["a"], 2) == 0.0
    assert ndcg_at_k(["a"], [], 2) == 0.0


def test_bootstrap_ci_deterministic_and_bounded():
    values = [0.0, 0.5, 1.0, 1.0]
    lo1, hi1 = bootstrap_ci(values, n=200, seed=7)
    lo2, hi2 = bootstrap_ci(values, n=200, seed=7)
    assert (lo1, hi1) == (lo2, hi2)
    assert 0.0 <= lo1 <= hi1 <= 1.0
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_claim_and_refusal_metrics():
    assert claim_faithfulness([True, True, False]) == 2 / 3
    assert claim_faithfulness([]) == 1.0
    assert refusal_accuracy([True, False, True]) == 2 / 3
    assert refusal_accuracy([]) == 0.0


def test_citation_prf():
    result = citation_prf(cited_supported=3, total_citations=4, supported_total=5, cited_supported_claims=2)
    assert result["precision"] == 0.75
    assert result["recall"] == 0.4
    assert 0.5 < result["f1"] < 0.53
    assert citation_prf(0, 0, 0, 0) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


def test_metric_guards_on_empty_inputs():
    assert hit_at_k([], [], 5) == 0.0
    assert hit_at_k(["a"], ["a"], 0) == 0.0
    assert recall_at_k([], [], 5) == 0.0
    assert recall_at_k(["a"], ["a"], 0) == 0.0
    assert precision_at_k(["a"], ["a"], 0) == 0.0
    assert bootstrap_ci([1.0], n=0) == (1.0, 1.0)


def test_duplicate_retrieved_ids_use_unique_sequence():
    """重复 ID 按唯一序列压缩名次（与去重后的检索列表同口径）。"""
    assert recall_at_k(["a", "a"], ["a"], 2) == 1.0
    assert ndcg_at_k(["a", "a"], ["a"], 2) == 1.0
    assert reciprocal_rank(["a", "a"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "x", "a"], ["a"]) == 0.5
    assert ndcg_at_k(["x", "x", "a"], ["a"], 3) == ndcg_at_k(["x", "a"], ["a"], 3)


def test_aggregate_retrieval_and_mean():
    items = [{"retrieved": ["a"], "gold": ["a"]}, {"retrieved": ["b"], "gold": ["a"]}]
    agg = aggregate_retrieval(items, k=5, ndcg_k=10)
    assert agg["hit@5"] == 0.5
    assert agg["recall@5"] == 0.5
    assert agg["mrr"] == 0.5
    assert mean([]) == 0.0 and mean([1, 3]) == 2.0
