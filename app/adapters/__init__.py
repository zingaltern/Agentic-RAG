"""适配器注册（DEVELOPMENT.md 5.11）："{module}.{provider}" → 实现类。

用模块前缀区分同 provider 名的不同模块（如 evaluator.rule 与 rewriter.rule）。
"""
from app.adapters.detector.citation_tracing import CitationTracingDetector
from app.adapters.embedding.local_bge_v15 import LocalBGEV15Embedding
from app.adapters.evaluator.rule_evaluator import RuleEvaluator
from app.adapters.llm.deepseek_llm import DeepSeekLLM
from app.adapters.reranker.identity import IdentityReranker
from app.adapters.retriever.hybrid_rrf import HybridRRFRetriever
from app.adapters.rewriter.rule_rewriter import RuleRewriter
from app.adapters.vector_store.chroma_store import ChromaStore
from app.core.registry import PluginRegistry

PluginRegistry.register("embedding.local_bge_v15", LocalBGEV15Embedding)
PluginRegistry.register("vector_store.chroma", ChromaStore)
PluginRegistry.register("retriever.hybrid_rrf", HybridRRFRetriever)
PluginRegistry.register("reranker.identity", IdentityReranker)
PluginRegistry.register("evaluator.rule", RuleEvaluator)
PluginRegistry.register("rewriter.rule", RuleRewriter)
PluginRegistry.register("detector.citation", CitationTracingDetector)
PluginRegistry.register("llm.deepseek", DeepSeekLLM)
