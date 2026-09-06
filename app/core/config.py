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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
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
    "ADMIN_USER": ("auth", "admin_user"),     # V5.1：首管理员引导（auth_enabled=true 且用户表为空时）
    "ADMIN_PASSWORD": ("auth", "admin_password"),
    "AUTH_SECRET": ("auth", "secret"),        # V5.1：Token 签名密钥（否则运行时生成 data/secret.key）
}

DEFAULT_SETTINGS_PATH = "configs/settings.yaml"
DOTENV_PATH = ".env"


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"          # S2（13.11.1）：默认仅本机监听，0.0.0.0 需显式配置
    port: int = 8000
    auth_enabled: bool = False       # S1（13.11.1）：true 时启用密码登录 + Token 认证
    cors_origins: list[str] = []     # 13.8：dev 跨域白名单（如 ["http://localhost:5173"]）
    query_rate_per_min: int = 20     # C4（V5.1）：/api/query 每 IP 每分钟上限，超限返回 43002
    upload_rate_per_min: int = 10    # C4：/api/documents/upload 每 IP 每分钟上限


class AuthConfig(BaseModel):
    """V5.1 团队模式：Token 有效期与首管理员引导（密钥只走环境变量/运行时生成）。"""

    token_ttl_days: int = 7
    admin_user: str | None = None
    admin_password: str | None = None
    secret: str | None = None        # RAG_AUTH_SECRET；未配置则运行时生成 data/secret.key（0o600）


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


class TableAwareConfig(BaseModel):
    """表格原子块（P1-4，DEVELOPMENT.md 5.2；默认关闭：[TABLE] 行按普通文本聚合）。"""

    enabled: bool = False
    max_rows_per_chunk: int = 30           # 超限大表按行组切，每组行数上限（强制带表头）
    json_max_bytes: int = 4096             # table_json 字节上限（超限截断+warning）


class ChunkerConfig(BaseModel):
    """父子分块参数（5.2）。"""

    parent_target_tokens: int = 512
    child_target_tokens: int = 128
    token_char_ratio: float = 1.8            # 中文 token≈len(text)/1.8 估算
    table_aware: TableAwareConfig = TableAwareConfig()   # P1-4：表格原子块（默认关）


class VectorStoreConfig(BaseModel):
    provider: str = "chroma"
    persist_dir: str = "./data/chroma"
    collection: str = "kb"


class RetrieverConfig(BaseModel):
    provider: str = "hybrid_rrf"
    top_k: int = 20
    candidates: int = 50
    rr_fused_k: int = 60
    # —— 2026-08-17：检索内并发 + query 级缓存 ——
    # query 级 LRU 缓存条目上限（0=关闭缓存，退化为原行为）
    query_cache_maxsize: int = 128
    # 缓存条目存活时间（秒），作为文档变更后陈旧的兜底失效
    query_cache_ttl_seconds: int = 300
    # 检索内并行路数（BM25 + 向量两路并行）
    max_concurrent_paths: int = 2


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
    # A6（智能体体验增强）：多模型候选（按优先级）+ 熔断参数
    candidates: list[dict] = []
    circuit_fail_threshold: int = 3
    circuit_cooldown_seconds: int = 60


class OrchestrationConfig(BaseModel):
    max_reflection_iterations: int = 3
    search_timeout_seconds: int = 10
    request_timeout_seconds: int = 30
    max_concurrent_queries: int = 4        # C3（V5.1）：并发查询信号量上限，耗尽返回 43001
    semaphore_acquire_timeout_seconds: float = 5.0   # C3：获取信号量等待上限
    # 13.13 T1 绝望短路：首轮评估分远低于阈值且无联网候选 → 跳过改写轮直接 21001 兜底
    hopeless_shortcut: bool = True
    hopeless_score_threshold: float = 0.15


