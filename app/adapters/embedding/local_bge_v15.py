"""本地 BGE v1.5 嵌入（DEVELOPMENT.md 5.3）。

- 默认模型 BAAI/bge-small-zh-v1.5（稠密 512 维），首次加载联网下载到 HF 缓存。
- 硬约束：embed_query 自动拼 query_instruction 前缀；embed_documents 不加（BGE 中文特性）。
- normalize_embeddings=True（余弦相似即点积）。
- 模型懒加载：首个 embed/ready() 调用时才下载加载；配置维度与模型实际不符 → SystemConfigError。
- 外部 I/O（模型加载/推理）统一包装为 EmbeddingError（1.3）。
"""
from __future__ import annotations

from typing import Any

from app.core.errors import EmbeddingError, SystemConfigError
from app.core.interfaces import VectorEngine


class LocalBGEV15Embedding(VectorEngine):
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
        self.model_name = model_name
        self.query_instruction = query_instruction
        self.device = device
        self.batch_size = batch_size
        self.configured_dimension = dimension
        self._model_factory = model_factory
        self._model: Any = None
        self._dimension: int | None = dimension or None

    # -- 工具 ----------------------------------------------------------------

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _ensure_model(self) -> None:
        """懒加载模型（首次联网下载），并校验维度一致性（5.3 对齐硬约束）。"""
        if self._model is not None:
            return
        try:
            if self._model_factory is not None:
                self._model = self._model_factory()
            else:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, device=self._resolve_device())
            _get_dim = getattr(self._model, "get_embedding_dimension", None) \
                or self._model.get_sentence_embedding_dimension
            actual = int(_get_dim())
        except SystemConfigError:
            raise
        except Exception as exc:
            self._model = None
            raise EmbeddingError(f"嵌入模型加载失败（{self.model_name}）: {exc}") from exc
        if self.configured_dimension and self.configured_dimension != actual:
            self._model = None
            raise SystemConfigError(
                f"embedding.dimension={self.configured_dimension} 与模型实际维度 {actual} 不符"
            )
        self._dimension = actual

    def ready(self) -> bool:
        """探活：确认模型已加载（供 /api/health 使用）。"""
        self._ensure_model()
        return self._model is not None

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
