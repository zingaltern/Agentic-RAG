"""评测指标库（P2）：IR / claim / 引用 / 兜底等纯函数指标。

设计约束（避免过度设计）：
- 纯标准库、确定性、无外部 I/O；输入输出都是 Python 原生类型。
- 每个函数只做一件事；CLI/报告只做聚合与展示。
- 统计不确定性用 Bootstrap 分位数，不引入 scipy/numpy 依赖。
"""
from __future__ import annotations

import math
import random


def _unique(items: list[str]) -> list[str]:
    """按首次出现顺序去重；IR 指标对重复 ID 必须幂等。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def hit_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    """Top-k 中命中任一 gold 记为 1.0，否则 0.0。"""
    if not gold or k <= 0:
        return 0.0
    gold_set = set(gold)
    return 1.0 if any(item in gold_set for item in _unique(retrieved)[:k]) else 0.0


def recall_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    """Recall@k = |Top-k ∩ gold| / |gold|（Top-k 先去重）。"""
    if not gold or k <= 0:
        return 0.0
    gold_set = set(gold)
    return len([item for item in _unique(retrieved)[:k] if item in gold_set]) / len(gold_set)


def precision_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    """Precision@k = |Top-k ∩ gold| / k（检索不足 k 条时按 k 计，符合标准定义）。"""
    if k <= 0:
        return 0.0
    gold_set = set(gold or [])
    return len([item for item in _unique(retrieved)[:k] if item in gold_set]) / k


def reciprocal_rank(retrieved: list[str], gold: list[str]) -> float:
    """首个相关结果的排名倒数；无命中为 0。"""
    gold_set = set(gold or [])
    for idx, item in enumerate(_unique(retrieved), start=1):
        if item in gold_set:
            return 1.0 / idx
    return 0.0


def ndcg_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    """二值相关性 NDCG@k（Top-k 先去重，结果上限 1.0）。"""
    if not gold or k <= 0:
        return 0.0
    gold_set = set(gold)
    dcg = sum(1.0 / math.log2(i + 1) for i, item in enumerate(_unique(retrieved)[:k], start=1)
              if item in gold_set)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(gold_set)) + 1))
    return min(1.0, dcg / idcg) if idcg else 0.0


def mean(values: list[float]) -> float:
    """均值；空列表返回 0.0。"""
    return sum(values) / len(values) if values else 0.0


def bootstrap_ci(values: list[float], n: int = 1000, confidence: float = 0.95,
                 seed: int = 42) -> tuple[float, float]:
    """Bootstrap 均值置信区间（默认 95%，1000 次，固定 seed 可复现）。"""
    if not values:
        return (0.0, 0.0)
    if n <= 0:
        center = sum(values) / len(values)
        return (center, center)
    rng = random.Random(seed)
    size = len(values)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(size)] for _ in range(size)]
        means.append(sum(sample) / size)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = means[min(len(means) - 1, max(0, int(alpha * len(means))))]
    hi = means[min(len(means) - 1, max(0, int((1.0 - alpha) * len(means)) - 1))]
    return (lo, hi)


def claim_faithfulness(supported: list[bool]) -> float:
    """claim 级 Faithfulness = 被上下文支持的 claim 数 / 总 claim 数。"""
    return (sum(1 for ok in supported if ok) / len(supported)) if supported else 1.0


def citation_prf(cited_supported: int, total_citations: int,
                 supported_total: int, cited_supported_claims: int) -> dict[str, float]:
    """引用支持度的 P/R/F1（ALCE 风格）。

    - precision = 被证据支持的引用数 / 全部引用数
    - recall    = 被正确引用的支持句数 / 全部有支持句数
    """
    precision = cited_supported / total_citations if total_citations else 0.0
    recall = cited_supported_claims / supported_total if supported_total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def refusal_accuracy(results: list[bool]) -> float:
    """兜底/no_answer/no_invent 正确率。"""
    return (sum(1 for ok in results if ok) / len(results)) if results else 0.0


def aggregate_retrieval(items: list[dict], k: int = 5, ndcg_k: int = 10) -> dict[str, float]:
    """对 [{retrieved, gold}] 序列聚合检索指标。"""
    return {
        f"hit@{k}": mean([hit_at_k(i["retrieved"], i["gold"], k) for i in items]),
        f"recall@{k}": mean([recall_at_k(i["retrieved"], i["gold"], k) for i in items]),
        f"precision@{k}": mean([precision_at_k(i["retrieved"], i["gold"], k) for i in items]),
        "mrr": mean([reciprocal_rank(i["retrieved"], i["gold"]) for i in items]),
        f"ndcg@{ndcg_k}": mean([ndcg_at_k(i["retrieved"], i["gold"], ndcg_k) for i in items]),
    }
