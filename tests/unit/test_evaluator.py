"""规则评估器单测（DEVELOPMENT.md 5.5、8.1：阈值两侧 + 空上下文）。"""
from __future__ import annotations

from app.adapters.evaluator.rule_evaluator import RuleEvaluator
from app.core.domain import Chunk, ScoredChunk


def _scored(text: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(chunk_id="c1", document_id="d", text=text, order=0, parent_id=None, metadata={}),
        score=1.0, notion="rrf")


def test_empty_context():
    """空上下文 → passed=False, reason=empty_context（5.5）。"""
    result = RuleEvaluator().evaluate("年假怎么申请", [])
    assert result.passed is False
    assert result.reason == "empty_context"
    assert result.score == 0.0


def test_above_threshold_passes():
    """查询词全部被覆盖 + 上下文足够长 → passed。"""
    chunks = [_scored("年假申请需提前三个工作日通过 OA 系统提交。"),
              _scored("入职满一年年假五天。")]
    result = RuleEvaluator(rule_threshold=0.45).evaluate("年假怎么申请", chunks)
    assert result.passed is True
    assert result.reason == "ok"


def test_below_threshold_fails():
    """查询词几乎未被覆盖 → below_threshold。"""
    chunks = [_scored("IT 账号入职当天由部门开通，无需自行申请。")]
    result = RuleEvaluator(rule_threshold=0.45).evaluate("年假申请流程", chunks)
    assert result.passed is False
    assert result.reason == "below_threshold"
    assert result.score < 0.45


def test_threshold_boundary():
    """阈值两侧确定性：全覆盖通过、无覆盖失败（8.1 矩阵）。"""
    evaluator = RuleEvaluator(rule_threshold=0.45)
    full = evaluator.evaluate("年假申请", [_scored("年假申请流程如下。"), _scored("年假申请须提前提交。")])
    assert full.passed is True
    none = evaluator.evaluate("年假申请", [_scored("报销需提交发票。")])
    assert none.passed is False


def test_stopwords_only_query_passes():
    """查询只剩停用词（如"请问"）→ terms 为空 → 判定通过（5.5 无词可覆盖）。"""
    result = RuleEvaluator().evaluate("请问", [_scored("年假申请流程。")])
    assert result.passed is True
    assert result.reason == "ok"


def test_top_n_limits_context():
    """只评估 top_n 块（默认 5）。"""
    chunks = [_scored("IT 账号开通。"), _scored("IT 账号开通。"), _scored("IT 账号开通。"),
              _scored("IT 账号开通。"), _scored("IT 账号开通。"), _scored("年假申请流程。")]
    result = RuleEvaluator(rule_threshold=0.45, top_n=5).evaluate("年假申请", chunks)
    assert result.passed is False, "第 6 块（含目标词）超出 top_n，不应参与评估"
