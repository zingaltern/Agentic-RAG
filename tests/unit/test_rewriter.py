"""规则改写器单测（DEVELOPMENT.md 5.6）：确定性、无 LLM。"""
from __future__ import annotations

import pytest

from app.adapters.rewriter.rule_rewriter import RuleRewriter, normalize_query
from app.core.domain import EvaluationResult


def _fb(reason: str) -> EvaluationResult:
    return EvaluationResult(passed=False, score=0.1, reason=reason)


def test_normalize_half_width_and_collapse():
    assert normalize_query("　年假怎么申请？  ") == "年假怎么申请?"
    assert normalize_query("a  b   c") == "a b c"


def test_spoken_to_written():
    """口语 → 书面（最长词优先）。"""
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("咋样申请年假", _fb("below_threshold"))
    assert "怎么样" in out
    out2 = rw.rewrite("行不行", _fb("below_threshold"))
    assert "是否可行" in out2


def test_stopwords_removed():
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("请问年假怎么申请呢", _fb("below_threshold"))
    assert "请问" not in out
    assert "呢" not in out
    assert "年假" in out and "申请" in out


def test_empty_context_drops_single_chars():
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("我要查年假", _fb("empty_context"))
    assert "要" not in out and "我" not in out, "empty_context 时剔除单字停用词"
    assert "年假" in out


def test_synonym_expansion_builtin():
    """synonyms.csv 缺失 → 内置词表兜底，同义词扩充。"""
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("年假能攒多少天", _fb("below_threshold"))
    assert "年假" in out
    assert any(w in out for w in ("休假", "假期")), "命中同义词组应扩充"


def test_synonym_expansion_from_file(tmp_path):
    p = tmp_path / "synonyms.csv"
    p.write_text("年假,休假,假期\n报销,报账\n", encoding="utf-8")
    rw = RuleRewriter(synonyms_csv=str(p))
    out = rw.rewrite("年假", _fb("below_threshold"))
    assert "休假" in out and "假期" in out


def test_corrupt_synonyms_file_falls_back(tmp_path):
    p = tmp_path / "synonyms.csv"
    p.write_bytes(b"\xff\xfe broken")
    rw = RuleRewriter(synonyms_csv=str(p))   # 不崩溃
    out = rw.rewrite("年假", _fb("below_threshold"))
    assert "年假" in out


def test_rewrite_returns_something_when_all_stripped():
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("的的的", _fb("below_threshold"))
    assert out == "的的的", "全被删掉时回退原查询"


def test_rewrite_idempotent_deterministic():
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    q = "咋样申请年假报销"
    assert rw.rewrite(q, _fb("below_threshold")) == rw.rewrite(q, _fb("below_threshold"))


def test_rewrite_keeps_working_without_feedback_reason():
    rw = RuleRewriter(synonyms_csv="/nonexistent/synonyms.csv")
    out = rw.rewrite("行不行", EvaluationResult(passed=False, score=0.0, reason="below_threshold"))
    assert "是否可行" in out
