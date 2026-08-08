"""语义幻觉检测 v2 单测（DEVELOPMENT.md 13.4）：4 组规范用例。"""
from __future__ import annotations

import pytest

from app.adapters.detector.semantic_verifier import SemanticVerifierDetector
from app.core.domain import Chunk, ScoredChunk
from app.core.errors import EmbeddingError
from tests.unit.fakes import StubEmbedding


def _ctx(texts: list[str]) -> list[ScoredChunk]:
    return [ScoredChunk(
        chunk=Chunk(chunk_id=f"c{i}", document_id="d", text=t, order=i, parent_id=None, metadata={}),
        score=1.0, notion="rrf") for i, t in enumerate(texts, 1)]


def _detector(**kw):
    return SemanticVerifierDetector(embedding=StubEmbedding(dimension=128), **kw)


def test_true_support():
    """真支撑：句子与引用块文本一致 → 嵌入余弦≈1 ≥ 0.6 → verified。"""
    text = "年假需提前三个工作日通过OA系统提交申请[1]。"
    result = _detector().detect(text, _ctx([text.replace("[1]", "")]))
    assert result.verified is True
    assert result.suspected == []
    assert result.verified_rate == 1.0


def test_false_support():
    """假支撑：引用块内容完全不同 → suspected。"""
    text = "离职补偿金为六个月工资[1]。"
    result = _detector().detect(text, _ctx(["年假申请需提前三个工作日提交。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert result.verified_rate == 0.0


def test_no_citation_sentences_suspected():
    """无引用整句 → suspected（规则保留）；有引用且支撑的句子已证。"""
    text = "年假需提前三天申请。" + "加班可申请调休[1]。"
    result = _detector().detect(text, _ctx(["加班可申请调休。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert "年假需提前三天申请" in result.suspected[0]
    assert result.verified_rate == 0.5


def test_empty_answer():
    result = _detector().detect("", _ctx(["内容"]))
    assert result.verified is True
    assert result.verified_rate == 1.0


def test_threshold_sensitivity():
    """相似但不完全相同的句子：0.3 通过、0.9 不通过（stub 哈希下近似句余弦中等）。"""
    text = "年假需提前三天申请[1]。"
    ctx = _ctx(["年假需提前三天申请，需经部门负责人审批后生效。"])
    loose = _detector(semantic_similarity_threshold=0.1)
    strict = _detector(semantic_similarity_threshold=0.99)
    assert loose.detect(text, ctx).verified is True
    assert strict.detect(text, ctx).verified is False


def test_missing_embedding_raises():
    detector = SemanticVerifierDetector(embedding=None)
    with pytest.raises(EmbeddingError, match="embedding"):
        detector.detect("年假[1]。", _ctx(["年假"]))
