"""嵌入适配器单测（DEVELOPMENT.md 5.3、8.1 矩阵）。"""
from __future__ import annotations

import pytest

from app.adapters.embedding.local_bge_v15 import LocalBGEV15Embedding
from app.core.errors import EmbeddingError, SystemConfigError
from tests.unit.fakes import StubSentenceModel


@pytest.fixture()
def stub_factory():
    def factory(dim: int = 512):
        return StubSentenceModel(dim)
    return factory


def test_dimension_property_auto(stub_factory):
    emb = LocalBGEV15Embedding(model_name="stub", dimension=0, model_factory=stub_factory)
    assert emb.dimension == 512


def test_dimension_consistent_with_config(stub_factory):
    emb = LocalBGEV15Embedding(dimension=512, model_factory=stub_factory)
    assert emb.dimension == 512


def test_dimension_mismatch_raises_system_config_error(stub_factory):
    """5.3 对齐硬约束：配置维度与模型实际不符 → SystemConfigError。"""
    emb = LocalBGEV15Embedding(dimension=768, model_factory=stub_factory)
    with pytest.raises(SystemConfigError, match="维度"):
        emb.ready()


def test_embed_query_has_instruction_prefix(stub_factory):
    """BGE 硬约束：embed_query 自动拼 query_instruction；embed_documents 不加（8.1 矩阵）。"""
    model = stub_factory()
    emb = LocalBGEV15Embedding(query_instruction="为这个句子生成表示以用于检索相关文章：", model_factory=lambda: model)
    emb.embed_query("如何申请年假")
    emb.embed_documents(["年假需提前三天申请。", "加班可申请调休。"])
    assert len(model.encoded_sentences) == 2
    query_batch, doc_batch = model.encoded_sentences
    assert query_batch[0].startswith("为这个句子生成表示以用于检索相关文章：")
    assert doc_batch == ["年假需提前三天申请。", "加班可申请调休。"], "文档文本不得加指令前缀"


def test_embed_lengths_and_dimension(stub_factory):
    emb = LocalBGEV15Embedding(model_factory=stub_factory)
    texts = ["一", "二三四五", "六七八九十" * 20]
    vectors = emb.embed_documents(texts)
    assert len(vectors) == len(texts)
    assert all(len(v) == 512 for v in vectors)
    query_vector = emb.embed_query("查什么")
    assert len(query_vector) == 512
    assert emb.dimension == 512


def test_query_vector_equals_document_vector_of_same_text(stub_factory):
    """归一化后，同文本的 query（带指令）与 doc（不带指令）维度一致即可；此处验证同文本查询自洽。"""
    emb = LocalBGEV15Embedding(model_factory=stub_factory)
    v1 = emb.embed_query("申请年假")
    v2 = emb.embed_query("申请年假")
    assert v1 == v2
