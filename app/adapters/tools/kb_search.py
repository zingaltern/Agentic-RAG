"""kb_search 工具（DEVELOPMENT.md 17.4）：复用混合检索 + 精排，产出证据。"""
from __future__ import annotations

import inspect
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import Evidence, ScoredChunk, ToolContext, ToolResult
from app.core.interfaces import Reranker, Retriever, Tool
from app.core.logging import get_logger

logger = get_logger("tools.kb_search")


class KbSearchArgs(BaseModel):
    """模型可填参数；collection/filters 不在其中（服务端注入）。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=500,
                       description="检索查询，使用关键实体与限定词；不要传空字符串")
    k: int | None = Field(default=None, ge=1, le=20, description="返回条数（1~20）；缺省用配置默认值")
    path: Literal["hybrid", "bm25", "vector"] = Field(
        default="hybrid", description="检索路径；不确定时用 hybrid")


class KbSearchTool(Tool):
    name = "kb_search"
    description = (
        "在企业知识库中检索与问题相关的片段。需要事实、定义、条款、数字或流程时使用；"
        "query 要包含关键实体与限定词。返回证据 ID、来源、页码与短片段；"
        "摘要不足时可用 kb_fetch 读取某条证据的父块全文。"
    )
    args_model = KbSearchArgs
    timeout_ms = 5000
    max_result_chars = 4000
    side_effect = "none"

    def __init__(self, retriever: Retriever, reranker: Reranker | None = None,
                 default_k: int = 8, max_k: int = 20, **_: Any):
        self.retriever = retriever
        self.reranker = reranker
        self.default_k = max(1, default_k)
        self.max_k = max(1, max_k)
        self._supports_scope = self._probe_scope_support()

    def _probe_scope_support(self) -> bool:
        """检查 Retriever 是否显式支持 path/collection。

        不能依赖“捕获 TypeError 后三参重试”：三参调用会走默认库，造成跨库泄露。
        仅当签名显式包含 collection 与 path 时才认为支持；**kwargs 也不算。
        """
        retrieve = getattr(self.retriever, "retrieve", None)
        if not callable(retrieve):
            return False
        try:
            params = inspect.signature(retrieve).parameters
        except (TypeError, ValueError):
            return False
        return "collection" in params and "path" in params

    def invoke(self, ctx: ToolContext, args: KbSearchArgs) -> ToolResult:
        k = max(1, min(args.k or self.default_k, self.max_k))
        if not self._supports_scope:
            logger.warning("kb_search 拒绝无作用域 Retriever：%s", type(self.retriever).__name__)
            return ToolResult(
                call_id="", name=self.name, ok=False,
                summary="检索器不支持 collection/path 作用域，已拒绝默认库检索",
                error_code="TOOL_SCOPE_DENIED",
                error_message="retriever.retrieve 必须显式支持 collection 与 path",
                retryable=False)
        try:
            chunks = self.retriever.retrieve(
                args.query, k=k, filters=ctx.filters,
                path=args.path, collection=ctx.collection)
        except TypeError as exc:
            # TypeError 可能是实现缺陷，也可能是旧桩伪装；一律结构化失败，绝不无作用域重试。
            logger.warning("kb_search 作用域检索 TypeError：%s", exc)
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="检索器作用域参数调用失败，已拒绝回退默认库",
                              error_code="TOOL_EXECUTION_ERROR",
                              error_message=str(exc)[:300], retryable=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb_search 检索失败：%s", exc)
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"检索失败：{type(exc).__name__}",
                              error_code="TOOL_EXECUTION_ERROR",
                              error_message=str(exc)[:300], retryable=True)

        if self.reranker is not None and chunks:
            try:
                chunks = self.reranker.rerank(args.query, chunks)
            except Exception as exc:  # noqa: BLE001
                logger.warning("kb_search 精排失败，使用原始排序：%s", exc)
        top: list[ScoredChunk] = list(chunks[:k])
        max_raw = max((float(item.score) for item in top), default=0.0)
        evidence: list[Evidence] = []
        for rank, item in enumerate(top):
            meta = item.chunk.metadata or {}
            # 不同 reranker 的分值尺度不同（RRF ~0.01，CrossEncoder 可能为负）；
            # API 仍返回原始 score，另给 metadata.relevance（按名次的 0~1 相关度）
            # 供跨 provider 的统一阈值使用。
            relevance = 1.0 / (rank + 1.0)
            raw = float(item.score)
            # 跨 provider 的相对分：同批最高分归一化；max_raw<=0（如负分 CrossEncoder）
            # 时退回名次 relevance，避免除零/负值反转。
            normalized = (raw / max_raw) if max_raw > 0 else relevance
            normalized = max(0.0, min(1.0, normalized))
            evidence.append(Evidence(
                chunk_id=item.chunk.chunk_id,
                document_id=item.chunk.document_id,
                text=item.chunk.text,
                source=str(meta.get("source") or item.chunk.document_id),
                page=meta.get("page") if isinstance(meta.get("page"), int) else None,
                section=meta.get("section"),
                score=float(item.score),
                retrieval_query=args.query,
                tool_name=self.name,
                metadata={"path": args.path, "notion": item.notion,
                          "relevance": relevance, "normalized_score": normalized,
                          "raw_score": raw},
            ))
        summary = (f"检索到 {len(evidence)} 条与「{args.query}」相关的证据"
                   if evidence else f"未检索到与「{args.query}」相关的证据")
        return ToolResult(
            call_id="", name=self.name, ok=True, summary=summary,
            data={"query": args.query, "count": len(evidence), "path": args.path},
            evidence=evidence,
        )