class QualityGateConfig(BaseModel):
    """解析质量门禁（P0-1，DEVELOPMENT.md 5.1/5.10；默认关闭：不改变现有入库行为）。

    - mode=reject：命中任一 flag（empty_text/high_garbled/few_pages_with_text）
      抛 QualityGateError（30005），Job failed；
    - mode=flag：仅 doc.metadata["quality_flags"] 打标 + warning 日志，继续入库。
    - mode 非法值在 field_validator 抛 SystemConfigError（pydantic v2 对非
      ValueError 异常原样上抛）→ 启动即错（4.3，禁止静默降级）。
    """

    enabled: bool = False
    mode: str = "reject"                     # reject | flag
    min_text_chars: int = 30                 # 有效文本最小字符数（不足 → empty_text）
    max_garbled_ratio: float = 0.05          # U+FFFD 乱码占比上限（超过 → high_garbled）
    min_pages_with_text_ratio: float = 0.2   # 多页 PDF 有文本页占比下限（低于 → few_pages_with_text）

    @field_validator("mode")
    @classmethod
    def _mode_must_be_reject_or_flag(cls, value: str) -> str:
        if value not in ("reject", "flag"):
            raise SystemConfigError(
                f"ingestion.quality_gate.mode 必须为 reject 或 flag，实际为 {value!r}"
            )
        return value


class ArchiveConfig(BaseModel):
    """原件归档（P0-2，DEVELOPMENT.md 5.10；默认关闭：不改变现有入库行为）。

    启用后入库成功路径将**原始字节**原子写至 {dir}/{kb_id}/{document_id}{ext}，
    供换 embedding 全量重建（P1-3 rebuild CLI）、解析器升级重解析与审计取证使用；
    写失败仅告警不回滚入库（归档尽力而为，archive_path 置 None 表示未归档）。
    """

    enabled: bool = False
    dir: str = "./data/archive"


class IngestionConfig(BaseModel):
    max_file_size_mb: int = 50
    allowed_extensions: list[str] = ["pdf", "docx", "xlsx", "pptx", "md", "txt",
                                     "html", "htm", "eml", "msg"]
    embed_batch_size: int = 64
    desensitize_enabled: bool = False   # S5：敏感数据（身份证/手机号/银行卡）脱敏开关
    max_queue_size: int = 64            # C5（V5.1）：Worker 队列上限，满时拒绝并返回 43003
    # S2（V5.1）：zip 系格式解压防御上限（防止 zip 炸弹炸内存/CPU）
    zip_max_total_mb: int = 200
    zip_max_entries: int = 1000
    quality_gate: QualityGateConfig = QualityGateConfig()   # P0-1：解析质量门禁（默认关）
    archive: ArchiveConfig = ArchiveConfig()                # P0-2：原件归档（默认关）


class CleanerConfig(BaseModel):
    """数据清洗层（解析后、分块前，app/adapters/cleaner）。默认关闭：不改现有入库行为。"""

    enabled: bool = False
    normalize: bool = True       # 全半角/空白/控制字符归一
    noise: bool = True           # 页码/页眉页脚/模板串去噪
    dedup: bool = True           # 段落级 simhash 近重复去重
    quality: bool = True         # 质量过滤（过短/无实词）
    dedup_threshold: float = 0.9
    min_chunk_chars: int = 20


class InjectionConfig(BaseModel):
    """入库侧提示注入扫描（P-C，默认关闭：不改变现有入库行为）。

    - mode=audit：命中仅审计日志 + metadata[injection_hits] 标记，不拒收；
    - mode=reject：命中抛 InjectionError（40002），Job failed。
    - patterns 为追加模式（内置模式表始终生效，自定义正则追加其后）；
      whitelist 为误杀白名单子串（命中该子串的行整行豁免）。
    """

    enabled: bool = False
    mode: str = "audit"          # audit | reject
    patterns: list[str] = []
    whitelist: list[str] = []


