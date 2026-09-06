"""插件注册与装配（DEVELOPMENT.md 5.11）。

- PluginRegistry："{module}.{provider}" → 实现类的注册表（各实现类位于 app/adapters/）。
- wire(config) -> Dependencies：启动时按 provider 实例化各模块默认实现。
  llm 装配容错：未配置 API Key / provider 未实现 → llm=None 优雅降级（health 显示 not_loaded/degraded），
  不阻塞启动；调用方（编排/路由）访问时再抛明确错误。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from app.core.errors import SystemConfigError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import SystemConfig
    from app.core.interfaces import (
        BoundaryDetector,
        Evaluator,
        HallucinationDetector,
        LLM,
        QueryNormalizer,
        QueryRewriter,
        Reranker,
        Retriever,
        VectorEngine,
        VectorStore,
    )
    from app.services.documents_store import DocumentsStore
    from app.services.ingestion import IngestionPipeline
    from app.services.jobs_store import JobStore
    from app.services.orchestration import OrchestrationService
    from app.services.session_store import SessionStore

logger = get_logger("registry")


class PluginRegistry:
    """类级注册表：register(name, impl) / get(name, **cfg)。"""

    _registry: ClassVar[dict[str, type]] = {}

    @classmethod
    def register(cls, name: str, impl: type) -> None:
        cls._registry[name] = impl

    @classmethod
    def get(cls, name: str, **cfg: Any) -> Any:
        if name not in cls._registry:
            raise KeyError(f"未注册的插件: {name!r}")
        return cls._registry[name](**cfg)

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()


@dataclass
class Dependencies:
    """运行时装配的组件集合（wire 产物）；未装配项为 None（健康检查据此判定 not_loaded）。"""

    config: "SystemConfig"
    llm: "LLM | None" = None
    embedding: "VectorEngine | None" = None
    vector_store: "VectorStore | None" = None
    retriever: "Retriever | None" = None
    reranker: "Reranker | None" = None
    evaluator: "Evaluator | None" = None
    rewriter: "QueryRewriter | None" = None
    detector: "HallucinationDetector | None" = None
    orchestration: "OrchestrationService | None" = None
    jobs: "JobStore | None" = None
    documents: "DocumentsStore | None" = None
    pipeline: "IngestionPipeline | None" = None
    kbs: "KbStore | None" = None
    users: "UserStore | None" = None
    # —— 13.12 输入侧治理（默认关时均为 None，零开销）——
    normalizer: "QueryNormalizer | None" = None
    boundary: "BoundaryDetector | None" = None
    query_guard: "Any | None" = None        # app.adapters.query_guard.QueryGuard
    sessions: "SessionStore | None" = None


def _instantiate(module: str, provider: str, cfg: dict) -> Any:
    """按 "{module}.{provider}" 从注册表实例化（配置合法但未实现时启动即失败，禁止静默降级）。"""
    import app.adapters  # noqa: F401  确保适配器已注册（模块缓存，幂等）

    key = f"{module}.{provider}"
    kwargs = {k: v for k, v in cfg.items() if k != "provider"}
    try:
        return PluginRegistry.get(key, **kwargs)
    except KeyError as exc:
        raise SystemConfigError(f"provider {provider!r}（{module}）尚未装配实现") from exc


def wire(config: "SystemConfig", components: bool = True, *,
         embedding: "VectorEngine | None" = None,
         vector_store: "VectorStore | None" = None,
         retriever: "Retriever | None" = None,
         llm: "LLM | None" = None,
         reranker: "Reranker | None" = None,
         evaluator: "Evaluator | None" = None,
         rewriter: "QueryRewriter | None" = None,
         detector: "HallucinationDetector | None" = None,
         kbs: "KbStore | None" = None,
         users: "UserStore | None" = None,
         documents: "DocumentsStore | None" = None,
         jobs: "JobStore | None" = None,
         pipeline: "IngestionPipeline | None" = None,
         normalizer: "QueryNormalizer | None" = None,
         boundary: "BoundaryDetector | None" = None,
         query_guard: "Any | None" = None,
         sessions: "SessionStore | None" = None) -> Dependencies:
    """按 config 装配默认实现。

    - components=False：仅返回 config（契约/UI 冒烟测试用，不加载模型）。
    - 可注入 stub 实现（单测用）覆盖注册表装配；llm 缺 key 时优雅降级为 None。
    """
    deps = Dependencies(config=config)
    if not components:
        return deps

    if embedding is None:
        embedding = _instantiate("embedding", config.embedding.provider, config.embedding.model_dump())
    deps.embedding = embedding

    if vector_store is None:
        # P1-3：索引 DNA 随集合创建写入 metadata（新建集合生效，存量集合不受影响——
        # get_or_create 对已存在集合忽略 metadata 参数，恰好是"存量不覆盖"语义）
        from app.core.index_dna import compute_index_dna

        vector_store = _instantiate("vector_store", config.vector_store.provider,
                                    {**config.vector_store.model_dump(),
                                     "index_dna": compute_index_dna(config)["dna"]})
    deps.vector_store = vector_store

    if retriever is None:
        retriever = _instantiate(
            "retriever", config.retriever.provider,
            {**config.retriever.model_dump(),
             "embedding": embedding, "vector_store": vector_store,
             "collection": config.vector_store.collection},
        )
    deps.retriever = retriever

    # LLM：缺 API Key 或 provider 未实现 → llm=None 优雅降级（health 显示 not_loaded/degraded）
    if llm is None and config.llm.api_key:
        try:
            llm = _instantiate("llm", config.llm.provider, config.llm.model_dump())
        except SystemConfigError as exc:
            logger.warning("LLM 装配失败，降级为未装配（health 将显示 not_loaded）：%s", exc)
            llm = None
    # A6：多模型候选 + 熔断（config.llm.candidates 非空时包裹 MultiCandidateLLM）
    if llm is not None and config.llm.candidates:
        from app.adapters.llm.multi_llm import MultiCandidateLLM

        candidates = [llm]
        for item in config.llm.candidates:
            try:
                import os

                key = os.environ.get(item.get("api_key_env") or "RAG_DEEPSEEK_API_KEY")
                if not key:
                    continue
                candidates.append(_instantiate(
                    "llm", item.get("provider", "deepseek"),
                    {**config.llm.model_dump(),
                     "model": item.get("model", config.llm.model),
                     "base_url": item.get("base_url", config.llm.base_url),
                     "api_key": key}))
            except Exception as exc:  # noqa: BLE001
                logger.warning("候选 LLM 装配失败（%s）：%s", item.get("model"), exc)
        if len(candidates) > 1:
            llm = MultiCandidateLLM(
                candidates,
                fail_threshold=config.llm.circuit_fail_threshold,
                cooldown_seconds=config.llm.circuit_cooldown_seconds,
            )
    deps.llm = llm

    # 纯本地组件：直接装配（可注入 stub 覆盖；embedding/llm 注入给 semantic/llm 系 provider）
    deps.reranker = reranker or _instantiate("reranker", config.reranker.provider, config.reranker.model_dump())
    deps.evaluator = evaluator or _instantiate(
        "evaluator", config.evaluator.provider, {**config.evaluator.model_dump(), "embedding": embedding})
    deps.rewriter = rewriter or _instantiate(
        "rewriter", config.rewriter.provider, {**config.rewriter.model_dump(), "llm": llm})
    deps.detector = detector or _instantiate(
        "detector", config.detector.provider, {**config.detector.model_dump(), "embedding": embedding})

    from app.services.documents_store import DocumentsStore
    from app.services.ingestion import IngestionPipeline
    from app.services.jobs_store import JobStore
    from app.services.kb_store import DEFAULT_COLLECTION, KbStore
    from app.services.orchestration import OrchestrationService
    from app.services.user_store import UserStore

    deps.jobs = jobs if jobs is not None else JobStore()
    deps.documents = documents if documents is not None else DocumentsStore()
    deps.kbs = kbs if kbs is not None else KbStore()
    deps.users = users if users is not None else UserStore()

    # —— 13.12 输入侧治理装配（全部默认关：未启用时保持 None，零开销）——
    if config.query_input.enabled:
        from app.adapters.normalizer.rule_normalizer import RuleQueryNormalizer

        deps.normalizer = normalizer or RuleQueryNormalizer(**config.query_input.model_dump())
    if config.query_security.enabled:
        from app.adapters.query_guard import QueryGuard

        deps.query_guard = query_guard or QueryGuard(**config.query_security.model_dump())
    if config.dialogue.enabled:
        from app.adapters.dialogue.rule_boundary import RuleBoundaryDetector
        from app.services.session_store import SessionStore

        deps.boundary = boundary or RuleBoundaryDetector(
            embedding=embedding, **config.dialogue.boundary.model_dump())
        deps.sessions = sessions or SessionStore(
            ttl_seconds=config.dialogue.session.ttl_seconds,
            max_sessions=config.dialogue.max_sessions)

    # C4：pipeline 可注入复用（热切换时避免重建 worker 线程/重载模型）
    deps.pipeline = pipeline if pipeline is not None else IngestionPipeline(
        config=config, embedding=embedding, vector_store=vector_store,
        jobs=deps.jobs, documents=deps.documents,
    )
    deps.orchestration = OrchestrationService(
        config=config, retriever=retriever, reranker=deps.reranker,
        evaluator=deps.evaluator, rewriter=deps.rewriter,
        detector=deps.detector, llm=llm, vector_store=vector_store,
        normalizer=deps.normalizer,
    )
    return deps
