"""kb_list_documents 工具（DEVELOPMENT.md 17.4）：列当前知识库文档。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import ToolContext, ToolResult
from app.core.interfaces import Tool
from app.core.logging import get_logger

logger = get_logger("tools.kb_list")


class KbListDocumentsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_contains: str | None = Field(default=None, max_length=100,
                                      description="按文件名包含过滤，如“员工手册”")
    limit: int = Field(default=20, ge=1, le=50)


class KbListDocumentsTool(Tool):
    name = "kb_list_documents"
    description = (
        "列出当前知识库中的文档（可带文件名过滤）。"
        "用户问“有哪些文档/哪份制度/知识库里有什么”或需要确认文档是否存在时使用；"
        "不要用它代替 kb_search 找具体事实。"
    )
    args_model = KbListDocumentsArgs
    timeout_ms = 2000
    max_result_chars = 4000
    side_effect = "none"

    def __init__(self, documents: Any, max_items: int = 50, **_: Any):
        self.documents = documents
        self.max_items = max(1, max_items)

    def invoke(self, ctx: ToolContext, args: KbListDocumentsArgs) -> ToolResult:
        if self.documents is None:
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="文档索引不可用",
                              error_code="TOOL_UNAVAILABLE")
        limit = max(1, min(args.limit or self.max_items, self.max_items))
        try:
            items = self.documents.list(page=1, size=max(limit * 2, limit),
                                        kb_id=ctx.kb_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb_list_documents 读取失败：%s", exc)
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"读取文档列表失败：{type(exc).__name__}",
                              error_code="TOOL_EXECUTION_ERROR", retryable=True)
        needle = (args.name_contains or "").strip()
        if needle:
            items = [d for d in items if needle in str(d.get("filename", ""))]
        items = items[:limit]
        pretty = [
            {"document_id": d.get("document_id"), "filename": d.get("filename"),
             "chunk_count": d.get("chunk_count"), "page_count": d.get("page_count")}
            for d in items
        ]
        return ToolResult(call_id="", name=self.name, ok=True,
                          summary=(f"当前知识库共匹配 {len(pretty)} 份文档"
                                   if pretty else "当前知识库中没有匹配的文档"),
                          data={"documents": pretty, "count": len(pretty)},
                          evidence=[])