class ModerationConfig(BaseModel):
    """入库前内容审核层（P-C，默认关闭）。

    - mode=reject：命中抛 ContentModerationError（40003），Job failed；
    - mode=flag：命中写 metadata[moderation_flags]，不拒收。
    - keywords_csv 缺失/损坏时用内置最小词表兜底（与 synonyms.csv 同款范式）。
    """

    enabled: bool = False
    mode: str = "reject"         # reject | flag
    keywords_csv: str = "./configs/moderation_keywords.csv"


class MemoryConfig(BaseModel):
    """A1 会话记忆压缩（智能体体验增强）。"""

    enabled: bool = True
    compress_threshold: int = 6     # 历史超过 N 轮时压缩早期轮次


class WebConfig(BaseModel):
    """A5 联网检索（Tavily，纯 HTTP）。"""

    enabled: bool = False
    provider: str = "tavily"
    api_key_env: str = "RAG_TAVILY_API_KEY"   # key 只走环境变量
    max_results: int = 5
    search_depth: str = "basic"
    timeout_seconds: int = 8
    first_round_only: bool = True   # 仅首轮拉取（省配额与耗时）
    kb_priority: bool = True        # 冲突优先级：KB 块恒排 web 之前（即使 web 相关度更高，仍以知识库为准；web 仅作补充）


class QueryInputConfig(BaseModel):
    """查询侧输入标准化管线（在线侧输入处理.md L1；默认关，不改变现有行为）。

    - filter_invalid_chars：Emoji/控制字符清理 + 连续重复标点压缩（！！！→！）；
    - asr_smooth：语音转写语气词清理（filler 词典可配，最长优先）+ 连续单字重复压缩（对对对→对）；
    - resolve_relative_time：相对时间 → 绝对时间（昨天→2026-08-17；reference 缺省取当天）。
    每步独立 try/except：异常即跳过该步，输出必为合法文本（最差等于输入）。
    """

    enabled: bool = False
    filter_invalid_chars: bool = True
    asr_smooth: bool = True
    asr_filler_words: list[str] = ["嗯", "那个", "就是说", "然后", "呃", "额", "这个这个", "就是"]
    resolve_relative_time: bool = True
    time_reference_date: str | None = None      # 如 "2026-08-17"；None = 当天
    timezone: str = "Asia/Shanghai"


class QuerySecurityConfig(BaseModel):
    """查询侧安全前置（在线侧输入处理.md 4.6 横切；默认关）。

    复用入库侧 InjectionScanner / KeywordModerator，在 /api/query 入口执行：
    - mode=audit：命中仅审计日志（query_security_audit），不拦截；
    - mode=reject：命中抛 InjectionError(40002) / ContentModerationError(40003)。
    - patterns/whitelist 为追加模式（内置模式表始终生效）；keywords_csv 缺失/损坏兜底内置词表。
    """

    enabled: bool = False
    mode: str = "audit"                          # audit | reject
    injection: bool = True
    moderation: bool = True
    patterns: list[str] = []
    whitelist: list[str] = []
    keywords_csv: str = "./configs/moderation_keywords.csv"


class GreetingRule(BaseModel):
    """单条极简通道规则（13.13 T2）：命中 pattern → 返回该规则独立 reply。"""

    name: str = ""               # 可选标识（日志/排障用）
    pattern: str
    reply: str


class GreetingConfig(BaseModel):
    """L0 极简通道：问候语/闲聊规则直达（在线侧输入处理.md 4.1.2；代码默认关，部署可启用）。

    匹配顺序：rules（每条独立回复，13.13 T2）→ 旧版 patterns+reply（兼容字段，rules 为空时生效）；
    命中即直接返回 reply（不检索、不调 LLM）；单个正则非法 → 跳过继续匹配。
    """

    enabled: bool = False
    rules: list[GreetingRule] = []
    patterns: list[str] = [
        r"^(?:你好|您好|嗨|哈喽|hello|hi)\s*[!！。~～]*$",
        r"^(?:在吗|在么|有人吗)\s*[?？]*$",
        r"^(?:谢谢|感谢|谢啦|多谢)\s*[!！。~～]*$",
    ]
    reply: str = "您好，我是企业知识库问答助手，可以问我知识库相关的问题，例如「年假怎么申请？」"


