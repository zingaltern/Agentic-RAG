"""本地 BGE v1.5 嵌入（DEVELOPMENT.md 5.3）。

- 默认模型 BAAI/bge-small-zh-v1.5（稠密 512 维），首次加载联网下载到 HF 缓存。
- 硬约束：embed_query 自动拼 query_instruction 前缀；embed_documents 不加（BGE 中文特性）。
- normalize_embeddings=True（余弦相似即点积）。
- 懒加载/device 解析/异常包装复用 LocalModelAdapter 公共基类（13.11.2）。
- 配置维度与模型实际不符 → SystemConfigError（启动即错，禁止静默降级）。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import LocalModelAdapter
from app.core.errors import EmbeddingError, SystemConfigError
from app.core.interfaces import VectorEngine


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

    # -- 契约（3.1） ----------------------------------------------------------

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._ensure_model()
        return int(self._dimension)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本：不加查询指令（5.3 硬约束）。"""
        self._ensure_model()
        try:
            return self._model.encode(
                list(texts),
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            ).tolist()
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"文档向量化失败: {exc}") from exc

    def embed_query(self, text: str) -> list[float]:
        """向量化查询：自动加 query_instruction 前缀（BGE 中文模型必需）。"""
        self._ensure_model()
        try:
            return self._model.encode(
                [self.query_instruction + text],
                normalize_embeddings=True,
                convert_to_numpy=True,
            )[0].tolist()
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"查询向量化失败: {exc}") from exc

    # -- 可选能力（V5.2：分块精确计量） ----------------------------------------

    def count_tokens(self, text: str) -> int | None:
        """真实分词器 token 计数（不含 special tokens）；不可用/异常返回 None（调用方回退估算）。"""
        if not text:
            return 0
        try:
            self._ensure_model()
            tokenizer = getattr(self._model, "tokenizer", None)
            if tokenizer is None:
                return None
            encoded = tokenizer(text, add_special_tokens=False)
            ids = encoded.get("input_ids") if hasattr(encoded, "get") else None
            return len(ids) if ids is not None else None
        except Exception:  # noqa: BLE001 计量失败不阻塞入库（chunker 回退 ratio）
            return None
