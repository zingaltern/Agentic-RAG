"""问答（DEVELOPMENT.md 6.4）。

- 全同步链路：OrchestrationService.run（内置全局/检索超时预算）。
- 错误映射：反思超限 → 21001（兜底文案）；超时 → 21002；LLM 失败 → 30001（统一异常处理器转包）。
- top_k 受 retriever.top_k 上限约束；filters/collection 透传并贯穿反思循环。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.utils import fail, get_deps, ok
from app.core.errors import InternalError
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    """6.4 请求模型：filters 透传检索过滤条件；collection 指定知识库集合。"""

    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    top_k: int | None = Field(default=None, ge=1)
    filters: dict | None = None
    collection: str | None = None
    debug: bool = False


def _detection_payload(answer) -> dict:
    """6.4：citations.used 由回答文本中的 [N] 引用标号提取；confirmed = 已证率。"""
    import re as _re

    if answer.hallucination is None:
        return {"citations": {"used": [], "confirmed": 0.0},
                "hallucination_check": "suspected"}
    used = sorted({f"[{n}]" for n in _re.findall(r"\[(\d+)\]", answer.text)})
    return {
        "citations": {"used": used, "confirmed": answer.hallucination.verified_rate},
        "hallucination_check": "passed" if answer.hallucination.verified else "suspected",
    }


@router.post("")
def query(payload: QueryRequest, deps: Dependencies = Depends(get_deps)) -> dict:
    if deps.orchestration is None:
        raise InternalError("问答链路尚未装配（W3 交付）")

    result = deps.orchestration.run(
        query=payload.query,
        filters=payload.filters,
        collection=payload.collection,
        top_k=payload.top_k,
        debug=payload.debug,
    )
    det = _detection_payload(result)
    data = {
        "answer": result.text,
        "sources": result.sources,
        "citations": det["citations"],
        "hallucination_check": det["hallucination_check"],
        "iterations": result.iterations,
        "elapsed_ms": result.elapsed_ms,
    }
    if payload.debug and result.debug_trace:
        data["debug_trace"] = result.debug_trace
    if result.code != 0:
        # 反思超限兜底：code=21001 + 兜底文案（6.4）
        return fail(result.code, result.message, data=data)
    return ok(data)
