"""本地 BGE v1.5 嵌入（DEVELOPMENT.md 5.3）。

- 默认模型 BAAI/bge-small-zh-v1.5（稠密 512 维），首次加载联网下载到 HF 缓存。
- 硬约束：embed_query 自动拼 query_instruction 前缀；embed_documents 不加（BGE 中文特性）。
- normalize_embeddings=True（余弦相似即点积）。
- 懒加载/device 解析/异常包装复用 LocalModelAdapter 公共基类（13.11.2）。
- 配置维度与模型实际不符 → SystemConfigError（启动即错，禁止静默降级）。
- 并发安全（P13 NEW-01）：HF fast tokenizer 的 Rust 侧并发 encode 会抛 AlreadyBorrowed，
  所有模型调用（encode/tokenizer）经同一把进程内互斥锁串行化 + 有限重试（5.3）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.adapters.base import LocalModelAdapter
from app.core.errors import EmbeddingError, SystemConfigError
from app.core.interfaces import VectorEngine

# 单次锁内 encode 的文本上限：把查询侧最坏等待限制在“一个子批”而非整批（5.3）。
_MAX_LOCK_BATCH = 16
# 并发冲突重试次数（不含首次）与退避基数；串行化后仍撞车属极小概率，重试兜底。
_CONCURRENCY_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 0.05


def _is_concurrency_error(exc: BaseException) -> bool:
    """识别 HF tokenizer 的并发借用冲突（AlreadyBorrowed 的字符串形式不稳定）。"""
    text = str(exc).lower()
    return "already borrowed" in text or "alreadyborrowed" in text


class LocalBGEV15Embedding(LocalModelAdapter, VectorEngine):
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        query_instruction: str = "为这个句子生成表示以用于检索相关文章：",
        device: str = "auto",          # auto|cpu|mps
        batch_size: int = 64,
        dimension: int = 0,            # 0 = 由模型自动识别
        model_factory: Any = None,     # 测试注入：() -> 带 encode/get_sentence_embedding_dimension 的桩
        **_: Any,
    ):
        super().__init__(model_name=model_name, device=device,
                         batch_size=batch_size, model_factory=model_factory)
        self.query_instruction = query_instruction
        self.configured_dimension = dimension
        self._dimension: int | None = dimension or None
        # 共享 SentenceTransformer 不是线程安全的：encode 与 tokenizer 计数必须共用同一把锁。
        self._model_lock = threading.RLock()

    # -- 基类钩子 --------------------------------------------------------------

    def _load_model(self) -> Any:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.model_name, device=self._resolve_device())

    def _post_load(self) -> None:
        _get_dim = getattr(self._model, "get_embedding_dimension", None) \
            or self._model.get_sentence_embedding_dimension
        actual = int(_get_dim())
        if self.configured_dimension and self.configured_dimension != actual:
            raise SystemConfigError(
                f"embedding.dimension={self.configured_dimension} 与模型实际维度 {actual} 不符")
        self._dimension = actual

    # -- 内部：串行化模型调用 --------------------------------------------------

    def _encode(self, sentences: list[str], **kwargs: Any) -> Any:
        """持锁调用 encode；并发借用冲突有限重试（5.3 并发安全）。"""
        self._ensure_model()
        for attempt in range(_CONCURRENCY_RETRIES + 1):
            try:
                with self._model_lock:
                    return self._model.encode(sentences, **kwargs)
            except Exception as exc:  # noqa: BLE001 — 仅并发冲突重试，其余原样抛出
                if not _is_concurrency_error(exc) or attempt >= _CONCURRENCY_RETRIES:
                    raise
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _embedding_error(prefix: str, exc: Exception) -> EmbeddingError:
        """并发冲突给出可行动文案；其他异常保持原样（5.3）。"""
        if _is_concurrency_error(exc):
            return EmbeddingError(
                f"{prefix}: {exc}（并发调用共享分词器冲突；服务已串行化嵌入调用并自动重试，"
                "仍失败请稍后重试该文件）")
        return EmbeddingError(f"{prefix}: {exc}")

    # -- 契约（3.1） ----------------------------------------------------------

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._ensure_model()
        return int(self._dimension)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本：不加查询指令（5.3 硬约束）。

        按 ≤16 条子批逐段持锁，避免一个 64 条批次长时间占锁拖慢并发查询（P13 NEW-01）。
        """
        items = list(texts)
        if not items:
            return []
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(items), _MAX_LOCK_BATCH):
                part = items[start:start + _MAX_LOCK_BATCH]
                encoded = self._encode(
                    part,
                    batch_size=min(self.batch_size, _MAX_LOCK_BATCH),
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
                vectors.extend(encoded.tolist())
            return vectors
        except EmbeddingError:
            raise
        except Exception as exc:
            raise self._embedding_error("文档向量化失败", exc) from exc

    def embed_query(self, text: str) -> list[float]:
        """向量化查询：自动加 query_instruction 前缀（BGE 中文模型必需）。"""
        try:
            return self._encode(
                [self.query_instruction + text],
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )[0].tolist()
        except EmbeddingError:
            raise
        except Exception as exc:
            raise self._embedding_error("查询向量化失败", exc) from exc

    # -- 可选能力（V5.2：分块精确计量） ----------------------------------------

    def count_tokens(self, text: str) -> int | None:
        """真实分词器 token 计数（不含 special tokens）；不可用/异常返回 None（调用方回退估算）。

        必须与 encode 共用同一把锁：tokenizer 也是 Rust 侧共享状态（P13 NEW-01）。
        """
        if not text:
            return 0
        try:
            self._ensure_model()
            tokenizer = getattr(self._model, "tokenizer", None)
            if tokenizer is None:
                return None
            with self._model_lock:
                encoded = tokenizer(text, add_special_tokens=False)
            ids = encoded.get("input_ids") if hasattr(encoded, "get") else None
            return len(ids) if ids is not None else None
        except Exception:  # noqa: BLE001 计量失败不阻塞入库（chunker 回退 ratio）
            return None
