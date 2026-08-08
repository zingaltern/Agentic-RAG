"""BGE 跨编码器重排器（DEVELOPMENT.md 13.2）。

- 模型：sentence-transformers.CrossEncoder（现有依赖，零新增包）；
  默认 BAAI/bge-reranker-v2-m3，配置改 model_name 可切 BAAI/bge-reranker-base。
- 对 (query, 候选块) 打分后按分数降序返回；输出 ScoredChunk.notion="rerank"。
- 懒加载 + 异常包装（EmbeddingError）；模型加载/推理失败不阻塞启动。
"""
from __future__ import annotations

from typing import Any

from app.core.domain import ScoredChunk
from app.core.errors import EmbeddingError
from app.core.interfaces import Reranker


class BGEReranker(Reranker):
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3",
                 batch_size: int = 32, device: str = "auto",
                 model_factory: Any = None, **_: Any):
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = device
        self._model_factory = model_factory       # 测试注入：() -> 带 predict 的桩
        self._model: Any = None

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            if self._model_factory is not None:
                self._model = self._model_factory()
            else:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self.model_name, device=self._resolve_device())
        except Exception as exc:
            self._model = None
            raise EmbeddingError(f"重排模型加载失败（{self.model_name}）: {exc}") from exc

    def ready(self) -> bool:
        self._ensure_model()
        return self._model is not None

    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        if not chunks:
            return []
        self._ensure_model()
        try:
            scores = self._model.predict(
                [(query, c.chunk.text) for c in chunks],
                batch_size=self.batch_size,
            )
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"重排打分失败: {exc}") from exc
        ordered = sorted(zip(chunks, [float(s) for s in scores]), key=lambda kv: kv[1], reverse=True)
        return [ScoredChunk(chunk=item.chunk, score=score, notion="rerank") for item, score in ordered]
