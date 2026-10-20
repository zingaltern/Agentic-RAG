"""8.5.1 复杂难度分层验证集生成器单测：确定性、结构、回环自检（项目解析器可抽出全部事实句、
质量门不触发）、域外题不混入语料。

回环自检是生成器的生死线：语料产物若魔数不合法或事实抽不出来，入库即 30003/评测必空——
因此这里对全部语料逐份走 parse_file 真解析，而不是只查字节头。
"""
from __future__ import annotations

import io

from app.adapters.parser import evaluate_parse_quality, parse_file
from app.cli.build_eval_stress import (
    MD_ARCH,
    TXT_ATT_V1,
    TXT_ATT_V2,
    TXT_VISITOR,
    TXT_VISITOR_COPY,
    build_corpus,
    build_pairs,
)

# 生产配置的质量门阈值（configs/settings.yaml cleaner 段 / config.py 默认值）
MIN_TEXT_CHARS = 30
MAX_GARBLED_RATIO = 0.05
MIN_PAGES_WITH_TEXT_RATIO = 0.2


def _parse_all() -> dict[str, str]:
    """逐份真解析全部语料 → {filename: 提取文本}。"""
    docs = build_corpus()
    return {d.filename: parse_file(io.BytesIO(d.payload), d.filename).text for d in docs}


def test_corpus_deterministic():
    """同参数两次构建逐字节一致（验证集资产必须可复现）。"""
    a = [(d.filename, d.payload) for d in build_corpus()]
    b = [(d.filename, d.payload) for d in build_corpus()]
    assert a == b
    assert len({f for f, _ in a}) == len(a)          # 文件名唯一


def test_office_core_props_pinned():
    """回归：Office 产物 core.xml 时间戳必须钉死。

    openpyxl 在 save() 时用当前时间覆盖 properties.modified（跨秒重生成字节漂移），
    生成器 _rezip_fixed(pin_core_props=True) 负责回钉；此处直接断言产物。
    """
    import zipfile

    from app.cli.build_eval_stress import _xlsx_bytes

    payload = _xlsx_bytes("表", ["列"], [["值"]])
    core = zipfile.ZipFile(io.BytesIO(payload)).read("docProps/core.xml").decode("utf-8")
    assert core.count("2026-01-01T00:00:00Z") == 2, core


def test_fix_pdf_id_both_forms():
    """回归：/ID 的 hex 与字面串两种形式都必须被固定化（否则随机 ID 跨次漂移）。"""
    from app.cli.build_eval_stress import _fix_pdf_id

    hex_form = b"trailer\n<</ID[<AABB><CCDD>]>>\n"
    fixed = _fix_pdf_id(hex_form)
    assert b"AABB" not in fixed and b"CCDD" not in fixed

    # pymupdf 实测形式：第二段为含八进制转义的字面串
    literal_form = b"trailer\n<</ID[<C38662>(q\\3136b\\2054\\332RQ\\0075\"4P@\\332)]>>\n"
    fixed = _fix_pdf_id(literal_form)
    assert b"C38662" not in fixed and b"3136b" not in fixed


def test_pairs_structure_and_tiers():
    """90 条测试对：id 唯一、variant/type 合法、分层分布符合 8.5.1 设计。"""
    pairs = build_pairs()
    assert len(pairs) == 90
    assert [p["id"] for p in pairs] == [f"C{i:03d}" for i in range(1, 91)]
    assert len({p["id"] for p in pairs}) == 90
    allowed_types = {"direct", "rewrite", "no_answer", "no_invent"}
    assert all(p["type"] in allowed_types for p in pairs)
    by_variant: dict[str, int] = {}
    for p in pairs:
        by_variant[p["variant"]] = by_variant.get(p["variant"], 0) + 1
    assert by_variant == {"direct": 24, "paraphrase": 12, "multi_doc": 10, "table": 10,
                          "noisy": 8, "long_doc": 6, "cross_fmt": 6, "oov": 8, "no_invent": 6}


def test_expected_sources_exist():
    """所有 expected_sources 都指向真实语料文件（跨层）。"""
    filenames = {d.filename for d in build_corpus()}
    for p in build_pairs():
        for src in p.get("expected_sources", []):
            assert src in filenames, f"{p['id']} 期望来源不存在：{src}"


def test_roundtrip_facts_extractable():
    """回环自检（生死线）：每份语料经项目解析器真解析后，登记事实句逐字出现。"""
    docs = build_corpus()
    for d in docs:
        text = parse_file(io.BytesIO(d.payload), d.filename).text
        for fact in d.facts:
            assert fact in text, f"{d.filename} 事实句抽不出来：{fact!r}"


def test_quality_gate_all_pass():
    """全部语料通过解析质量门（empty_text/high_garbled/few_pages_with_text 均不触发）。"""
    docs = build_corpus()
    for d in docs:
        doc = parse_file(io.BytesIO(d.payload), d.filename)
        flags = evaluate_parse_quality(
            doc.text, doc.metadata["text_stats"],
            min_text_chars=MIN_TEXT_CHARS, max_garbled_ratio=MAX_GARBLED_RATIO,
            min_pages_with_text_ratio=MIN_PAGES_WITH_TEXT_RATIO)
        assert flags == [], f"{d.filename} 质量门触发：{flags}"


def test_oov_queries_absent_from_corpus():
    """oov 域外问题（含关键词组合）不逐字出现在任何语料中，no_answer 判据才公平。"""
    texts = list(_parse_all().values())
    for p in build_pairs():
        if p["variant"] == "oov":
            assert not any(p["query"] in t for t in texts), f"oov 混入语料：{p['query']}"


def test_long_doc_volume():
    """长文档埋点白皮书 ≥3000 字（长文档难度层的体量前提）。"""
    doc = next(d for d in build_corpus() if d.filename == MD_ARCH)
    assert len(doc.payload.decode("utf-8")) >= 3000


def test_near_and_exact_dup_pairs():
    """近重复对：v1/v2 各含自身事实且互不串味；完全重复对：字节级相同（验证入库幂等语义）。"""
    docs = {d.filename: d for d in build_corpus()}
    v1 = docs[TXT_ATT_V1].payload.decode("utf-8")
    v2 = docs[TXT_ATT_V2].payload.decode("utf-8")
    assert "30 分钟" in v1 and "30 分钟" not in v2
    assert "60 分钟" in v2 and "60 分钟" not in v1
    assert docs[TXT_VISITOR].payload == docs[TXT_VISITOR_COPY].payload