class ClarificationConfig(BaseModel):
    """A2 主动澄清（在线侧输入处理.md 4.4.5）：固定模板 + 槽位填充，LLM 仅可选增强。

    - enabled=False：维持「反思超限直接 21001 兜底」（不反问）；
    - use_llm=True：先 LLM 生成澄清问句，失败/超时回退模板；
    - max_rounds：每会话连续反问上限（dialogue 启用后按 session 计数，达上限转 21001）。
    """

    enabled: bool = True
    max_rounds: int = 2
    use_llm: bool = False
    templates: dict[str, str] = {
        "no_entity": "请问您具体想了解哪个方面的内容呢？",
        "missing_issue": "请问您遇到了什么具体问题？",
        "generic": "您的需求我暂时没有理解清楚，建议您描述更详细一些，或联系人工客服。",
    }
    fallback_message: str = "您的需求我暂时没有理解清楚，建议您描述更详细一些，或联系人工客服。"


class DialogueSessionConfig(BaseModel):
    """L2 会话生命周期（在线侧输入处理.md 4.3.3）。"""

    ttl_seconds: int = 1800          # 会话空闲过期（默认 30 分钟）
    max_rounds: int = 5              # 保留最近 N 轮（轮 = user+assistant 对）
    enable_rollback: bool = True     # "不对/说错了" 等回溯意图 → 移除上一轮记录
    rollback_keywords: list[str] = ["不对", "说错了", "不是这个", "重来", "不是"]


class DialogueBoundaryConfig(BaseModel):
    """L2 会话边界判定（在线侧输入处理.md 4.3.2）：双维度综合判定新话题。"""

    enabled: bool = True
    semantic_threshold: float = 0.60          # 当前 query 与上轮 query 嵌入余弦 < 阈值 → 提示新话题
    entity_jaccard_threshold: float = 0.20    # 实体重合度 < 阈值 → 提示新话题
    use_embedding: bool = True


class DialogueConfig(BaseModel):
    """L2 对话状态引擎（在线侧输入处理.md 4.3；总开关默认关 → 完全维持现状）。

    启用后：服务端按 session_id 维护会话历史（客户端 history 退居兜底），
    新话题判定清空短期上下文，回溯关键词移除上一轮，TTL 过期重置；
    会话存储为进程内内存（max_sessions LRU 淘汰），生产可换 Redis（接口不变）。
    """

    enabled: bool = False
    session: DialogueSessionConfig = DialogueSessionConfig()
    boundary: DialogueBoundaryConfig = DialogueBoundaryConfig()
    max_sessions: int = 100          # 内存会话上限（超出 LRU 淘汰最久未用）


class LoggingConfig(BaseModel):
    # yaml 键保持 4.2 的 `json: true`，字段名避免与 pydantic 内部属性重名
    model_config = ConfigDict(populate_by_name=True)
    level: str = "info"
    json_enabled: bool = Field(default=True, validation_alias="json")


class DocumentsConfig(BaseModel):
    """文档元数据持久化（重启不丢；测试可注入临时路径）。"""

    persist_file: str = "data/documents.json"


class KbsConfig(BaseModel):
    """知识库元数据持久化（测试可注入临时路径）。"""

    persist_file: str = "data/kb.json"


