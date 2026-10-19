"""P1/P2 分块质量单测：原文覆盖、父子关系、ID 稳定（不依赖真实模型）。"""
from __future__ import annotations

import re

from app.adapters.chunker import split_chunks
from app.core.domain import Document


def _doc(text: str) -> Document:
    return Document(document_id="d1", filename="a.md", title="t", content_hash="h", text=text)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_parent_chunks_cover_source_text():
    text = ("第一段：年假需提前三个工作日申请。\n\n"
            "第二段：病假返岗后补交诊断证明，逾期需说明原因。\n\n"
            "第三段：" + "长句内容" * 300 + "。")
    chunks = split_chunks(_doc(text))
    parents = [c for c in chunks if c.parent_id is None]
    assert parents, "必须产出父块"
    covered = _norm("".join(c.text for c in parents))
    for sentence in ("年假需提前三个工作日申请。", "病假返岗后补交诊断证明，逾期需说明原因。"):
        assert _norm(sentence) in covered, f"父块丢失原文：{sentence}"


def test_parent_child_links_and_unique_ids():
    text = "段落一。" + "内容" * 200 + "。段落二。" + "细节" * 200 + "。"
    chunks = split_chunks(_doc(text))
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_id 必须唯一"
    parent_ids = {c.chunk_id for c in chunks if c.parent_id is None}
    for child in [c for c in chunks if c.parent_id is not None]:
        assert child.parent_id in parent_ids, "子块 parent_id 必须指向存在的父块"


def test_split_is_deterministic():
    text = "年假申请流程。" + "补充说明。" * 100
    first = [c.chunk_id for c in split_chunks(_doc(text))]
    second = [c.chunk_id for c in split_chunks(_doc(text))]
    assert first == second, "相同输入必须产出稳定 chunk_id"
