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
        Evaluator,
        HallucinationDetector,
        LLM,
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
         detector: "HallucinationDetector | None" = None) -> Dependencies:
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
        vector_store = _instantiate("vector_store", config.vector_store.provider, config.vector_store.model_dump())
    deps.vector_store = vector_store

    if retriever is None:
        retriever = _instantiate(
            "retriever", config.retriever.provider,
            {**config.retriever.model_dump(),
             "embedding": embedding, "vector_store": vector_store,
             "collection": config.vector_store.collection},
        )
    deps.retriever = retriever

    # 纯本地组件：直接装配（可注入 stub 覆盖）
    deps.reranker = reranker or _instantiate("reranker", config.reranker.provider, config.reranker.model_dump())
    deps.evaluator = evaluator or _instantiate("evaluator", config.evaluator.provider, config.evaluator.model_dump())
    deps.rewriter = rewriter or _instantiate("rewriter", config.rewriter.provider, config.rewriter.model_dump())
    deps.detector = detector or _instantiate("detector", config.detector.provider, config.detector.model_dump())

    # LLM：缺 API Key 或 provider 未实现 → llm=None 优雅降级（health 显示 not_loaded/degraded）
    if llm is None and config.llm.api_key:
        try:
            llm = _instantiate("llm", config.llm.provider, config.llm.model_dump())
        except SystemConfigError as exc:
            logger.warning("LLM 装配失败，降级为未装配（health 将显示 not_loaded）：%s", exc)
            llm = None
    deps.llm = llm

    from app.services.documents_store import DocumentsStore
    from app.services.ingestion import IngestionPipeline
    from app.services.jobs_store import JobStore
    from app.services.orchestration import OrchestrationService

    deps.jobs = JobStore()
    deps.documents = DocumentsStore()
    deps.pipeline = IngestionPipeline(
        config=config, embedding=embedding, vector_store=vector_store,
        jobs=deps.jobs, documents=deps.documents,
    )
    deps.orchestration = OrchestrationService(
        config=config, retriever=retriever, reranker=deps.reranker,
        evaluator=deps.evaluator, rewriter=deps.rewriter,
        detector=deps.detector, llm=llm, vector_store=vector_store,
    )
    return deps
