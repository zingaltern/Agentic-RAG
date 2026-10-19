"""索引 DNA（P1-3，DEVELOPMENT.md 5.10/13.5）：向量索引构建配置的指纹。

换 embedding 模型 / 改分块 / 改清洗配置后，新旧向量混在同一 collection 属"向量空间
不一致"——检索结果不可信。本模块对影响向量形态的配置做规范化指纹（dna）：

- 写入三处：Chroma collection metadata（建集合时）、DocEntry.index_dna、
  chunk.metadata["index_dna"]；
- 查询侧比对 collection dna 与当前配置 dna：不一致 → audit（标记+日志）或 reject（30006）；
- 任一相关配置项变更即 dna 变化（sha256 前 12 位，碰撞概率可忽略）。

纯函数、无 I/O、无副作用；config 传入 SystemConfig 或任意带同名字段的对象（测试友好）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# V5.2：解析/分块行为版本——行为变更（计量口径/行拼接/句子切分等）时递增，
# 保证存量 collection 被 dna 检测为"需重建"，避免新旧块形态混在一处。
PIPELINE_VERSION = 2


def _chunker_fingerprint(chunker: Any) -> dict:
    """影响块形态的 chunker 参数（P1-4/P1-5：表格原子块/硬边界/上下文前缀均改变块形态）。"""
    return {
        "parent_target_tokens": chunker.parent_target_tokens,
        "child_target_tokens": chunker.child_target_tokens,
        "token_char_ratio": chunker.token_char_ratio,
        "measure": getattr(chunker, "measure", "auto"),   # V5.2：auto=精确计数（改变块形态）
        "table_aware": chunker.table_aware.model_dump(),
        "hard_boundary": chunker.hard_boundary.model_dump(),
        "context_prefix": chunker.context_prefix.model_dump(),
    }


def _cleaner_fingerprint(cleaner: Any) -> dict:
    """影响正文内容的 cleaner 配置（enabled 关闭时参数无效应，但仍纳入指纹以便追溯）。"""
    return {
        "enabled": cleaner.enabled,
        "normalize": cleaner.normalize,
        "noise": cleaner.noise,
        "dedup": cleaner.dedup,
        "quality": cleaner.quality,
        "dedup_threshold": cleaner.dedup_threshold,
        "min_chunk_chars": cleaner.min_chunk_chars,
    }


def compute_index_dna(config: Any) -> dict:
    """计算索引 DNA。

    返回 `{"dna": str, "embedding_model": str, "embedding_dim": int,
    "chunker": {...}, "cleaner": {...}}`——dna = sha256(排序键规范化 JSON)[:12]。
    """
    embedding = config.embedding
    fingerprint = {
        "pipeline_version": PIPELINE_VERSION,
        "embedding_model": embedding.model_name,
        "embedding_dim": embedding.dimension,
        "embedding_query_instruction": embedding.query_instruction,
        "chunker": _chunker_fingerprint(config.chunker),
        "cleaner": _cleaner_fingerprint(config.cleaner),
    }
    canonical = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    dna = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return {"dna": dna, **fingerprint}