class IndexDnaConfig(BaseModel):
    """索引 DNA 查询侧校验（P1-3，DEVELOPMENT.md 13.5；默认关闭）。

    - enabled=false：不做校验（现状行为）；
    - mode=audit（默认）：collection dna 与当前配置不一致 → warning 日志 +
      响应标记 index_dna_mismatch=true，不拦截；
    - mode=reject：命中抛 IndexDnaMismatchError（30006）。
    存量无 dna 的 collection 视为 legacy：仅日志提示，不告警不拒绝。
    """

    enabled: bool = False
    mode: str = "audit"                      # audit | reject


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
    auth: AuthConfig = AuthConfig()
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
    cleaner: CleanerConfig = CleanerConfig()
    memory: MemoryConfig = MemoryConfig()
    web: WebConfig = WebConfig()
    injection: InjectionConfig = InjectionConfig()
    moderation: ModerationConfig = ModerationConfig()
    # —— 13.12 输入侧治理（在线侧输入处理落地；全部默认关，clarification 默认开）——
    query_input: QueryInputConfig = QueryInputConfig()
    query_security: QuerySecurityConfig = QuerySecurityConfig()
    greeting: GreetingConfig = GreetingConfig()
    clarification: ClarificationConfig = ClarificationConfig()
    dialogue: DialogueConfig = DialogueConfig()
    logging: LoggingConfig = LoggingConfig()
    documents: DocumentsConfig = DocumentsConfig()
    kbs: KbsConfig = KbsConfig()
    index_dna: IndexDnaConfig = IndexDnaConfig()   # P1-3：索引 DNA 查询侧校验（默认关）

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
        # C6（V5.1）：并发/限速/队列数值护栏 —— 0 会被 queue.Queue(0) 视为无界、限速永久拒绝
        if self.api.query_rate_per_min < 1 or self.api.upload_rate_per_min < 1:
            raise SystemConfigError("api.query_rate_per_min / upload_rate_per_min 必须 >= 1")
        if self.ingestion.max_queue_size < 1:
            raise SystemConfigError("ingestion.max_queue_size 必须 >= 1（0 会被 queue.Queue 视为无界）")
        if self.orchestration.max_concurrent_queries < 1:
            raise SystemConfigError("orchestration.max_concurrent_queries 必须 >= 1")
        if self.orchestration.request_timeout_seconds < 1 or self.orchestration.search_timeout_seconds < 1:
            raise SystemConfigError("orchestration 超时配置必须 >= 1 秒")
        if self.orchestration.semaphore_acquire_timeout_seconds < 0:
            raise SystemConfigError("orchestration.semaphore_acquire_timeout_seconds 必须 >= 0（0=不等待立即 43001）")
        # 13.12 输入侧治理：数值/枚举护栏（禁止静默降级为非法取值）
        if self.query_security.mode not in ("audit", "reject"):
            raise SystemConfigError("query_security.mode 必须为 audit 或 reject")
        if self.clarification.max_rounds < 1:
            raise SystemConfigError("clarification.max_rounds 必须 >= 1")
        if not 0 <= self.dialogue.boundary.semantic_threshold <= 1:
            raise SystemConfigError("dialogue.boundary.semantic_threshold 必须在 [0,1]")
        if not 0 <= self.dialogue.boundary.entity_jaccard_threshold <= 1:
            raise SystemConfigError("dialogue.boundary.entity_jaccard_threshold 必须在 [0,1]")
        if self.dialogue.session.ttl_seconds < 0 or self.dialogue.session.max_rounds < 0 \
                or self.dialogue.max_sessions < 1:
            raise SystemConfigError("dialogue 会话参数非法（ttl_seconds/max_rounds >= 0，max_sessions >= 1）")
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
    """收集 RAG_ 前缀环境变量（真实环境优先于 .env）并映射为嵌套配置 dict。

    注意：项目自实现 .env 解析（_read_dotenv），此前只把值喂给 SystemConfig，
    未回写 os.environ。直接读 os.environ 取 key 的组件（WebRetriever、A6 候选 LLM）
    因此拿不到 .env 里的 RAG_* —— 这里在加载时回写，保证「key 只走环境变量」对
    .env 同样生效（真实环境已存在的变量保持不变，仅用 .env 补缺）。
    """
    raw: dict[str, str] = _read_dotenv()
    raw.update({k: v for k, v in os.environ.items() if k.startswith("RAG_")})
    for _k, _v in raw.items():
        if _k.startswith("RAG_") and _k not in os.environ:
            os.environ[_k] = _v
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
