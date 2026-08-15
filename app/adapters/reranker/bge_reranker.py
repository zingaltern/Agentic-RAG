"""BGE 跨编码器重排器（DEVELOPMENT.md 13.2）。

- 模型：sentence-transformers.CrossEncoder（现有依赖，零新增包）；
  默认 BAAI/bge-reranker-base，配置改 model_name 可切 BAAI/bge-reranker-v2-m3。
- 对 (query, 候选块) 打分后按分数降序返回；输出 ScoredChunk.notion="rerank"。
- 懒加载/device 解析/异常包装复用 LocalModelAdapter 公共基类（13.11.2）。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import LocalModelAdapter
from app.core.domain import ScoredChunk
from app.core.errors import EmbeddingError, RerankError
from app.core.interfaces import Reranker


class BGEReranker(LocalModelAdapter, Reranker):
    def __init__(self, model_name: str = "BAAI/bge-reranker-base",
                 batch_size: int = 32, device: str = "auto",
                 model_factory: Any = None, **_: Any):
        super().__init__(model_name=model_name, device=device,
                         batch_size=batch_size, model_factory=model_factory)

    # -- 基类钩子 --------------------------------------------------------------

    def _load_model(self) -> Any:
        from sentence_transformers import CrossEncoder

        return CrossEncoder(self.model_name, device=self._resolve_device())

    # -- 契约（3.4） ----------------------------------------------------------

    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        if not chunks:
            return []
        self._ensure_model()
        try:
            scores = self._model.predict(
                [(query, c.chunk.text) for c in chunks],
                batch_size=self.batch_size,
            )
        except (EmbeddingError, RerankError):
            raise
        except Exception as exc:
            raise RerankError(f"重排打分失败: {exc}") from exc
        ordered = sorted(zip(chunks, [float(s) for s in scores]), key=lambda kv: kv[1], reverse=True)
        return [ScoredChunk(chunk=item.chunk, score=score, notion="rerank") for item, score in ordered]
