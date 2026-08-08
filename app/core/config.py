"""系统配置（DEVELOPMENT.md 第 4 章）。

加载与覆盖优先级：内置默认 < configs/settings.yaml < 环境变量（RAG_ 前缀）/ .env
- 环境变量命名如 RAG_LLM_PROVIDER（`_` 分隔大写，映射到嵌套字段 llm.provider）。
- 注意：不使用 pydantic-settings 的 env_nested_delimiter —— 它会把 RAG_LLM_MAX_TOKENS
  再拆成 MAX→TOKENS 导致字段名含下划线的环境变量丢失；改由 load_config 手工做
  "最长字段名匹配"解析（_resolve_env_path），保证 RAG_EMBEDDING_MODEL_NAME 等按规格生效。
- RAG_DEEPSEEK_API_KEY / RAG_LOG_LEVEL 两个别名走 _ENV_ALIASES 映射（3.8、4.1）。
- 未知 provider / 缺失 llm 段 / 非法数值 → SystemConfigError（4.3，禁止静默降级）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import SystemConfigError

# 各模块允许的 provider 取值（4.3：未知值启动即失败）
ALLOWED_PROVIDERS: dict[str, set[str]] = {
    "embedding": {"local_bge_v15", "bge_m3", "remote_api"},
    "vector_store": {"chroma", "milvus"},
    "retriever": {"hybrid_rrf", "hybrid_rerank"},
    "reranker": {"identity", "bge_reranker"},        # 13.2：identity(回退) | bge_reranker(W5)
    "evaluator": {"rule", "semantic_v2"},             # 13.3.1：rule(回退) | semantic_v2(W5)
    "rewriter": {"rule", "llm"},                      # 13.3.2：rule(回退) | llm(W5)
    "detector": {"citation", "semantic"},             # 13.4：citation(回退) | semantic(W5)
    "llm": {"deepseek", "ollama", "openai_compat"},
}

# 环境变量别名 → 配置路径（规格里明确的特殊映射）
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "DEEPSEEK_API_KEY": ("llm", "api_key"),   # 3.8：key 只走环境变量
    "LOG_LEVEL": ("logging", "level"),        # 4.1 示例 RAG_LOG_LEVEL
}

DEFAULT_SETTINGS_PATH = "configs/settings.yaml"
DOTENV_PATH = ".env"


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"          # S2（13.11.1）：默认仅本机监听，0.0.0.0 需显式配置
    port: int = 8000
    auth_enabled: bool = False
    cors_origins: list[str] = []     # 13.8：dev 跨域白名单（如 ["http://localhost:5173"]）


class FrontendConfig(BaseModel):
    """13.8：SPA 前端工程（Vue3+Vite）。"""

    dev_port: int = 5173
    dist_dir: str = "./frontend/dist"


class EmbeddingConfig(BaseModel):
    provider: str = "local_bge_v15"          # local_bge_v15（MVP）| bge_m3 | remote_api
    model_name: str = "BAAI/bge-small-zh-v1.5"
    query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    device: str = "auto"                     # auto|cpu|mps
    batch_size: int = 64
    dimension: int = 0                       # 0 = 由模型自动识别（运行时回填实际维度）


class ChunkerConfig(BaseModel):
    """父子分块参数（5.2）。"""

    parent_target_tokens: int = 512
    child_target_tokens: int = 128
    token_char_ratio: float = 1.8            # 中文 token≈len(text)/1.8 估算


class VectorStoreConfig(BaseModel):
    provider: str = "chroma"
    persist_dir: str = "./data/chroma"
    collection: str = "kb"


class RetrieverConfig(BaseModel):
    provider: str = "hybrid_rrf"
    top_k: int = 20
    candidates: int = 50
    rr_fused_k: int = 60


class RerankerConfig(BaseModel):
    provider: str = "bge_reranker"               # identity(回退) | bge_reranker(W5, 13.2)
    model_name: str = "BAAI/bge-reranker-v2-m3"  # CPU 慢可切 BAAI/bge-reranker-base
    rerank_top_k: int = 5
    batch_size: int = 32
    device: str = "auto"


class EvaluatorConfig(BaseModel):
    provider: str = "semantic_v2"                # rule(回退) | semantic_v2(W5, 13.3.1)
    rule_threshold: float = 0.45
    semantic_v2_threshold: float = 0.45          # 13.3.1：语义评估阈值（qa_pairs 校准）
    top_n: int = 5


class RewriterConfig(BaseModel):
    provider: str = "llm"                        # rule(回退) | llm(W5, 13.3.2)
    synonyms_csv: str = "./configs/synonyms.csv"
    llm_timeout_seconds: int = 30                # LLM 改写超时


class DetectorConfig(BaseModel):
    provider: str = "semantic"                   # citation(回退) | semantic(W5, 13.4)
    similarity_threshold: float = 0.8            # citation：字符 2-gram Jaccard 阈值
    semantic_similarity_threshold: float = 0.6   # semantic：句⊕块嵌入余弦阈值（13.4）


class LlmConfig(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    temperature: float = 0.1
    max_tokens: int = 1024
    timeout_seconds: int = 30
    # api_key 只允许环境变量注入（RAG_DEEPSEEK_API_KEY / RAG_LLM_API_KEY），禁止写入文件
    api_key: str | None = None


class OrchestrationConfig(BaseModel):
    max_reflection_iterations: int = 3
    search_timeout_seconds: int = 10
    request_timeout_seconds: int = 30


class IngestionConfig(BaseModel):
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = ["pdf", "docx", "xlsx", "pptx", "md", "txt"]
    embed_batch_size: int = 64


class LoggingConfig(BaseModel):
    # yaml 键保持 4.2 的 `json: true`，字段名避免与 pydantic 内部属性重名
    model_config = ConfigDict(populate_by_name=True)
    level: str = "info"
    json_enabled: bool = Field(default=True, validation_alias="json")


class SystemConfig(BaseSettings):
    """系统唯一配置入口（4.2 完整 Schema）。

    环境变量解析统一走 load_config 的手工映射（见模块 docstring），
    这里保留 pydantic-settings 作为 .env 与 RAG_ENV 等单段键的兜底。
    """

    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=".env",
        extra="ignore",
        validate_default=True,
    )

    env: str = "dev"
    api: ApiConfig = ApiConfig()
    frontend: FrontendConfig = FrontendConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    chunker: ChunkerConfig = ChunkerConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    retriever: RetrieverConfig = RetrieverConfig()
    reranker: RerankerConfig = RerankerConfig()
    evaluator: EvaluatorConfig = EvaluatorConfig()
    rewriter: RewriterConfig = RewriterConfig()
    detector: DetectorConfig = DetectorConfig()
    llm: LlmConfig = LlmConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    ingestion: IngestionConfig = IngestionConfig()
    logging: LoggingConfig = LoggingConfig()

    @model_validator(mode="after")
    def _validate_providers(self) -> "SystemConfig":
        """未知 provider / 非法数值 → SystemConfigError（4.3，禁止静默降级）。"""
        providers = {
            "embedding": self.embedding.provider,
            "vector_store": self.vector_store.provider,
            "retriever": self.retriever.provider,
            "reranker": self.reranker.provider,
            "evaluator": self.evaluator.provider,
            "rewriter": self.rewriter.provider,
            "detector": self.detector.provider,
            "llm": self.llm.provider,
        }
        for module, provider in providers.items():
            if provider not in ALLOWED_PROVIDERS[module]:
                raise SystemConfigError(
                    f"未知 {module}.provider: {provider!r}（可选：{sorted(ALLOWED_PROVIDERS[module])}）"
                )
        if self.embedding.dimension < 0:
            raise SystemConfigError("embedding.dimension 必须 >= 0（0 表示由模型自动识别）")
        if not 0 <= self.evaluator.rule_threshold <= 1:
            raise SystemConfigError("evaluator.rule_threshold 必须在 [0,1]")
        if not 0 <= self.evaluator.semantic_v2_threshold <= 1:
            raise SystemConfigError("evaluator.semantic_v2_threshold 必须在 [0,1]")
        if not 0 <= self.detector.similarity_threshold <= 1:
            raise SystemConfigError("detector.similarity_threshold 必须在 [0,1]")
        if not 0 <= self.detector.semantic_similarity_threshold <= 1:
            raise SystemConfigError("detector.semantic_similarity_threshold 必须在 [0,1]")
        return self


# ---------------------------------------------------------------------------
# 环境变量解析（RAG_ 前缀 → 嵌套配置 dict，最长字段名匹配）
# ---------------------------------------------------------------------------

def _read_yaml(path: str | Path) -> dict[str, Any]:
    if not Path(path).exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _read_dotenv(path: str = DOTENV_PATH) -> dict[str, str]:
    """极简 .env 解析（KEY=VALUE，# 注释，支持引号）。"""
    out: dict[str, str] = {}
    if not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _coerce_env_value(value: str) -> Any:
    """复杂类型（list/dict）环境变量值按 JSON 解析，其余原样交给 pydantic 强转。"""
    stripped = value.strip()
    if stripped[:1] in ("[", "{", '"'):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped


