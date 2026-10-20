"""kb_outline 工具（DEVELOPMENT.md 17.4，默认关）：按 section/page 聚合大纲。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import ToolContext, ToolResult
from app.core.interfaces import Tool


class KbOutlineArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128)
    max_sections: int = Field(default=40, ge=1, le=100)


class KbOutlineTool(Tool):
    name = "kb_outline"
    description = (
        "读取某份文档的章节/页码大纲。需要先定位“第几章/哪一节”再深入检索时使用；"
        "只有文档包含 section/page 元数据时才有意义。"
    )
    args_model = KbOutlineArgs
    timeout_ms = 3000
    max_result_chars = 4000
    side_effect = "none"

    def __init__(self, vector_store: Any = None, max_sections: int = 40, **_: Any):
        self.vector_store = vector_store
        self.max_sections = max(1, max_sections)

    def invoke(self, ctx: ToolContext, args: KbOutlineArgs) -> ToolResult:
        if self.vector_store is None or not hasattr(self.vector_store, "get_all_chunks"):
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="当前向量库不支持大纲读取",
                              error_code="TOOL_UNAVAILABLE")
        try:
            chunks = [c for c in self.vector_store.get_all_chunks(ctx.collection)
                      if c.document_id == args.document_id]
        except Exception as exc:  # noqa: BLE001
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"读取大纲失败：{type(exc).__name__}",
                              error_code="TOOL_EXECUTION_ERROR", retryable=True)
        if not chunks:
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"未找到文档：{args.document_id}",
                              error_code="TOOL_NOT_FOUND")
        chunks.sort(key=lambda c: c.order)
        limit = max(1, min(args.max_sections or self.max_sections, self.max_sections))
        groups: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for c in chunks:
            meta = c.metadata or {}
            section = str(meta.get("section") or "").strip()
            key = section or f"第 {c.order + 1} 块"
            page = meta.get("page") if isinstance(meta.get("page"), int) else None
            if current is None or current["section"] != key:
                current = {"section": key, "page_start": page, "page_end": page,
                           "chunk_start": c.order, "chunk_end": c.order}
                groups.append(current)
            else:
                current["chunk_end"] = c.order
                if page is not None:
                    current["page_end"] = page
            if len(groups) >= limit:
                break
        return ToolResult(call_id="", name=self.name, ok=True,
                          summary=f"文档 {args.document_id} 共 {len(groups)} 个章节/分组",
                          data={"document_id": args.document_id, "sections": groups,
                                "truncated": len(groups) >= limit},
                          evidence=[])
