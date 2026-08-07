"""父子分块（DEVELOPMENT.md 5.2）。

契约（3.9）：`split_chunks(doc: Document) -> list[Chunk]`
规则：
1. 以自然段落为切割单元；段落超目标按 `。！？；` 句子切，禁止在词中断（尽量在空白处断）。
2. 父块 ≤ parent_target_tokens（必要完整）；父块再按 child_target_tokens 切子块。
   - 父块本身未超子块目标（≤ child 目标 token）时不再产出重复子块。
3. 父块 parent_id=None；子块 parent_id=父块 chunk_id。
4. chunk_id = sha256(document_id + ":" + str(order))[:16]（2.1）。
"""
from __future__ import annotations

import hashlib
import math
import re

from app.core.domain import Chunk, Document

_SENTENCE_END = "。！？；"


def _estimate_tokens(text: str, token_char_ratio: float) -> int:
    """中文 token 估算：len(text)/token_char_ratio 向上取整（5.2）。"""
    return max(1, math.ceil(len(text) / token_char_ratio))


def _split_sentences(text: str) -> list[str]:
    """按句末标点切句（保留标点），丢弃空白段。"""
    parts = re.split(rf"(?<=[{_SENTENCE_END}])", text)
    return [p.strip() for p in parts if p.strip()]


def _hard_cut(text: str, limit_chars: int) -> tuple[str, str]:
    """超长单句硬切：优先在窗口内最后一个空白处断开（避免断词），否则按字符边界。"""
    head, tail = text[:limit_chars], text[limit_chars:]
    space = head.rfind(" ")
    if 0 < space and space >= limit_chars * 0.6:
        head, tail = head[:space], head[space:] + tail
    return head, tail


def _split_overlong(text: str, target_tokens: int, token_char_ratio: float) -> list[str]:
    """把超目标的单句切成 ≤ target_tokens 的若干段。"""
    limit_chars = max(1, int(target_tokens * token_char_ratio))
    parts: list[str] = []
    while _estimate_tokens(text, token_char_ratio) > target_tokens and len(text) > limit_chars:
        head, text = _hard_cut(text, limit_chars)
        head = head.strip()
        if head:
            parts.append(head)
    text = text.strip()
    if text:
        parts.append(text)
    return parts


def _group_to_target(segments: list[str], target_tokens: int, token_char_ratio: float) -> list[str]:
    """把句子序列按目标 token 数聚合成块（超长单句先硬切，返回文本列表）。"""
    expanded: list[str] = []
    for seg in segments:
        if _estimate_tokens(seg, token_char_ratio) > target_tokens:
            expanded.extend(_split_overlong(seg, target_tokens, token_char_ratio))
        else:
            expanded.append(seg)
    groups: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for seg in expanded:
        seg_tokens = _estimate_tokens(seg, token_char_ratio)
        if current and current_tokens + seg_tokens > target_tokens:
            groups.append("".join(current))
            current, current_tokens = [], 0
        current.append(seg)
        current_tokens += seg_tokens
    if current:
        groups.append("".join(current))
    return groups


def _chunk_id(document_id: str, order: int) -> str:
    return hashlib.sha256(f"{document_id}:{order}".encode("utf-8")).hexdigest()[:16]


def split_chunks(
    doc: Document,
    parent_target_tokens: int = 512,
    child_target_tokens: int = 128,
    token_char_ratio: float = 1.8,
) -> list[Chunk]:
    """按父子分块规则产出一组 Chunk（纯函数；配置缺省值与 settings.yaml 一致）。"""
    text = (doc.text or "").strip()
    if not text:
        return []
    chunks: list[Chunk] = []
    order = 0

    # 1) 以自然段落（换行分隔）聚合成父块
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    parents: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for para in paragraphs:
        para_tokens = _estimate_tokens(para, token_char_ratio)
        if current and current_tokens + para_tokens > parent_target_tokens:
            parents.append("".join(current))
            current, current_tokens = [], 0
        if para_tokens > parent_target_tokens:
            if current:
                parents.append("".join(current))
                current, current_tokens = [], 0
            parents.extend(_group_to_target(_split_sentences(para), parent_target_tokens, token_char_ratio))
        else:
            current.append(para)
            current_tokens += para_tokens
    if current:
        parents.append("".join(current))

    # 2) 产出父块 + 按子块目标切分子块
    def emit(text: str, parent_id: str | None) -> str:
        nonlocal order
        chunk = Chunk(
            chunk_id=_chunk_id(doc.document_id, order),
            document_id=doc.document_id,
            text=text,
            order=order,
            parent_id=parent_id,
            metadata=dict(doc.metadata),
        )
        chunks.append(chunk)
        order += 1
        return chunk.chunk_id

    for parent_text in parents:
        parent_text = parent_text.strip()
        if not parent_text:
            continue
        parent_chunk_id = emit(parent_text, None)
        if _estimate_tokens(parent_text, token_char_ratio) <= child_target_tokens:
            continue  # 父块本身已在子块目标内，不产出重复子块
        for group in _group_to_target(_split_sentences(parent_text), child_target_tokens, token_char_ratio):
            emit(group, parent_chunk_id)

    return chunks
