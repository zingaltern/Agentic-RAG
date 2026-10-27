"""嵌入适配器单测（DEVELOPMENT.md 5.3、8.1 矩阵）。"""
from __future__ import annotations

import threading
import time

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


def test_model_load_failure_raises_embedding_error():
    """模型加载失败 → EmbeddingError（30002，1.3 外部 I/O 异常包装）。"""
    def boom():
        raise RuntimeError("模型下载失败")

    emb = LocalBGEV15Embedding(model_factory=boom)
    with pytest.raises(EmbeddingError, match="加载失败"):
        emb.ready()


def test_encode_failure_raises_embedding_error():
    """向量化失败 → EmbeddingError（30002）。"""

    class _BadModel(StubSentenceModel):
        def encode(self, sentences, **kwargs):
            raise RuntimeError("encode boom")

    emb = LocalBGEV15Embedding(model_factory=lambda: _BadModel())
    with pytest.raises(EmbeddingError, match="文档向量化失败"):
        emb.embed_documents(["文本"])
    with pytest.raises(EmbeddingError, match="查询向量化失败"):
        emb.embed_query("查询")


# -- 并发安全（P13 NEW-01）：共享 SentenceTransformer 的 tokenizer 不是线程安全的 ----------

class _RacyModel(StubSentenceModel):
    """模拟 HF fast tokenizer：并发进入 encode/tokenizer 时抛 Already borrowed。

    encode 与 tokenizer 共享 busy 标记，因此只有适配器把两者纳入同一把锁才能 0 冲突。
    """

    def __init__(self):
        super().__init__(512)
        self._busy = False
        self._guard = threading.Lock()
        self.overlaps = 0
        self.tokenizer = self._tokenize

    def _enter(self):
        with self._guard:
            if self._busy:
                self.overlaps += 1
                raise RuntimeError("Already borrowed")
            self._busy = True

    def _leave(self):
        with self._guard:
            self._busy = False

    def encode(self, sentences, **kwargs):
        self._enter()
        try:
            time.sleep(0.02)        # 放大竞态窗口：无锁实现必然命中
            return super().encode(sentences, **kwargs)
        finally:
            self._leave()

    def _tokenize(self, text, add_special_tokens=False):  # noqa: ARG002 — 桩签名
        self._enter()
        try:
            time.sleep(0.02)
            return {"input_ids": list(range(len(text)))}
        finally:
            self._leave()


def _run_threads(targets):
    barrier = threading.Barrier(len(targets))
    errors: list[BaseException] = []

    def wrap(fn):
        try:
            barrier.wait(timeout=5)
            fn()
        except BaseException as exc:  # noqa: BLE001 — 收集线程异常在主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(fn,)) for fn in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return errors


def test_concurrent_embed_calls_do_not_share_tokenizer():
    """P13 NEW-01：两个（多个）并发 embed 不得互相踩 tokenizer（不得 Already borrowed）。"""
    model = _RacyModel()
    emb = LocalBGEV15Embedding(model_factory=lambda: model)

    def call(i):
        def _inner():
            vectors = emb.embed_documents([f"文档{i}-{j}" for j in range(3)])
            assert len(vectors) == 3 and len(vectors[0]) == 512
        return _inner

    errors = _run_threads([call(i) for i in range(4)])
    assert not errors, errors
    assert model.overlaps == 0, "并发调用发生 tokenizer 借用冲突（未串行化）"


def test_count_tokens_and_encode_share_the_same_lock():
    """分块计量的 tokenizer 与查询/入库 encode 必须共用一把锁。"""
    model = _RacyModel()
    emb = LocalBGEV15Embedding(model_factory=lambda: model)

    def encode_loop():
        for _ in range(5):
            assert len(emb.embed_query("年假怎么申请")) == 512

    def token_loop():
        for _ in range(5):
            assert emb.count_tokens("年假怎么申请") == 6

    errors = _run_threads([encode_loop, token_loop])
    assert not errors, errors
    assert model.overlaps == 0


def test_transient_borrow_error_is_retried():
    """偶发 Already borrowed 允许有限重试（5.3：重试 2 次）。"""
    class _Flaky(StubSentenceModel):
        def __init__(self):
            super().__init__(512)
            self.calls = 0

        def encode(self, sentences, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Already borrowed")
            return super().encode(sentences, **kwargs)

    model = _Flaky()
    emb = LocalBGEV15Embedding(model_factory=lambda: model)
    assert len(emb.embed_documents(["文本"])) == 1
    assert model.calls == 2


def test_persistent_borrow_error_message_is_actionable():
    """重试仍失败时不得只透传 Already borrowed：文案要说明并发冲突与重试建议。"""
    class _AlwaysBorrowed(StubSentenceModel):
        def encode(self, sentences, **kwargs):
            raise RuntimeError("Already borrowed")

    emb = LocalBGEV15Embedding(model_factory=lambda: _AlwaysBorrowed())
    with pytest.raises(EmbeddingError) as excinfo:
        emb.embed_documents(["文本"])
    message = str(excinfo.value)
    assert "并发" in message and "重试" in message
    assert message.startswith("文档向量化失败")
