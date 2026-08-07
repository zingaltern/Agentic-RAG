"""适配器注册（DEVELOPMENT.md 5.11）：provider 名 → 实现类。"""
from app.adapters.embedding.local_bge_v15 import LocalBGEV15Embedding
from app.adapters.retriever.hybrid_rrf import HybridRRFRetriever
from app.adapters.vector_store.chroma_store import ChromaStore
from app.core.registry import PluginRegistry

PluginRegistry.register("local_bge_v15", LocalBGEV15Embedding)
PluginRegistry.register("chroma", ChromaStore)
PluginRegistry.register("hybrid_rrf", HybridRRFRetriever)
