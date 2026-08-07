"""问答（6.4）。W1 骨架：路由与请求模型就位，链路 W3 填实。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.errors import InternalError

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    """6.4 请求模型：filters 透传检索过滤条件；collection 指定知识库集合。"""

    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    top_k: int | None = None
    filters: dict | None = None
    collection: str | None = None
    debug: bool = False


@router.post("")
def query(payload: QueryRequest) -> dict:
    # W3 交付：Retriever → Evaluator →（改写循环）→ LLM → Detector
    raise InternalError("问答链路尚未实现（计划 W3 交付）")
