"""kb_fetch 工具（DEVELOPMENT.md 17.4）：按 chunk/document 读取父块或原文。"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.domain import Evidence, ToolContext, ToolResult
from app.core.interfaces import Tool, VectorStore
from app.core.logging import get_logger

logger = get_logger("tools.kb_fetch")


def _source_stem(value: str) -> str:
    """来源文件名/URL 末段去扩展名（小写）；用于 document_id 填成文件名时的兜底。"""
    name = str(value or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name).lower()


class KbFetchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str | None = Field(default=None, max_length=128,
                                 description="证据块 ID，来自 kb_search 返回的 evidence[].chunk_id")
    document_id: str | None = Field(default=None, max_length=128,
                                    description="文档 ID，来自 kb_list_documents 返回")
    evidence_id: str | None = Field(
        default=None, max_length=32,
        description="可选：证据编号 E1/E2（来自 <evidence id=...>）；服务端会反查 chunk_id")
    max_chars: int = Field(default=4000, ge=200, le=8000)

    @model_validator(mode="after")
    def _need_one(self) -> "KbFetchArgs":
        if not self.chunk_id and not self.document_id and not self.evidence_id:
            raise ValueError("chunk_id、evidence_id 与 document_id 至少提供一个")
        return self


class KbFetchTool(Tool):
    name = "kb_fetch"
    description = (
        "读取某条证据的完整父块，或某份文档的前段全文。"
        "当 kb_search 的短片段不足以回答、需要上下文/相邻条款时使用。"
        "优先用 evidence 标签里的 chunk_id；也可传 evidence_id（E1/E2）；"
        "只有知道 document_id 时才用文档模式。"
    )
    args_model = KbFetchArgs
    timeout_ms = 4000
    max_result_chars = 8000
    side_effect = "none"

    def __init__(self, vector_store: VectorStore | None, max_chars: int = 4000, **_: Any):
        self.vector_store = vector_store
        self.max_chars = max(200, max_chars)

    def invoke(self, ctx: ToolContext, args: KbFetchArgs) -> ToolResult:
        if self.vector_store is None or not hasattr(self.vector_store, "get_all_chunks"):
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="当前向量库不支持按块读取",
                              error_code="TOOL_UNAVAILABLE", retryable=False)
        try:
            chunks = list(self.vector_store.get_all_chunks(ctx.collection))
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb_fetch 读取失败：%s", exc)
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"读取失败：{type(exc).__name__}",
                              error_code="TOOL_EXECUTION_ERROR",
                              error_message=str(exc)[:300], retryable=True)

        limit = max(200, min(args.max_chars or self.max_chars, self.max_chars))
        chunk_id = args.chunk_id
        if args.evidence_id:
            chunk_id = (getattr(ctx, "evidence_index", {}) or {}).get(args.evidence_id)
            if not chunk_id:
                return ToolResult(call_id="", name=self.name, ok=False,
                                  summary=f"未找到证据：{args.evidence_id}（当前账本无此编号）",
                                  error_code="TOOL_NOT_FOUND")
        if chunk_id:
            target = next((c for c in chunks if c.chunk_id == chunk_id), None)
            if target is None:
                # 兼容旧模型把 E1/E2 填进 chunk_id：仅在账本确有该编号时反查。
                mapped = (getattr(ctx, "evidence_index", {}) or {}).get(chunk_id)
                if mapped:
                    chunk_id = mapped
                    target = next((c for c in chunks if c.chunk_id == chunk_id), None)
            if target is None:
                return ToolResult(call_id="", name=self.name, ok=False,
                                  summary=f"未找到证据块：{chunk_id}",
                                  error_code="TOOL_NOT_FOUND")
            selected = target
            if target.parent_id:
                parent = next((c for c in chunks if c.chunk_id == target.parent_id), None)
                if parent is not None:
                    selected = parent
            meta = selected.metadata or {}
            evidence = Evidence(
                chunk_id=chunk_id,
                document_id=selected.document_id,
                text=selected.text[:limit],
                source=str(meta.get("source") or selected.document_id),
                page=meta.get("page") if isinstance(meta.get("page"), int) else None,
                section=meta.get("section"),
                score=0.0,
                retrieval_query=chunk_id or "",
                tool_name=self.name,
                metadata={"mode": "chunk", "parent_id": selected.chunk_id},
            )
            return ToolResult(call_id="", name=self.name, ok=True,
                              summary=f"已读取证据 {chunk_id} 的完整内容",
                              data={"mode": "chunk", "chunk_id": chunk_id,
                                    "chars": len(evidence.text)},
                              evidence=[evidence])

        doc_chunks = sorted((c for c in chunks if c.document_id == args.document_id),
                            key=lambda c: c.order)
        if not doc_chunks and args.document_id:
            # 模型常把文档名（员工手册.md）填进 document_id；按 source 末段兜底匹配，
            # 但只做确定性文件名匹配，不接受任意路径/URL 读取。
            wanted = _source_stem(args.document_id)
            if wanted:
                doc_chunks = sorted(
                    (c for c in chunks
                     if _source_stem((c.metadata or {}).get("source") or "") == wanted),
                    key=lambda c: c.order)
        if not doc_chunks:
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"未找到文档：{args.document_id}",
                              error_code="TOOL_NOT_FOUND")
        text_parts: list[str] = []
        used = 0
        for c in doc_chunks:
            if used >= limit:
                break
            piece = c.text
            if used + len(piece) > limit:
                piece = piece[: max(0, limit - used)]
            if piece:
                text_parts.append(piece)
                used += len(piece)
        first = doc_chunks[0]
        meta = first.metadata or {}
        text = "\n".join(text_parts)
        evidence = Evidence(
            chunk_id=first.chunk_id,
            document_id=first.document_id,
            text=text,
            source=str(meta.get("source") or first.document_id),
            page=meta.get("page") if isinstance(meta.get("page"), int) else None,
            section=meta.get("section"),
            score=0.0,
            retrieval_query=args.document_id or "",
            tool_name=self.name,
            metadata={"mode": "document", "chunks": len(text_parts)},
        )
        return ToolResult(call_id="", name=self.name, ok=True,
                          summary=f"已读取文档 {first.document_id} 的前 {len(text_parts)} 块",
                          data={"mode": "document", "document_id": first.document_id,
                                "chunks": len(text_parts), "chars": len(text)},
                          evidence=[evidence], truncated=used >= limit)
