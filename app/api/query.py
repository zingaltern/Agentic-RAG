"""问答（DEVELOPMENT.md 6.4、13.5、V5.1）。

- 全同步链路：OrchestrationService.run（内置全局/检索超时预算）。
- 错误映射：反思超限 → 21001（兜底文案）；超时 → 21002；LLM 失败 → 30001（统一异常处理器转包）。
- top_k 受 retriever.top_k 上限约束；filters/collection 透传并贯穿反思循环。
- 13.5 多知识库：可选 kb_id（与 collection 二选一，kb_id 优先，缺省 default），库不存在 → 40001。
- V5.1：读权限按 Authorizer.can_read_kb 校验。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import get_authorizer
from app.api.utils import fail, get_deps, ok
from app.core.errors import (
    BusyError,
    ContentModerationError,
    InjectionError,
    InternalError,
    RateLimitError,
    ValidationError,
)
from app.core.logging import get_logger, get_trace_id
from app.core.registry import Dependencies

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    """6.4 请求模型：filters 透传检索过滤条件；collection 指定知识库集合。"""

    query: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    history: list[dict] | None = None   # 会话内最近若干轮 {role: user|assistant, content}，供生成阶段理解上下文
    top_k: int | None = Field(default=None, ge=1)
    filters: dict | None = None
    collection: str | None = None
    kb_id: str | None = None        # 13.5：与 collection 二选一，kb_id 优先，缺省 default
    web_search: bool = False     # A5：联网搜索（前端开关；服务端 web.enabled 为全局默认）
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
def query(payload: QueryRequest, request: Request,
          deps: Dependencies = Depends(get_deps)) -> dict:
    """C3/C4（V5.1）：并发信号量 + 速率限制包裹（43001/43002），成功后进入查询链路。"""
    config = request.app.state.config
    sem = request.app.state.query_semaphore
    if not sem.acquire(timeout=config.orchestration.semaphore_acquire_timeout_seconds):
        raise BusyError()
    try:
        return _query_impl(payload, request, deps)
    finally:
        sem.release()


def _query_impl(payload: QueryRequest, request: Request,
                deps: Dependencies) -> dict:
    limiter = request.app.state.rate_limiters["query"]
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client):
        raise RateLimitError()
    if deps.orchestration is None:
        raise InternalError("问答链路尚未装配（W3 交付）")
    if deps.kbs is None:
        raise InternalError("知识库服务尚未装配")

    # 13.12.3 查询侧安全前置：audit=仅审计标记；reject=拦截（40002/40003）
    if deps.query_guard is not None:
        guard_result = deps.query_guard.check(payload.query)
        if guard_result.verdict == "blocked":
            if guard_result.blocked_by == "injection":
                raise InjectionError("您的输入疑似包含提示注入指令，已被拦截")
            raise ContentModerationError("您的输入未通过内容审核，已被拦截")
        if guard_result.verdict == "flag" and guard_result.hits:
            get_logger("query_guard").info(
                "query_security_audit",
                extra={"extra_fields": {"hits": guard_result.hits[:5],
                                        "trace_id": get_trace_id()}},
            )

    # 13.12.6 服务端会话解析（dialogue 未启用/无 session_id → 沿用客户端 history）
    resolved_history, session = _resolve_session(request, deps, payload, payload.query)

    # 13.5：kb_id 与 collection 二选一，kb_id 优先，缺省 default；目标库不存在 → 参数错误
    if payload.kb_id:
        info = deps.kbs.get(payload.kb_id)
        if info is None:
            raise ValidationError(f"知识库不存在: {payload.kb_id!r}")
        collection = info.collection
    elif payload.collection:
        info = next((k for k in deps.kbs.list() if k.collection == payload.collection), None)
        if info is None:
            raise ValidationError(f"知识库不存在: {payload.collection!r}")
        collection = info.collection
    else:
        info = deps.kbs.get("default")
        collection = deps.kbs.collection_of("default")
    # V5.1：读权限校验 —— 越权访问知识库统一 40101（S14：与上传/删除路径的鉴权语义一致）
    if not get_authorizer(request).can_read_kb(getattr(request.state, "user", None), info):
        from app.core.errors import AuthError

        raise AuthError(f"无权访问知识库: {info.kb_id!r}")

    # P1-3：索引 DNA 校验（audit=warning+响应标记；reject=30006 拦截）
    dna_mismatch = _check_index_dna(deps, collection)

    result = deps.orchestration.run(
        query=payload.query,
        filters=payload.filters,
        collection=collection,
        top_k=payload.top_k,
        debug=payload.debug,
        history=resolved_history,
        web_search=payload.web_search,
        allow_clarify=_session_allow_clarify(request, session),
    )
    # 13.12.6 问答结束回写会话（历史/澄清计数/最后提问）
    _update_session(request, deps, payload, session, result, payload.query)
    # S6 审计（13.11.1）：含 user_id/kb_id 与回答摘要，不含完整敏感文本与 key
    from app.api.auth import current_user

    get_logger("audit").info(
        "query_audit",
        extra={"extra_fields": {
            "user_id": current_user(request),
            "kb_id": payload.kb_id or "default",
            "collection": collection,
            "query_summary": payload.query[:80],
            "answer_summary": (result.text or "")[:120],
            "iterations": result.iterations,
            "code": result.code,
            "elapsed_ms": result.elapsed_ms,
            "trace_id": get_trace_id(),
        }},
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
    if dna_mismatch:
        data["index_dna_mismatch"] = True   # P1-3：索引 dna 与当前配置不一致（audit 模式标记）
    if result.code != 0:
        # 反思超限兜底：code=21001 + 兜底文案（6.4）
        return fail(result.code, result.message, data=data)
    return ok(data)


# ---------------------------------------------------------------------------
# 13.12.6 服务端会话（L2 对话状态引擎落地）
# ---------------------------------------------------------------------------

def _matches_any(text: str, keywords: list[str]) -> bool:
    return any(kw and kw in text for kw in keywords)


def _check_index_dna(deps: Dependencies, collection: str) -> bool:
    """P1-3：索引 DNA 校验（13.5）。

    - enabled=false → 不校验（现状行为）；
    - 存量集合无 dna（legacy）→ 仅日志提示，不告警不拒绝；
    - dna 不一致：audit（默认）warning + 返回 True（响应标记 index_dna_mismatch）；
      reject 抛 IndexDnaMismatchError（30006）。
    返回值：是否命中 mismatch（供响应标记）。
    """
    from app.core.index_dna import compute_index_dna
    from app.core.errors import IndexDnaMismatchError

    cfg = deps.config.index_dna
    if not cfg.enabled:
        return False
    getter = getattr(deps.vector_store, "get_collection_dna", None)
    if getter is None:          # 非 Chroma 存储无 dna 能力 → legacy 语义
        return False
    stored = getter(collection)
    if stored is None:          # 存量 legacy 集合：仅提示
        get_logger("query").info(
            "索引 DNA 缺失（存量 legacy 集合，不校验）",
            extra={"extra_fields": {"collection": collection, "trace_id": get_trace_id()}},
        )
        return False
    current = compute_index_dna(deps.config)["dna"]
    if stored == current:
        return False
    get_logger("query").warning(
        "索引 DNA 与当前配置不一致（换 embedding/分块/清洗配置后需重建索引）",
        extra={"extra_fields": {"collection": collection, "stored_dna": stored,
                                "current_dna": current, "trace_id": get_trace_id()}},
    )
    if cfg.mode == "reject":
        raise IndexDnaMismatchError(
            f"集合 {collection!r} 构建于 dna={stored}，当前配置 dna={current}")
    return True


def _resolve_session(request: Request, deps: Dependencies, payload: "QueryRequest",
                     query: str) -> tuple[list[dict], object | None]:
    """P2：服务端会话解析。

    dialogue 未启用 / 无 session_id / 会话存储未装配 → (客户端 history, None)（现状语义）；
    启用后：回溯关键词移除上一轮；新话题判定清空短期上下文；TTL 过期由 store 处理。
    """
    cfg = request.app.state.config.dialogue
    if not cfg.enabled or not payload.session_id or deps.sessions is None:
        return (payload.history or []), None
    store = deps.sessions
    session = store.get(payload.session_id) or store.new(payload.session_id)
    if cfg.session.enable_rollback and _matches_any(query, cfg.session.rollback_keywords):
        if session.history:
            session.history = session.history[:-1]   # 回溯：移除上一轮记录
    if session.last_query and deps.boundary is not None and cfg.boundary.enabled:
        try:
            if deps.boundary.is_new_topic(query, session.last_query):
                session.history = []                 # 新话题：清空短期上下文
        except Exception as exc:  # noqa: BLE001
            get_logger("dialogue").warning("会话边界判定异常（保留历史降级）：%s", exc)
    return list(session.history), session


def _session_allow_clarify(request: Request, session: object | None) -> bool:
    """13.12.5：连续反问达 clarification.max_rounds → 不再反问（转 21001 兜底）。"""
    if session is None:
        return True
    return getattr(session, "clarify_count", 0) < request.app.state.config.clarification.max_rounds


def _update_session(request: Request, deps: Dependencies, payload: "QueryRequest",
                    session: object | None, result: Any, query: str) -> None:
    """P2：问答结束后回写会话（历史 + 澄清计数 + 最后提问），并做轮数截断。"""
    if session is None or deps.sessions is None:
        return
    cfg = request.app.state.config.dialogue
    if result.text:
        session.history.append({"role": "user", "content": query})
        session.history.append({"role": "assistant", "content": result.text})
        max_rounds = cfg.session.max_rounds
        if max_rounds > 0 and len(session.history) > max_rounds * 2:
            session.history = session.history[-(max_rounds * 2):]
    if result.code == 0:
        session.clarify_count = 0          # 正常回答 → 澄清计数清零（连续反问重置）
    elif getattr(result, "need_clarify", False):
        session.clarify_count += 1
    session.last_query = query
    deps.sessions.put(session)
