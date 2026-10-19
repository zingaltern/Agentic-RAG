"""Chunker 契约测试（DEVELOPMENT.md 5.2）：父子块规则。"""
from __future__ import annotations

import math
import re

from app.adapters.chunker import split_chunks
from app.core.domain import Document

PARENT_TOKENS = 512
CHILD_TOKENS = 128
RATIO = 1.8


def _doc(text: str, **meta) -> Document:
    return Document(
        document_id="doc_test1",
        filename="测试.md",
        title="测试",
        content_hash="h",
        text=text,
        metadata={"source": "测试.md", "kind": "md", "page": None, **meta},
    )


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / RATIO))


def test_empty_document_no_chunks():
    assert split_chunks(_doc("")) == []
    assert split_chunks(_doc("   \n  ")) == []


def test_short_document_single_parent():
    chunks = split_chunks(_doc("年假需提前三天申请。"))
    assert len(chunks) == 1
    c = chunks[0]
    assert c.parent_id is None
    assert c.order == 0
    assert c.metadata["source"] == "测试.md"
    assert c.metadata["kind"] == "md"


def test_parent_token_limit():
    """父块 ≤ 512 tokens；必要完整。"""
    text = "\n".join(f"第{i}段。" + "内容内容内容" * 10 for i in range(200))
    chunks = split_chunks(_doc(text))
    parents = [c for c in chunks if c.parent_id is None]
    assert parents, "必须产出父块"
    for p in parents:
        assert _tokens(p.text) <= PARENT_TOKENS


def test_child_token_limit_and_link():
    """子块 ≤ 128 tokens，且 parent_id 指向父块。"""
    long_para = "。".join(f"第{i}句，" + "详细说明详细说明详细说明详细说明详细说明" for i in range(40)) + "。"
    chunks = split_chunks(_doc(long_para))
    parents = [c for c in chunks if c.parent_id is None]
    children = [c for c in chunks if c.parent_id is not None]
    assert children, "长父块必须产出子块"
    parent_ids = {p.chunk_id for p in parents}
    for ch in children:
        assert _tokens(ch.text) <= CHILD_TOKENS
        assert ch.parent_id in parent_ids, "子块 parent_id 必须指向存在的父块"


def test_sentence_boundary_split():
    """子块/父块尽量在句末标点处断开。"""
    sentences = [f"这是第{i}句话。" for i in range(60)]
    chunks = split_chunks(_doc("".join(sentences)))
    parents = [c for c in chunks if c.parent_id is None]
    for p in parents:
        assert p.text.endswith(("。", "！", "？", "；")) or _tokens(p.text) <= PARENT_TOKENS


def test_overlong_sentence_hard_cut():
    """无句号超长单句：硬切后每段 ≤ 目标 tokens。"""
    text = "很" * 3000
    chunks = split_chunks(_doc(text))
    parents = [c for c in chunks if c.parent_id is None]
    assert parents
    for p in parents:
        assert _tokens(p.text) <= PARENT_TOKENS


def test_chunk_id_format_and_uniqueness():
    text = "\n".join(f"段落{i}，内容内容。" for i in range(10))
    chunks = split_chunks(_doc(text))
    ids = [c.chunk_id for c in chunks]
    assert all(re.match(r"^[0-9a-f]{16}$", cid) for cid in ids)
    assert len(ids) == len(set(ids)), "chunk_id 必须唯一"
    assert [c.order for c in chunks] == list(range(len(chunks))), "order 必须从 0 连续递增"


def test_short_parent_no_duplicate_child():
    """父块未超子块目标时不产出重复子块。"""
    chunks = split_chunks(_doc("短文本内容。"))
    assert len(chunks) == 1
    assert chunks[0].parent_id is None


def test_metadata_inherited():
    chunks = split_chunks(_doc("第一段。\n第二段。", author="张三", page=3))
    for c in chunks:
        assert c.metadata["source"] == "测试.md"
        assert c.metadata["kind"] == "md"
        assert c.metadata["author"] == "张三"
        assert c.metadata["page"] == 3


def test_paragraph_grouping():
    """多个短段落聚合为一个父块。"""
    text = "\n".join(f"短段落{i}。" for i in range(5))
    chunks = split_chunks(_doc(text))
    parents = [c for c in chunks if c.parent_id is None]
    assert len(parents) == 1
    assert "短段落0。" in parents[0].text and "短段落4。" in parents[0].text


# -- V5.2：精确计量 / 换行保留 / 中英切句 -------------------------------------------


def test_token_counter_exact_limit_and_newline_preserved():
    """注入计数器（1 字符=1 token）时：父块硬保证 ≤ 目标；块内以换行保留行结构。"""
    text = "\n".join(f"第{i}行内容。" + "补充说明" * 8 for i in range(40))
    chunks = split_chunks(_doc(text), token_counter=len)
    parents = [c for c in chunks if c.parent_id is None]
    assert len(parents) >= 2
    assert all(len(p.text) <= PARENT_TOKENS for p in parents), "精确口径下父块不得超过目标"
    assert "\n" in parents[0].text, "父块必须保留行结构（不得行粘连）"


def test_token_counter_used_over_ratio():
    """计量确实生效：中文近 1 字符/token 时，精确口径父块数必须多于旧 ratio 口径。"""
    text = "\n".join(f"第{i}段。" + "内容" * 60 for i in range(30))
    by_ratio = [c for c in split_chunks(_doc(text)) if c.parent_id is None]
    by_exact = [c for c in split_chunks(_doc(text), token_counter=len) if c.parent_id is None]
    assert len(by_exact) > len(by_ratio)


def test_token_counter_returns_none_falls_back_to_ratio():
    """计数器返回 None（引擎不支持语义）→ 回退 ratio 估算，不抛错。"""
    chunks = split_chunks(_doc("年假需提前三天申请。"), token_counter=lambda _t: None)
    assert len(chunks) == 1


def test_token_counter_raises_falls_back_to_ratio():
    """计数器异常 → 回退 ratio 估算（不阻塞入库）。"""
    def bad(_text: str) -> int:
        raise RuntimeError("counter boom")

    chunks = split_chunks(_doc("年假需提前三天申请。"), token_counter=bad)
    assert len(chunks) == 1


def test_english_sentence_split_no_decimal_break():
    """英文断句：句末标点 + 空白才断（3.14/版本号不误切）。"""
    from app.adapters.chunker import _split_sentences

    parts = _split_sentences("Version 2.1 is stable. The value is 3.14 exactly! Next one?")
    assert len(parts) == 3
    assert any("3.14" in p for p in parts)
    assert parts[0].strip().startswith("Version 2.1")
