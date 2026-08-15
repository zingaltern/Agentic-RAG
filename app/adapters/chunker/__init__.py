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


def _build_line_sections(text: str, sections: list) -> list:
    """T5：把 [(title, 行号)] 展开为逐行 section 归属（标题行起到下一标题行前）。"""
    lines = text.split("\n")
    line_section: list = [None] * len(lines)
    for idx, (title, line_no) in enumerate(sections):
        if not (0 <= line_no < len(lines)):
            continue
        next_no = sections[idx + 1][1] if idx + 1 < len(sections) else len(lines)
        for i in range(line_no, min(next_no, len(lines))):
            line_section[i] = title
    return line_section


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

    # T5：章节标题 → 逐行 section 归属（sections 为解析器产出的 [(title, 行号)]）
    line_section: list = []
    sections_meta = doc.metadata.get("sections") or []
    if sections_meta:
        line_section = _build_line_sections(text, sections_meta)

    def section_of(line_no: int) -> str | None:
        if not line_section:
            return None
        return line_section[line_no] if 0 <= line_no < len(line_section) else None

    # 1) 以自然段落（换行分隔）聚合成父块（记录每段起始行号，用于 section 归属）
    raw_lines = text.split("\n")
    items = [(i, line.strip()) for i, line in enumerate(raw_lines) if line.strip()]
    parents: list[tuple[str, int]] = []      # (text, start_line)
    current: list[str] = []
    current_start = 0
    current_tokens = 0
    for line_no, para in items:
        para_tokens = _estimate_tokens(para, token_char_ratio)
        if current and current_tokens + para_tokens > parent_target_tokens:
            parents.append(("".join(current), current_start))
            current, current_tokens = [], 0
        if para_tokens > parent_target_tokens:
            if current:
                parents.append(("".join(current), current_start))
                current, current_tokens = [], 0
            for group in _group_to_target(_split_sentences(para), parent_target_tokens, token_char_ratio):
                parents.append((group, line_no))
        else:
            if not current:
                current_start = line_no
            current.append(para)
            current_tokens += para_tokens
    if current:
        parents.append(("".join(current), current_start))

    # 2) 产出父块 + 按子块目标切分子块（section 继承：子块沿用父块章节）
    def emit(text: str, parent_id: str | None, section: str | None) -> str:
        nonlocal order
        meta = {k: v for k, v in doc.metadata.items() if k != "sections"}
        if section is not None:
            meta["section"] = section         # T5：章节标题落库（可参与 filters 等值过滤）
        chunk = Chunk(
            chunk_id=_chunk_id(doc.document_id, order),
            document_id=doc.document_id,
            text=text,
            order=order,
            parent_id=parent_id,
            metadata=meta,
        )
        chunks.append(chunk)
        order += 1
        return chunk.chunk_id

    for parent_text, start_line in parents:
        parent_text = parent_text.strip()
        if not parent_text:
            continue
        section = section_of(start_line)
        parent_chunk_id = emit(parent_text, None, section)
        if _estimate_tokens(parent_text, token_char_ratio) <= child_target_tokens:
            continue  # 父块本身已在子块目标内，不产出重复子块
        for group in _group_to_target(_split_sentences(parent_text), child_target_tokens, token_char_ratio):
            emit(group, parent_chunk_id, section)

    return chunks
