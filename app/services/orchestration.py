"""Agentic 编排服务（DEVELOPMENT.md 5.9）——反思循环。

```
iteration = 0
normalized = QueryNormalize(query)
while True:
    chunks = retriever.retrieve(normalized, k=top_k, filters)
    result = evaluator.evaluate(normalized, chunks)
    if result.passed:
        context = backtrack_parent_chunks(chunks[:rerank_top_k])
        answer = llm.chat(system+user prompt)         # 5.7 prompt 原样
        detection = detector.detect(answer, context)
        return Answer(...)
    elif iteration < max_reflection_iterations:
        iteration += 1
        normalized = rewriter.rewrite(normalized, result.reason)   # 改写后继续，filters 保持
    else:
        return FallbackAnswer(code=21001)
```

- 安全阀：整体 request_timeout_seconds（默认 30s）与检索轮次 search_timeout_seconds（10s）
  采用时间预算检查（同步实现无法强制中断外部 I/O，每阶段校验 elapsed 预算），超时抛 RequestTimeoutError(21002)。
- filters/collection 贯穿整个循环；collection 非默认 → ValidationError（MVP 单库）。
- sources[].filename = 入库时 Chunk.metadata.source 原始文件名（6.4 关键契约）。
- 每阶段输出结构化日志（iteration、模块耗时）→ UI debug_trace 面板。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import SystemConfig
from app.core.domain import DetectionResult, EvaluationResult, ScoredChunk
from app.core.errors import LLMUnavailableError, RequestTimeoutError, ValidationError
from app.core.interfaces import (
    Evaluator,
    HallucinationDetector,
    LLM,
    QueryRewriter,
    Reranker,
    Retriever,
    VectorStore,
)
from app.core.logging import get_logger, get_trace_id
from app.adapters.rewriter.rule_rewriter import normalize_query

logger = get_logger("orchestration")

_SOURCE_TEXT_LIMIT = 300          # sources[].chunk 文本截断长度（防响应膨胀，模块内常量）
FALLBACK_TEXT = "抱歉，暂未找到相关答案…"

SYSTEM_PROMPT = (
    "你是一个企业知识库问答助手。只基于提供的知识库上下文回答；"
    "上下文不足时明确说明'知识库中未找到相关依据'。"
    "回答末尾必须为引用对应上下文编号，使用 [N]（N 为正整数）标注来源。"
)
USER_PROMPT = "上下文：\n{context}\n\n问题：{query}"


@dataclass
class Answer:
    """编排结果（code=0 正常；code=21001 反思超限兜底）。"""

    text: str
    sources: list[dict] = field(default_factory=list)
    iterations: int = 0
    hallucination: DetectionResult | None = None
    debug_trace: list[dict] = field(default_factory=list)
    code: int = 0
    message: str = "ok"
    elapsed_ms: int = 0


class OrchestrationService:
    def __init__(self, config: SystemConfig, retriever: Retriever,
                 reranker: Reranker, evaluator: Evaluator,
                 rewriter: QueryRewriter, detector: HallucinationDetector,
                 llm: LLM | None, vector_store: VectorStore,
                 **_: Any):
        self.config = config
        self.retriever = retriever
        self.reranker = reranker
        self.evaluator = evaluator
        self.rewriter = rewriter
        self.detector = detector
        self.llm = llm
        self.vector_store = vector_store

    # -- 工具 ----------------------------------------------------------------

    @staticmethod
    def _check_budget(start: float, timeout_seconds: int | float) -> None:
        if time.perf_counter() - start > timeout_seconds:
            raise RequestTimeoutError(f"请求超过 {timeout_seconds}s 预算，强制终止")

    def _parent_map(self) -> dict[str, ScoredChunk]:
        """父块索引（子块回溯父块全文用；W2 只存父块，命中即父块，此步兼容预留）。"""
        getter = getattr(self.vector_store, "get_all_chunks", None)
        if getter is None:
            return {}
        collection = self.config.vector_store.collection
        return {c.chunk_id: ScoredChunk(chunk=c, score=0.0, notion="parent") for c in getter(collection)}

    def _backtrack_parent_chunks(self, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        """5.9：检索命中子块时回溯其父块全文进入 LLM 上下文。"""
        parents = self._parent_map()
        if not parents:
            return chunks
        seen: set[str] = set()
        out: list[ScoredChunk] = []
        for item in chunks:
            target_id = item.chunk.parent_id or item.chunk.chunk_id
            parent = parents.get(target_id)
            if parent is None or parent.chunk.chunk_id in seen:
                continue
            seen.add(parent.chunk.chunk_id)
            out.append(ScoredChunk(chunk=parent.chunk, score=item.score, notion=item.notion))
        return out or chunks

    @staticmethod
    def _sources(context: list[ScoredChunk]) -> list[dict]:
        """6.4：sources[].filename 必须等于入库时 Chunk.metadata.source 原始文件名。"""
        out: list[dict] = []
        for item in context:
            meta = item.chunk.metadata
            out.append({
                "document_id": item.chunk.document_id,
                "filename": meta.get("source") or item.chunk.document_id,
                "page": meta.get("page"),
                "chunk": item.chunk.text[:_SOURCE_TEXT_LIMIT],
                "score": round(item.score, 4),
            })
        return out

    # -- 主流程 --------------------------------------------------------------

    def run(self, query: str, filters: dict | None = None,
            collection: str | None = None,
            top_k: int | None = None, debug: bool = False) -> Answer:
        start = time.perf_counter()
        if collection and collection != self.config.vector_store.collection:
            raise ValidationError(
                f"暂不支持知识库集合 {collection!r}（MVP 仅 {self.config.vector_store.collection!r}）"
            )
        if self.llm is None:
            raise LLMUnavailableError("LLM 未装配：请设置 RAG_DEEPSEEK_API_KEY 后重启服务")

        cfg = self.config.orchestration
        retriever_cfg = self.config.retriever
        requested_k = top_k or retriever_cfg.top_k
        k = max(1, min(requested_k, retriever_cfg.top_k))     # 6.4：top_k 受 retriever.top_k 上限约束
        filters = filters or {}

        normalized = normalize_query(query)
        iteration = 0
        trace: list[dict] = []

        while True:
            self._check_budget(start, cfg.request_timeout_seconds)

            # 1) 检索（每轮独立预算）
            search_start = time.perf_counter()
            chunks = self.retriever.retrieve(normalized, k=k, filters=filters)
            self._check_budget(start, cfg.request_timeout_seconds)
            if time.perf_counter() - search_start > cfg.search_timeout_seconds:
                raise RequestTimeoutError(f"检索超过 {cfg.search_timeout_seconds}s 预算")

            # 2) 评估
            eval_start = time.perf_counter()
            result = self.evaluator.evaluate(normalized, chunks)
            eval_ms = round((time.perf_counter() - eval_start) * 1000)

            logger.info(
                "反思迭代",
                extra={"extra_fields": {
                    "iteration": iteration, "query": normalized,
                    "evaluator_score": result.score, "reason": result.reason,
                    "candidates": len(chunks), "eval_ms": eval_ms, "trace_id": get_trace_id(),
                }},
            )
            trace.append({
                "iter": iteration,
                "query": normalized,
                "evaluator_score": result.score,
                "reason": result.reason,
                "candidates": len(chunks),
            })

            if result.passed:
                # 3) 上下文：rerank_top_k 内子块回溯父块（5.9）
                context = self._backtrack_parent_chunks(
                    self.reranker.rerank(normalized, chunks[:self.config.reranker.rerank_top_k])
                )
                numbered = "\n".join(f"{i}. {c.chunk.text}" for i, c in enumerate(context, 1))
                user_prompt = USER_PROMPT.format(context=numbered, query=query)

                # 4) 生成
                llm_start = time.perf_counter()
                answer_text = self.llm.chat(
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user_prompt}],
                    temperature=self.config.llm.temperature,
                    max_tokens=self.config.llm.max_tokens,
                )
                llm_ms = round((time.perf_counter() - llm_start) * 1000)

                # 5) 幻觉检测
                detection = self.detector.detect(answer_text, context)
                elapsed_ms = round((time.perf_counter() - start) * 1000)
                trace.append({"iter": iteration, "llm_ms": llm_ms,
                              "detection": {"verified": detection.verified,
                                            "verified_rate": detection.verified_rate}})
                return Answer(
                    text=answer_text,
                    sources=self._sources(context),
                    iterations=iteration,
                    hallucination=detection,
                    debug_trace=trace if debug else [],
                    elapsed_ms=elapsed_ms,
                )

            # 6) 改写后继续（filters 保持贯穿）
            if iteration < cfg.max_reflection_iterations:
                iteration += 1
                rewritten = self.rewriter.rewrite(normalized, result)
                logger.info(
                    "查询改写",
                    extra={"extra_fields": {"iteration": iteration, "before": normalized,
                                            "after": rewritten, "trace_id": get_trace_id()}},
                )
                trace.append({"iter": iteration, "rewritten": rewritten})
                normalized = rewritten
            else:
                # 兜底：反思达上限（5.9 → 21001）
                elapsed_ms = round((time.perf_counter() - start) * 1000)
                return Answer(text=FALLBACK_TEXT, iterations=iteration,
                              debug_trace=trace if debug else [],
                              code=21001, message="反思达到上限仍未找到可靠答案",
                              elapsed_ms=elapsed_ms)