def _set_path(target: dict, path: list[str], value: Any) -> None:
    node = target
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = value


def _resolve_env_path(name: str, model: type[BaseModel]) -> list[str] | None:
    """把 RAG_ 后的环境变量名解析为配置字段路径（最长字段名匹配，不区分大小写）。

    例：EMBEDDING_MODEL_NAME → ["embedding", "model_name"]；
        LLM_MAX_TOKENS → ["llm", "max_tokens"]。
    无法映射到字段树时返回 None（忽略该变量）。
    """
    lower = name.lower()
    for field_name in model.model_fields:
        if lower == field_name.lower():
            return [field_name]
    for field_name, field_info in model.model_fields.items():
        prefix = field_name + "_"
        if lower.startswith(prefix.lower()):
            annotation = field_info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                sub = _resolve_env_path(name[len(field_name) + 1:], annotation)
                if sub is not None:
                    return [field_name] + sub
    return None


def _env_source_dict() -> dict[str, Any]:
    """收集 RAG_ 前缀环境变量（真实环境优先于 .env）并映射为嵌套配置 dict。"""
    raw: dict[str, str] = _read_dotenv()
    raw.update({k: v for k, v in os.environ.items() if k.startswith("RAG_")})
    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = key[len("RAG_"):]
        path = _ENV_ALIASES.get(name)
        if path is None:
            path = _resolve_env_path(name, SystemConfig)
        if path is not None:
            _set_path(out, list(path), _coerce_env_value(value))
    return out


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个配置 dict（override 覆盖 base）。"""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(settings_path: str = DEFAULT_SETTINGS_PATH) -> SystemConfig:
    """加载配置：settings.yaml + 环境变量（环境变量优先）。

    - 缺失 llm 段（yaml 与环境变量均无）→ SystemConfigError（4.3）。
    - pydantic 校验失败 → SystemConfigError。
    """
    data = _read_yaml(settings_path)
    env_data = _env_source_dict()
    if "llm" not in data and "llm" not in env_data:
        raise SystemConfigError(
            f"配置缺少必需的 llm 段（{settings_path} 或 RAG_LLM_* / RAG_DEEPSEEK_API_KEY 环境变量）"
        )
    merged = _deep_merge(data, env_data)
    try:
        return SystemConfig(**merged)
    except SystemConfigError:
        raise
    except ValidationError as exc:
        raise SystemConfigError(f"配置校验失败：{exc}") from exc
