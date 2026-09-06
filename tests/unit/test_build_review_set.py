"""8.4 外部基准测试集构建器纯函数单测：确定性抽样、期望来源映射、域外题不混入语料、落盘幂等、
CMRC 2018 扁平格式兼容。"""
from __future__ import annotations

import json

from app.cli.build_review_set import build_review_set, sanitize_title, write_outputs


def _raw(num_articles: int = 8, paragraphs_each: int = 2) -> dict:
    """合成 SQuAD 信封格式：各段落答案文本互不相同，便于验证域外过滤。"""
    data = []
    for i in range(num_articles):
        paragraphs = []
        for j in range(paragraphs_each):
            answer = f"混合排序策略{i}{j}"
            context = f"文章{i}的第{j}段说明：检索系统采用{answer}融合多路召回与重排序结果。" * 8
            paragraphs.append({
                "context": context,
                "qas": [{"question": f"文章{i}第{j}段采用了什么排序策略？",
                         "answers": [{"text": answer, "answer_start": 0}]}],
            })
        data.append({"title": f"测试文章{i}", "paragraphs": paragraphs})
    return {"version": "v1.0", "data": data}


def _raw_cmrc_flat(num_items: int = 8) -> list:
    """合成 CMRC 2018 扁平格式（真实 dev 集结构）：answers 为字符串列表。"""
    return [{
        "context_id": f"C{i}",
        "title": f"条目{i}",
        "context_text": f"条目{i}的正文说明：检索系统采用混合排序策略{i}融合多路召回与重排序结果。" * 8,
        "qas": [{"query_id": f"Q{i}", "query_text": f"条目{i}采用了什么排序策略？",
                 "answers": [f"混合排序策略{i}", f"混合排序策略{i}"]}],
    } for i in range(num_items)]


def test_build_review_set_deterministic():
    """同 seed 两次构建完全一致（可复现是测试集的基本要求）。"""
    kw = dict(num_docs=4, contexts_per_doc=2, qa_per_ctx=1, num_oov=2, seed=42)
    assert build_review_set(_raw(), **kw) == build_review_set(_raw(), **kw)


def test_cmrc_flat_format_supported():
    """CMRC 2018 扁平结构（query_text + answers 字符串列表）可正常转换。"""
    docs, pairs = build_review_set(_raw_cmrc_flat(), num_docs=3, contexts_per_doc=2,
                                   qa_per_ctx=1, num_oov=2, seed=42)
    direct = [p for p in pairs if p["type"] == "direct"]
    assert len(docs) == 3
    assert len(direct) == 6                       # 6 个入选段落 × 每段 1 题
    filenames = {d["filename"] for d in docs}
    assert all(p["expected_sources"][0] in filenames for p in direct)


def test_direct_pairs_expected_sources_mapping():
    """direct 用例：expected_sources 指向真实生成的语料文档，聚合段落数与题数符合参数。"""
    docs, pairs = build_review_set(_raw(), num_docs=4, contexts_per_doc=2,
                                   qa_per_ctx=2, num_oov=2, seed=42)
    filenames = {d["filename"] for d in docs}
    direct = [p for p in pairs if p["type"] == "direct"]
    assert len(docs) == 4
    assert len(direct) == 4 * 2                   # 4 文档 × 2 段落（合成数据每段仅 1 题）
    for p in direct:
        assert p["expected_sources"] and p["expected_sources"][0] in filenames
        assert p["variant"] == "wiki"
        assert p["id"].startswith("E")


def test_no_answer_not_in_corpus():
    """no_answer 用例：问题与答案文本均不出现在任何语料中（真正域外），不指向来源。"""
    docs, pairs = build_review_set(_raw(), num_docs=3, contexts_per_doc=2,
                                   qa_per_ctx=1, num_oov=5, seed=42)
    corpus = "\n".join(d["content"] for d in docs)
    oov = [p for p in pairs if p["type"] == "no_answer"]
    assert oov, "未入库段落应能提供域外用例"
    assert len(oov) <= 5
    for p in oov:
        assert "expected_sources" not in p
        assert p["query"] not in corpus
        assert p["id"].startswith("N")


def test_question_dedup_across_pairs():
    """同一问题不会同时出现在 direct 与 no_answer（跨集去重）。"""
    _, pairs = build_review_set(_raw(), num_docs=3, contexts_per_doc=2,
                                qa_per_ctx=1, num_oov=5, seed=42)
    queries = [p["query"] for p in pairs]
    assert len(queries) == len(set(queries))


def test_short_or_invalid_articles_filtered():
    """段落过短（<MIN_CTX_CHARS）或无可抽取问题的单元不入选语料。"""
    raw = {"data": [{"title": "太短", "paragraphs": [{"context": "一句话。",
                                                      "qas": [{"question": "这句话说了什么内容？",
                                                               "answers": [{"text": "一句话", "answer_start": 0}]}]}]}]}
    docs, pairs = build_review_set(raw, num_docs=5, contexts_per_doc=2,
                                   qa_per_ctx=2, num_oov=1, seed=42)
    assert docs == []
    assert pairs == []


def test_write_outputs_idempotent(tmp_path):
    """落盘幂等：重生成时清理旧语料文件，不留脏文件；jsonl 逐行合法 JSON。"""
    docs_dir = tmp_path / "sample_data" / "cmrc"
    pairs_path = tmp_path / "qa_pairs" / "cmrc" / "qa_pairs.jsonl"
    docs_dir.mkdir(parents=True)
    stale = docs_dir / "cmrc_999_旧文件.txt"
    stale.write_text("陈旧语料", encoding="utf-8")

    docs, pairs = build_review_set(_raw(), num_docs=2, contexts_per_doc=2,
                                   qa_per_ctx=1, num_oov=1, seed=42)
    write_outputs(docs, pairs, docs_dir, pairs_path)
    assert not stale.exists()
    assert {p.name for p in docs_dir.glob("*.txt")} == {d["filename"] for d in docs}

    lines = [json.loads(line) for line in
             pairs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == pairs


def test_sanitize_title():
    """标题 → 文件名安全片段：非法字符与空白归一，空标题兜底。"""
    assert sanitize_title("经济/学:概论?") == "经济_学_概论"
    assert sanitize_title("  平常 的话语  ") == "平常_的话语"
    assert sanitize_title("///") == "untitled"
