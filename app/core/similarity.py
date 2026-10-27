"""相似度小工具（唯一实现）：评估器、会话边界与幻觉检测共用。

- `cosine`：向量余弦，零向量按 1e-9 兜底，避免除零；
- `jaccard`：集合 Jaccard；空集按 `empty` 返回（会话边界要求“都为空=相同”，
  字符 2-gram 检测要求“都为空=0”，因此用参数区分，不复制两份实现）。
"""
from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    """向量余弦相似度；长度不一致时按 zip 截断（调用方保证同维）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def jaccard(a: set[str], b: set[str], *, empty: float = 0.0) -> float:
    """集合 Jaccard；`a`/`b` 都为空时返回 `empty`。"""
    if not a and not b:
        return empty
    union = a | b
    if not union:
        return empty
    return len(a & b) / len(union)
