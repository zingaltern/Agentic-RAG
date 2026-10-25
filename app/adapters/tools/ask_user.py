"""ask_user 工具（DEVELOPMENT.md 17.4）：主动澄清的终止信号。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import ToolContext, ToolResult
from app.core.interfaces import Tool


class AskUserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=80)
    options: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(default="", max_length=120)


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "当用户意图不清、关键槽位缺失或存在多个无法消歧的候选时，向用户提出一个简短澄清问题。"
        "调用后本轮结束；不要用它代替检索或逃避回答。"
    )
    args_model = AskUserArgs
    timeout_ms = 1000
    max_result_chars = 1000
    side_effect = "none"

    def invoke(self, ctx: ToolContext, args: AskUserArgs) -> ToolResult:
        options = [o.strip()[:40] for o in (args.options or []) if o and o.strip()][:5]
        return ToolResult(
            call_id="", name=self.name, ok=True,
            summary="需要用户澄清：" + args.question,
            data={"clarify": True, "question": args.question,
                  "options": options, "reason": args.reason},
        )
