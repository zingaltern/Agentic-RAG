"""BGE 重排器单测（DEVELOPMENT.md 13.2）：确定性打分排序 + 空输入 + 异常包装。"""
from __future__ import annotations

import pytest

from app.adapters.reranker.bge_reranker import BGEReranker
from app.core.domain import Chunk, ScoredChunk
from app.core.errors import EmbeddingError


class StubCrossEncoder:
    """模拟 CrossEncoder.predict：分数由块文本长度决定（确定性）。"""

    def __init__(self, scores=None):
        self.scores = scores or [1.0, 0.5, 0.9]
        self.called_with = None

    def predict(self, pairs, batch_size=None):
        self.called_with = (list(pairs), batch_size)
        return self.scores[:len(pairs)]


def _scored(text: str, chunk_id: str = "") -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(chunk_id=chunk_id or f"c{abs(hash(text)) % 10000}", document_id="d",
                    text=text, order=0, parent_id=None, metadata={"source": "x.md"}),
        score=0.5, notion="rrf")


def test_rerank_sorts_by_score_desc():
    model = StubCrossEncoder(scores=[0.3, 0.9, 0.5])
    reranker = BGEReranker(model_factory=lambda: model)
    chunks = [_scored("甲"), _scored("乙"), _scored("丙")]
    out = reranker.rerank("查询", chunks)
    assert [c.chunk.text for c in out] == ["乙", "丙", "甲"]
    assert [c.score for c in out] == [0.9, 0.5, 0.3]
    assert all(c.notion == "rerank" for c in out), "重排结果 notion 必须为 rerank"
    assert model.called_with[0] == [("查询", "甲"), ("查询", "乙"), ("查询", "丙")]


def test_rerank_empty_input():
    reranker = BGEReranker(model_factory=lambda: StubCrossEncoder())
    assert reranker.rerank("查询", []) == []


def test_rerank_model_load_failure():
    def boom():
        raise RuntimeError("模型下载失败")

    reranker = BGEReranker(model_factory=boom)
    with pytest.raises(EmbeddingError, match="加载失败"):
        reranker.ready()


def test_rerank_predict_failure():
    class _Bad(StubCrossEncoder):
        def predict(self, pairs, batch_size=None):
            raise RuntimeError("predict boom")

    reranker = BGEReranker(model_factory=lambda: _Bad())
    with pytest.raises(EmbeddingError, match="重排打分失败"):
        reranker.rerank("查询", [_scored("甲")])


def test_rerank_identity_kept_as_fallback_registered():
    """identity 仍是合法 provider（13.2 回退默认）。"""
    from app.core.config import ALLOWED_PROVIDERS

    assert "identity" in ALLOWED_PROVIDERS["reranker"]
    assert "bge_reranker" in ALLOWED_PROVIDERS["reranker"]
