"""semantic_v2 评估器单测（DEVELOPMENT.md 13.3.1）：阈值两侧 + 空上下文。"""
from __future__ import annotations

import pytest

from app.adapters.evaluator.semantic_v2 import SemanticV2Evaluator
from app.core.domain import Chunk, ScoredChunk
from app.core.errors import EmbeddingError
from tests.unit.fakes import StubEmbedding


def _scored(text: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(chunk_id=f"c{hash(text) % 10000}", document_id="d", text=text,
                    order=0, parent_id=None, metadata={"source": "x.md"}),
        score=1.0, notion="rrf")


def _evaluator(**kw):
    return SemanticV2Evaluator(embedding=StubEmbedding(dimension=128), **kw)


def test_empty_context():
    result = _evaluator().evaluate("年假怎么申请", [])
    assert result.passed is False
    assert result.reason == "empty_context"


def test_related_context_passes():
    """查询与候选集中相关块语义相近（stub 哈希使相似文本向量接近）→ 相对分高 → passed。"""
    query = "年假申请需提前三个工作日提交"
    chunks = [_scored(query), _scored("IT 账号入职当天开通，无需自行申请。")]
    result = _evaluator(semantic_v2_threshold=0.45).evaluate(query, chunks)
    assert result.passed is True
    assert result.reason == "ok"
    assert 0 <= result.score <= 1


def test_unrelated_context_fails():
    """候选集整体与查询无关（相对分被护栏压 0）→ below_threshold。"""
    chunks = [_scored("报销需提交发票"), _scored("加班可申请调休")]
    result = _evaluator(semantic_v2_threshold=0.45).evaluate("年假申请流程", chunks)
    assert result.passed is False
    assert result.reason == "below_threshold"


def test_threshold_boundary():
    """阈值两侧确定性：同一输入在低阈值通过、高阈值不通过。"""
    query = "年假申请需提前三个工作日提交"
    chunks = [_scored(query), _scored("IT 账号开通。")]
    low = _evaluator(semantic_v2_threshold=0.1).evaluate(query, chunks)
    high = _evaluator(semantic_v2_threshold=0.95).evaluate(query, chunks)
    assert low.passed is True
    assert high.passed is False


def test_missing_embedding_raises():
    evaluator = SemanticV2Evaluator(embedding=None)
    with pytest.raises(EmbeddingError, match="embedding"):
        evaluator.evaluate("查询", [_scored("内容")])
