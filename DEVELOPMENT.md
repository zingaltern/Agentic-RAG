# Agentic RAG 智能知识库系统 — 开发设计文档（V5.1 交付版）

> 本文件是开发本系统的**唯一事实依据（Single Source of Truth）**，内容自包含（Self-Contained），无需参考任何其他文档。
> 任何开发本项目的智能体（Code Agent）与工程师必须严格遵守本文档的**接口契约、数据模型、算法、配置、验收标准**。
> 若实现与本文档冲突，以本文档为准；确需偏离时，必须在代码中显著标注原因并申请修订本文件。

**版本沿革**：V4.0（Phase2 规划）→ V5.0（Phase2 全部交付）：W5~W8 已完成（reranker/semantic 评估/LLM 改写/语义检测/多知识库/评测 v2/Vue3 SPA/Electron 桌面/认证/审计/热切换/脱敏/持久化）。
→ V5.1（轻量两级团队模式）：密码登录 + HMAC Token（替代 X-API-Key）；管理员/普通用户两级权限（知识库级读写）；用户管理 API；RBAC 接线（Authorizer）；kb owner/scope（私有库）。
与代码冲突时**以 V5.1 本文件为准**；第 13 章为交付状态与规格，第 14 章为当前状态与遗留。

---

## 第 1 章 工程基线（开发前必读）

### 1.1 技术栈与依赖（版本锁定）

| 类别 | 选型 | 版本约束 | 用途 |
|---|---|---|---|
| 语言 | Python | 3.10 ~ 3.12 | 应为 3.12 上限，依赖 `sentence-transformers/torch` 生态兼容 |
| API 框架 | FastAPI | >= 0.110 | 自动生成 OpenAPI 文档 |
| ASGI | uvicorn[standard] | 最新稳定 | 本地 `uvicorn app.main:app --reload` 启动 |
| 配置 | pydantic + pydantic-settings | >= 2.6 | 唯一配置入口 |
| 向量化 | sentence-transformers | >= 3.0 | 本地嵌入模型推理 |
| 向量库 | chromadb | >= 0.5 | SQLite 后端，单文件，零运维 |
| 全文检索 | rank-bm25 | >= 0.2 | BM25 检索（中文需先分词） |
| 分词 | jieba | >= 0.42 | Evaluator/Rewriter/BM25 中文处理 |
| 文档解析 | pymupdf / python-docx / openpyxl / python-pptx | 各自 >= 稳定版 | PDF/Word/Excel/PPT 文本抽取 |
| LLM 客户端 | openai（OpenAI 兼容协议） | >= 1.30 | 调用 DeepSeek API |
| 测试 | pytest + pytest-asyncio + respx | —— | 单元/契约 Mock |
| 前端 SPA | Vue3 + Vite + Vue Router + Pinia + Element Plus | 见 `frontend/package.json` | 主界面（13.7），Web 与桌面共用 |
| 桌面壳 | Electron | ^43（package.json 锁定） | 内置拉起后端的桌面形态（13.7 / W8） |
| 前端测试页 | 原生 HTML+JS | 无依赖 | 由 FastAPI 静态托管于 `/ui`（冒烟/降级入口） |
| 容器 | docker / docker-compose | 可选 | 集成测试/部署（MVP 可不装） |

依赖清单统一写入 `pyproject.toml`，禁止任何代码直接 `import` 清单外的第三方库。

### 1.2 仓库结构（Agent 须按此骨架落盘）

```
rag_kb/
├── application.py             # 装配层：中间件（CORS/安全头/限速/审计）+ 依赖注入
├── app/
│   ├── main.py               # FastAPI 入口：装配 DI → 启动
│   ├── api/
│   │   ├── documents.py      # upload / list / get / delete（支持 kb_id）
│   │   ├── query.py          # /api/query（history/kb_id/debug）
│   │   ├── plugins.py        # status / switch（热切换，已实现）
│   │   ├── jobs.py           # /api/jobs/{id}
│   │   ├── kbs.py            # /api/kbs CRUD（13.5 多知识库）
│   │   ├── users.py          # 用户管理（仅 admin，V5.1 / 6.8）
│   │   ├── health.py         # /api/health
│   │   ├── auth.py           # S1：密码登录 + Bearer Token 认证 + 白名单
│   │   └── utils.py          # ok/fail 响应包 + get_deps
│   ├── core/
│   │   ├── config.py         # SystemConfig（pydantic-settings）
│   │   ├── domain.py         # 全部数据模型（第 2 章）
│   │   ├── interfaces.py     # 全部抽象接口（第 3 章）
│   │   ├── registry.py       # PluginRegistry + wire() 装配
│   │   ├── errors.py         # 异常层级与错误码（2.2）
│   │   ├── rate_limit.py     # 滑动窗口限速器（V5.1，纯标准库）
│   │   └── logging.py        # JSON 结构化日志 + trace_id
│   ├── services/
│   │   ├── ingestion.py      # IngestionPipeline（上传/入库，含脱敏 S5）
│   │   ├── orchestration.py  # Agentic 循环编排（5.9/13.3）
│   │   ├── kb_store.py       # KbStore：kb_id→collection 映射（持久化）
│   │   ├── documents_store.py# 文档索引（持久化 + Chroma 兜底重建）
│   │   ├── jobs_store.py     # Job 存储（内存实现 + 锁）
│   │   ├── user_store.py     # 用户存储（scrypt 哈希 + token_version，V5.1）
│   │   ├── rbac.py           # RBAC 权限映射（V5.1 已接线：Authorizer + kb owner/scope）
│   │   └── desensitize.py    # S5：敏感数据脱敏（正则）
│   └── adapters/
│       ├── base.py           # LocalModelAdapter 公共基类（13.11.2）
│       ├── embedding/        # local_bge_v15（已实现）；bge_m3/remote_api（预留）
│       ├── vector_store/     # chroma（已实现）；milvus（预留）
│       ├── retriever/        # hybrid_rrf（已实现）；hybrid_rerank（预留）
│       ├── reranker/         # bge_reranker（已实现，默认）；identity（回退）
│       ├── evaluator/        # semantic_v2（已实现，默认）；rule（回退）
│       ├── rewriter/         # llm（已实现，默认）；rule（回退）
│       ├── detector/         # semantic（已实现，默认）；citation（回退）
│       ├── llm/              # deepseek（已实现）；ollama/openai_compat（预留）
│       ├── parser/           # pdf/docx/xlsx/pptx/md/txt
│       └── chunker/          # parent_child
├── frontend/                 # Vue3 + Vite + Pinia + Element Plus SPA（13.7）
│   ├── src/                  # views（问答/文档/知识库/设置）、api、stores、router
│   ├── electron/             # 桌面壳：main.cjs/preload.cjs/bootstrap.cjs
│   └── dist/                 # vite build 产物（由 FastAPI 托管 /）
├── web/                      # /ui 旧测试页（冒烟/降级入口，13.7）
├── configs/
│   └── settings.yaml         # 见第 4 章
├── tests/
│   ├── unit/                 # 单测（含 auth/plugins/desensitize/kbs/…）
│   ├── integration/          # 真实 BGE 全链路（-m integration）
│   └── eval/                 # qa_pairs/{default,docs}/*.jsonl（107 条）
├── data/                     # 运行期数据：chroma/ kb.json documents.json
├── reports/                  # eval 报告存档（13.1 度量协议）
├── docs/review-followup.md   # 专业评审跟进清单
├── pyproject.toml
├── .env.example               # 环境变量样例（不含真实密钥）
├── Makefile                  # make bootstrap / run / test / eval / desktop
└── README.md
```

### 1.3 编码与协作规范
- 注释、文档用中文；标识符用英文蛇形命名（`snake_case`）。
- 抽象接口一律放 `core/interfaces.py`；具体实现一律放 `app/adapters/` 下的独立文件，类名 = `策略名 + 接口名`（如 `LocalBGEBellEmbedding(VectorEngine)`）。
- 所有配置值必须从 `SystemConfig` 读取；禁止在业务代码中散落 `os.getenv(...)`。
- 所有外部 I/O（LLM 请求、模型加载、文件系统）必须有超时与异常包装（升起 `core/errors.py` 定义的异常）。
- 不落任何密钥到仓库（`api_key` 只允许通过环境变量注入）。
- Makefile 提供 `make bootstrap`（venv+依赖）、`make run`、`make test`、`make demo-query`。

### 1.4 启动前置
- Python 3.11 + 虚拟环境。
- 环境变量 `RAG_DEEPSEEK_API_KEY=sk-...`（DeepSeek 平台获取）。
- 首次启动自动下载嵌入模型（约 100~400MB，见 5.3），仅需联网一次。

---

## 第 2 章 数据模型与全局契约

### 2.1 核心数据模型（`core/domain.py`）

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Chunk:
    chunk_id: str                 # 必填：sha256(document_id + ":" + str(order))[:16]
    document_id: str
    text: str
    order: int
    parent_id: str | None         # 子块 → 父块；父块为 None
    metadata: dict[str, Any]      # 常规字段见下

@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    notion: str = "rrf"           # 来源："bm25"/"vector"/"rrf"/"rerank"

@dataclass
class Document:
    document_id: str
    filename: str
    title: str
    content_hash: str              # sha256(文件原始字节)
    text: str = ""                 # 解析出的归一化 UTF-8 纯文本（5.1 需要；2.1 原清单遗漏，W1 修订补充）
    metadata: dict[str, Any] = field(default_factory=dict)  # source/kind/page/author 等（见下）
    chunks: list[Chunk] = field(default_factory=list)

@dataclass
class Job:
    job_id: str
    filename: str
    status: str                     # pending | running | success | failed
    document_id: str | None = None
    chunk_count: int = 0
    error_code: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

@dataclass
class EvaluationResult:
    passed: bool
    score: float
    reason: str                     # ok | below_threshold | empty_context | query_too_long

@dataclass
class DetectionResult:
    verified: bool
    suspected: list[str]           # 未找到支撑的句子
    verified_rate: float           # 已证句子 / 总句子，0~1

# Chunk.metadata 保留字段（一致，勿改键名）
#   source: 原始文件名       page: 页码(int/None)
#   author: 作者(str/None)   kind: 文件类型后缀，如 "pdf"
```

### 2.2 统一响应包与错误码

所有 REST 响应统一封装：

```json
{ "code": 0, "message": "ok", "data": {...} }
```

错误码（`doc.领域编制错误` 编号稳定，前端/UI 靠它对文案）：

| code | 含义 |
|---|---|
| 0 | 成功 |
| 40001 | 参数校验失败 |
| 40002 | 入库被拒：文档疑似包含提示注入指令（P-C，`injection.mode=reject` 时命中） |
| 40003 | 入库被拒：文档未通过内容审核（P-C，`moderation.mode=reject` 时命中） |
| 40101 | 鉴权失败（`api.auth_enabled=true` 时）；**V5.1：不可访问/越权的知识库、Job 查询统一 40101**（Query/Upload/Delete/Kbs 各路由一致，不再混用 40001/40401） |
| 40401 | 文档/Job/知识库不存在（S5：Job 越权查询对外统一表现为 40401「不存在」以不泄露存在性） |
| 40440 | 路由未匹配（SPA catch-all 对未知 `/api/*` 返回，替代吞成 HTML） |
| 40500 | 方法不允许（框架级 405 统一包装） |
| 30001 | LLM（DeepSeek）调用失败/超时 |
| 30002 | 向量化失败 |
| 30003 | 解析失败（损坏文件/格式不支持） |
| 30004 | 重排打分失败（bge_reranker，语义区别于向量化失败 30002） |
| 21001 | 兜底：反思达到上限仍未找到可靠答案 |
| 21002 | 全局请求超时 |
| 43001 | 系统繁忙（并发查询信号量耗尽/超时，V5.1） |
| 43002 | 请求过于频繁（登录之外接口限速，V5.1） |
| 43003 | 入库队列已满（背压，V5.1） |
| 50001 | 未知内部错误 |
| 50003 | 服务重启，残留任务中断（V5.1） |

> 幂等说明：重复上传同一 `content_hash` 文档时返回 `code=0` 且 `data.duplicate=true`、`data.document_id` 为既有 ID，不重复入库。

### 2.3 全局约定（防"各写各的"）

- **时间**：所有耗时字段单位为毫秒（`elapsed_ms`）。
- **ID**：`chunk_id` 用上述 hash；`document_id` 用 `uuid4` hex 前 12 位；`job_id` 用 `uuid4` 前 8 位。
- **超时语义**：宿逻辑各模块自身超时（LLM 30s、检索 10s），且 `orchestration.request_timeout_seconds`（默认 30s）兜底整体请求。
- **幂等上传**：同一文件（相同 `content_hash`）重复上传直接命中成功并返回既有 `document_id`，不重复入库。
- **审计与日志**：每个请求以 `X-Trace-Id`（或内部生成）贯穿所有模块日志，格式为 JSON。

---

## 第 3 章 模块接口（`core/interfaces.py`）

> 所有实现位于 `app/adapters/`。接口是运行契约，实现必须满足其签名与语义，不允许改名成交付。

### 3.1 VectorEngine（嵌入）

```python
class VectorEngine(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本（无查询指令前缀）。必须保持 texts 与返回长度一致。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """向量化查询（嵌入模型如需要，需自动加查询指令前缀，见 5.3）。"""
```

**对齐硬约束**：`embed_documents` 与 `embed_query` 输出向量维度必须恒等于 `dimension`，且所有块同维。启动时若配置维度与模型实际维度不符 → 启动即报错 `SystemConfigError`。

### 3.2 VectorStore（向量库）

```python
class VectorStore(ABC):
    @abstractmethod
    def add_chunks(self, chunks: Iterable[tuple[Chunk, list[float]]],
                   collection: str = "kb") -> None:
        """批量写入 (块, 向量)，collection 存在则追加。"""

    @abstractmethod
    def search(self, query_vector: list[float], k: int,
               filters: dict | None = None, collection: str = "kb") -> list[ScoredChunk]:
        """余弦相似度检索 Top-k，按 score 降序。filter 支持 metadata[key]==value 等值过滤。"""

    @abstractmethod
    def delete_document(self, document_id: str, collection: str = "kb") -> int:
        """级联删除该文档所有块；返回删除条数。"""

    @abstractmethod
    def count(self, collection: str = "kb") -> int: ...

    @abstractmethod
    def drop_collection(self, collection: str = "kb") -> None: ...
```

- MVP：`ChromaStore`（persist 目录由配置 `vector_store.persist_dir` 指定）。
- 向量检索得分归一化由 `VectorStore` 返回（Chroma 默认返回距离，需转相似度时由实现决定算法并文档注明）。

### 3.3 Retriever（检索）

```python
class Retriever(ABC):
    @abstractmethod
    def retrieve(self, query: str, k: int, filters: dict | None = None) -> list[ScoredChunk]:
        """返回降序排列的 k 条候选，且带 bm25/vector 混合评分（见 5.4）。
        filters: 检索过滤条件，形如 {"metadata": {"department": "HR"}}，
                 透传给 VectorStore.search（全文检索侧也按同条件过滤）。"""
```

- `HybridRetriever`（默认）：BM25 + 向量 RRF 融合，见 5.4。实现时必须把 `filters` 同时应用到 BM25 与向量两路（否则过滤不生效）。
- `RerankRetriever`（后续）：RRF 召回前 50 → Reranker 精排前 5。

### 3.4 Reranker（重排）

```python
class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        """对已召回块重排序（可返回相同列表）。"""
```

- `IdentityReranker`（默认 MVP）：直通，仅 `ScoredChunk.score` 保持 RRF 得分。
- 职责注记：**RRF 融合只在 Retriever 内做**；Reranker 只做"精排阶段"，MVP 用直通占位，不得重复实现 RRF。

### 3.5 Evaluator（可达性评估）

```python
class Evaluator(ABC):
    @abstractmethod
    def evaluate(self, query: str, chunks: list[ScoredChunk]) -> EvaluationResult:
        """纯函数、无副作用，返回是否通过及原因。"""
```

- `RuleEvaluator`（默认），算法见 5.5。
- `ModelEvaluator`（后续）：调用 LLM 打分。

### 3.6 QueryRewriter（查询改写）

```python
class QueryRewriter(ABC):
    @abstractmethod
    def rewrite(self, query: str, feedback: EvaluationResult) -> str: ...
```

- `RuleRewriter`（默认），规则见 5.6。
- `LLMRewriter`（后续）：调用 LLM。

### 3.7 HallucinationDetector（幻觉检测）

```python
class HallucinationDetector(ABC):
    @abstractmethod
    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult: ...
```

- `CitationTracingDetector`（默认），算法见 5.8。
- `LLMCrossCheckDetector`（后续）。

### 3.8 LLM（生成/思维）

```python
class LLM(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, str]],
             temperature: float = 0.1, max_tokens: int = 1024) -> str:
        """messages: [{"role":"system"...},{"role":"user"...}...]；返回 assistant 文本。"""
```

- `DeepSeekLLM`（**默认**）：OpenAI 兼容。
  - `base_url = base来自配置 llm.base_url`（默认 `https://api.deepseek.com`）
  - 模型默认 `deepseek-chat`（配置 `llm.model`）
  - 认证：`Authorization: Bearer <api_key>`，key 只来自环境变量 `RAG_DEEPSEEK_API_KEY`
  - 超时 30s；失败时抛 `LLMUnavailableError` → 对外 code 30001
- `OllamaLocalLLM`（离线场景可选）：本地 Ollama。
- `OpenAICompatibleLLM`（通用）：其他 OpenAI 兼容网关。

### 3.9 Parser 与 Chunker（纯函数，不要求实现接口，但签名固定）

```python
def parse_file(file_stream: BinaryIO, filename: str) -> Document:
    """抽出文本与 page/author 等 metadata 组成 Document（不含 chunks）。"""

def split_chunks(doc: Document) -> list[Chunk]:
    """按父子分块规则（5.2）产出一组 Chunk；Chunk.parent_id 关联规范。"""
```

### 3.10 Provider 实现状态速览（V5.0）

| 模块 | 默认（当前配置） | 已实现 | 预留（白名单已列、未实现） |
|---|---|---|---|
| embedding | `local_bge_v15` | local_bge_v15 | bge_m3、remote_api |
| vector_store | `chroma` | chroma | milvus |
| retriever | `hybrid_rrf` | hybrid_rrf | hybrid_rerank |
| reranker | `bge_reranker` | bge_reranker、identity(回退) | —— |
| evaluator | `semantic_v2` | semantic_v2、rule(回退) | —— |
| rewriter | `llm` | llm、rule(回退) | —— |
| detector | `semantic` | semantic、citation(回退) | —— |
| llm | `deepseek` | deepseek | ollama、openai_compat |

- 预留 provider 配置到会**启动即报错**（4.3 禁止静默降级），实现后再切配置即生效（13.10 P3）。
- 本地模型 adapter 统一继承 `app/adapters/base.py` 的 `LocalModelAdapter`（懒加载/探活/异常包装复用，13.11.2）。

---

## 第 4 章 配置体系

### 4.1 加载与覆盖优先级

`内置默认值 < configs/settings.yaml < 环境变量（RAG_ 前缀）`

环境变量命名：例如 `RAG_LLM_PROVIDER=deepseek`、`RAG_EMBEDDING_MODEL_NAME=...`（`_` 分隔大写），优先级最高；允许 `.env` 文件（本地）。

### 4.2 settings.yaml 完整 Schema（当前默认，与 configs/settings.yaml 一致）

```yaml
env: dev

api:
  host: 127.0.0.1            # S2：默认仅本机；0.0.0.0 需显式配置暴露
  port: 8000
  auth_enabled: true         # S1：契约默认 false（个人模式）；此处为 settings.yaml 现值（当前部署已开启团队模式）
  query_rate_per_min: 20     # V5.1：/api/query 每 IP 每分钟上限（超限 43002）
  upload_rate_per_min: 10    # V5.1：/api/documents/upload 每 IP 每分钟上限（超限 43002）
  cors_origins: ["http://localhost:5173"]   # dev 模式 vite proxy 之外的白名单

frontend:                    # 13.7：SPA 托管
  dev_port: 5173
  dist_dir: ./frontend/dist

embedding:
  provider: local_bge_v15    # local_bge_v15（默认）| bge_m3 | remote_api（预留）
  model_name: BAAI/bge-small-zh-v1.5
  query_instruction: "为这个句子生成表示以用于检索相关文章："
  device: auto               # auto|cpu|mps
  batch_size: 64
  dimension: 0               # 0 表示由模型自动识别（运行时回填实际维度）

chunker:                     # 父子分块（5.2）
  parent_target_tokens: 512
  child_target_tokens: 128
  token_char_ratio: 1.8

vector_store:
  provider: chroma
  persist_dir: ./data/chroma
  collection: kb

retriever:
  provider: hybrid_rrf
  top_k: 20
  candidates: 50
  rr_fused_k: 60

reranker:
  provider: bge_reranker     # bge_reranker（默认，13.2）| identity（回退）
  model_name: BAAI/bge-reranker-base   # CPU 快；可切 v2-m3（慢一个量级）
  rerank_top_k: 5
  batch_size: 32
  device: auto

evaluator:
  provider: semantic_v2      # semantic_v2（默认，13.3.1）| rule（回退）
  rule_threshold: 0.45
  semantic_v2_threshold: 0.45
  top_n: 5

rewriter:
  provider: llm              # llm（默认，13.3.2，失败自动回退 rule）| rule
  synonyms_csv: ./configs/synonyms.csv
  llm_timeout_seconds: 30

detector:
  provider: semantic         # semantic（默认，13.4）| citation（回退）
  similarity_threshold: 0.8
  semantic_similarity_threshold: 0.6

llm:
  provider: deepseek
  model: deepseek-chat
  base_url: https://api.deepseek.com
  temperature: 0.1
  max_tokens: 1024
  timeout_seconds: 30
  # api_key 只允许环境变量注入（RAG_DEEPSEEK_API_KEY / RAG_LLM_API_KEY），禁止写入本文件

orchestration:
  max_reflection_iterations: 3
  search_timeout_seconds: 10
  request_timeout_seconds: 30
  max_concurrent_queries: 4              # V5.1：并发查询上限，耗尽/等待超时返回 43001
  semaphore_acquire_timeout_seconds: 5.0 # V5.1：获取并发信号量的等待超时（秒）

ingestion:
  max_file_size_mb: 50
  allowed_extensions: [pdf, docx, xlsx, pptx, md, txt]
  embed_batch_size: 64
  max_queue_size: 64             # V5.1：入库队列上限，满时返回 43003
  zip_max_total_mb: 200          # V5.1：zip 系格式解压总大小上限（防炸弹 S2）
  zip_max_entries: 1000          # V5.1：zip 条目数上限（防炸弹 S2）
  # desensitize_enabled: false   # S5：脱敏开关（config.py 默认 false，settings.yaml 未显式配置）

documents:                       # V5.0：文档索引持久化（D1）
  persist_file: ./data/documents.json

kbs:                             # 13.5：知识库映射持久化
  persist_file: ./data/kb.json

logging:
  level: info
  json: true
```

### 4.3 配置校验（写入 pytest 的契约测试）
- 任何未知 provider 值、`llm` 段缺失、`embedding.dimension` 与模型实际不符 → 启动抛 `SystemConfigError`，禁止静默降级。
- 允许"后续阶段替换"，但 MVP 配置须保持以上 4.2 默认完整可运行。

---

## 第 5 章 详细实现设计

### 5.1 文档解析 `parse_file`
- 格式：`.pdf/.docx/.xlsx/.pptx/.md/.txt`（大小 <= `max_file_size_mb`）。
- 统一产出 `Document`：`text` 归一为 UTF-8；元数据写入 `metadata`：`source`（原文件名）、`page`（PDF/Word 页码）、`kind`（扩展名）。
- PDF → 用 `pymupdf`（fitz）逐页提取；Word → `python-docx` 段落级；Excel → 每 sheet 每行判为一段文本，`metadata` 记录 `sheet` 与行号；PPT → `python-pptx`。
- 失败（损坏/不支持）→  抛 `ParseError（code 30003），Job 标记 failed。

### 5.2 智能分块 `split_chunks`（父子块）
- 参数：配置 `chunker.parent_target_tokens=512`、`child_target_tokens=128`、`token_char_ratio=1.8`（中文近数 `len(text)/1.8` 估算 token）。
- 规则：
  1. 以自然段落/章节为切割单元；段落超目标则继续按 `。！？；` 句子切；禁止在词中断。
  2. 每产出**父块**（≤ 512 tokens）必要完整；父块再按 `child_target_tokens` 切**子块**（≤128 tokens）。
  3. 父块 `parent_id=None`；子块 `parent_id=父块chunk_id`。
  4. `chunk_id = sha256(document_id + str(order))[:16]`。
- 检索命中子块时，编排层回溯其父块全文进入 LLM 上下文。

### 5.3 向量化（M-VP 本地 BGE v1.5）
- 默认模型 `BAAI/bge-small-zh-v1.5`（稠密 512 维，中文优、体积 ~100MB、CPU 友好）。
- **查询指令（关键）**：BGE 系中文模型的 `embed_query` 必须自动加前缀 `query_instruction`（默认 `"为这个句子生成表示以用于检索相关文章："`）；文档文本**不加**该指令。两方向维度必须一致。
- 向量化默认 `normalize_embeddings=True`（余弦相似即点积）。
- 设备：`device=auto`（Mac/Air 默认 cpu；GPU 存在时自动选 cuda）；`mps` 仅当显式配置且验证通过。
- 切换模型 = 改 `embedding.model_name`，不改代码（模型自动下载到 HF 缓存）。
  含可选：`BAAI/bge-base-zh-v1.5`（768 维，略优）、远程 API（`provider=remote_api`，`embedding.model_name="qwen3-embedding-0.6b"` 等）。

### 5.4 混合检索与 RRF（HybridRetriever）

```python
def rrf_fusion(bm25_scores: dict[str, float], vector_scores: dict[str, float],
               k: int = 60, top_n: int = 50) -> list[tuple[str, float]]:
    fused: dict[str, float] = {}
    for rank, doc_key in enumerate(sorted(bm25_scores, key=bm25_scores.get, reverse=True)):
        fused[doc_key] = fused.get(doc_key, 0.0) + 1.0 / (k + rank + 1.0)
    for rank, doc_key in enumerate(sorted(vector_scores, key=vector_scores.get, reverse=True)):
        fused[doc_key] = fused.get(doc_key, 0.0) + 1.0 / (k + rank + 1.0)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)[:top_n]
```

- 实施：
  1. BM25：`rank_bm25`，中文先 `jieba` 分词后建索引；
  2. 向量：调用 `VectorEngine.embed_query(query)` → `VectorStore.search`；
  3. 融合：两条排名各按公式累积分数 → 降序取 `top_k`；
  4. 去重：同一 `parent_id` 子块最多取 2 条进入上下文，防同一个文档霸屏反馈。
- **回归测试要点**：禁止 `sorted(key=lambda x: x)`——按分数（`item[1]`）降序排序才正确。

### 5.5 规则评估器（RuleEvaluator）—— 回退 provider（13.3.1 语义评估为默认）

> V5.0 起默认 `evaluator.provider: semantic_v2`（见 13.3.1）；本节算法保留为回退与语义评估的副信号口径。

```
terms = jieba.lcut(query)                 # 去掉空白与停止词
covered = {t for t in terms if any(t in c.chunk.text for c in top_chunks)}
coverage = len(covered) / max(len(terms), 1)
context_len = sum(len(c.chunk.text) for c in top_chunks)
length_norm = min(1.0, context_len / 200)
score = 0.7 * coverage + 0.3 * length_norm
passed = score >= evaluator.rule_threshold        # 默认 0.45
reason: 空上下文→EMPTY_CONTEXT；terms 为空→OK；分数不足→BELOW_THRESHOLD
```

### 5.6 规则改写器（RuleRewriter）—— 回退 provider（13.3.2 LLM 改写为默认）按序执行
1. 全转半角、压缩连续空白。
2. 同义词扩充：读 `synonyms.csv`（如 `年假,休假,假期`）→ 扩展成 `q1 OR q2` 候选串。
3. 口语→书面词典（如 `咋→怎么`、`行不行→是否可行`）。
4. 上次 `反馈.reason==EMPTY_CONTEXT` 时，先删停用词再重切长词。
> 改写数量受 `max_reflection_iterations` 约束（5.9）。

### 5.7 答案生成（LLM）

```python
SYSTEM_PROMPT = (
 "你是一个企业知识库问答助手。只基于提供的知识库上下文回答；"
 "上下文不足时明确说明'知识库中未找到相关依据'。"
 "回答末尾必须为引用对应上下文编号，使用 [N]（N 为正整数）标注来源。"
)
USER_PROMPT = "上下文：\n{context}\n\n问题：{query}"
```
- 上下文组织：候选父块按 `1. <text>` 编号注入；模型在句尾以 `[N]` 标注。
- 参数：`temperature=0.1`（`llm.temperature`）、`max_tokens`、超时 30s。
- DeepSeek 实现细节：OpenAI SDK，POST `/chat/completions`；`deepseek-reasoner` 时忽略 `reasoning_content` 只取 `content`。

### 5.8 幻觉检测（CitationTracingDetector）—— 回退 provider（13.4 语义检测为默认）

- 契约：
  1. 以 `。！？` 切分 answer 为 `s1..sn`；
  2. 正则 `\[(\d+)\]` 提取每句引用集合；
  3. 无引用的整句记入 `suspected`；
  4. 对每句引用，取其 N 对应的上下文块，计算 **字符 2-gram Jaccard**：
     `sim(s,cand) = |bigrams(s) ∩ bigrams(cand)| / |bigrams(s) ∪ bigrams(cand)|`
  5. 有任一 `<t candidate >= similarity_threshold(=0.8)` → 判该句"已证"，否则 `suspected`；
  6. `verified = len(suspected)==0`；`verified_rate = 已证/总句`。

```python
def char_bigrams(s: str) -> set[str]:
    return {s[i:i+2] for i in range(len(s)-1)}
```
- 纯 Python，毫秒级，无网络。阈值可配（调高更准、过低漂移；建议用 `tests/eval` 校准）。

### 5.9 编排（OrchestrationService）——Agentic 循环（已实现，V5.0 修订版）

> V5.0 实际实现含 13.3.3 修订：多路检索融合、LLM 改写多变体、query 不变自动换路（去停用词/BM25-only/向量-only）、
> LLM 调用后与幻觉检测后各一次预算检查（预算硬收口）。

```
iteration = 0
filters = filters or {}                    # 来自 /api/query 的 filters（可选）
normalized = QueryNormalize(query)         # 内置、确定性、无 LLM
while True:
    chunks = retriever.retrieve(normalized, k=retriever.top_k, filters=filters)
    result = evaluator.evaluate(normalized, chunks)
    if result.passed:
        context = backtrack_parent_chunks(chunks[:reranker.rerank_top_k])  # 子块回溯父块
        answer = llm.chat(系统+用户 prompt)
        detection = detector.detect(answer, context)
        return Answer(answer, sources, iterations=iteration, hallucination=detection)
    elif iteration < max_reflection_iterations:
        iteration += 1
        query = rewriter.rewrite(query, result.reason)          # 改写后继续
    else:
        return FallbackAnswer(code=21001)   # 兜底文案
```

- **安全阀**：整体请求强制 `request_timeout_seconds`（asyncio.wait_for 或进程级 wrap），超时返回 21002；
- 每个阶段以结构化日志输出（含 iteration、各模块耗时）→ 便于 UI 里的"反思过程"面板。
- `top_k`、阈值均为配置，禁止散落魔数。
- `filters` 必须贯穿整个循环（改写后的查询仍应用同条件过滤），字段规格见 6.4。

### 5.10 上传流水线（IngestionPipeline + Jobs）

```
POST /api/documents/upload (multipart file)
  ├─ 校验：大小/扩展名 → 计算 content_hash
  │   └─ 若该 hash 已存在 → 返回既有 document_id（code=0, duplicate=true），不重复入库
  ├─ 创建 Job（status=pending）写 JobStore（内存 dict + threading.Lock）
  ├─ 提交到单线程后台 Worker（ThreadPoolExecutor, max_workers=2）
  │   └─ parse_file → split_chunks → (for parent chunks) embed_documents(batch) → vector_store.add_chunks
  │   └─ 成功→ status=success，document_id；失败→ status=failed，error_code
  └─ 立即返回 {job_id}
```

- MVP JobStore：进程内字典 + 轻量持久化（`data/jobs.json`，仅落盘未终态任务）。
  **重启语义（V5.1 修订）**：启动时把残留的 `pending/running` Job 统一标记为
  `failed`（error_code=`50003`「服务重启，任务中断」），前端轮询收到明确终止而非 404/超时误报；
  终态（success/failed）任务不入盘。生产可换 Redis/MQ（注释与接口不变）。
- **并发背压（V5.1 修订）**：Worker 队列有界（`ingestion.max_queue_size`，默认 64）；
  队列满时 `submit` 拒绝，Job 标记 `failed`（error_code=`43003`「入库队列已满，请稍后重试」），
  路由层据此返回契约包（HTTP 200 + body code，不静默排队）。
- 说明：**解析/向量化全程不占用 HTTP worker 阻塞线程**；/query 时才同步走各模块。
- **S5 脱敏（V5.0）**：`ingestion.desensitize_enabled=true` 时，parse 之后、分块之前对全文执行
  `app/services/desensitize.desensitize_text`（身份证/手机号/银行卡 → `***`），默认关闭。
- **幂等并发（V5.0）**：`_run` 内以 content_hash 为粒度加进程锁（`_lock_for`），幂等检查与写入原子化，杜绝 TOCTOU 双份入库。

### 5.11 插件注册与装配（PluginRegistry + wire()）

```python
class PluginRegistry:
    _registry: dict[str, type] = {}
    @classmethod
    def register(cls, name, impl): cls._registry[name] = impl
    @classmethod
    def get(cls, name, **cfg): return cls._registry[name](**cfg)
```
- `wire(config) -> Dependencies`：启动时按 provider 名实例化各组件的默认实现，注入编排服务与路由。
- `/api/plugins/switch`（**已实现，V5.0**）：`{"module","strategy","config"?}` → 校验（模块/provider 合法且已注册）
  → 深拷贝 config → `wire` 重建依赖（保留 KbStore）→ 原子替换 `app.state.deps/config`；
  装配失败 → 50001 且不替换（回滚语义）。写操作需登录且 V5.1 仅管理员可用；
  `/api/plugins/status` 只读、白名单豁免认证。
- 所有模块实例在启动时统一创建且可关闭（`close()`），禁止到处单例塞 import。

---

## 第 6 章 API 契约（完整）

### 6.1 统一响应包

```json
{ "code": 0, "message": "ok", "data": {} }
```

### 6.2 POST /api/documents/upload
- `multipart/form-data` 字段 `file`。
- 返回：

```json
{ "code": 0, "message": "ok", "data": { "job_id": "ab12cd34", "status": "pending" } }
```

### 6.3 GET /api/jobs/{job_id}

```json
{ "code": 0, "message": "ok", "data": {
   "job_id": "ab12cd34", "status": "success",
   "document_id": "doc_12ab34cd", "chunk_count": 120,
   "error_code": null } }
```

### 6.4 POST /api/query

请求：
```json
{
  "query": "如何申请年假？",
  "session_id": "uuid-123",        // 预留（当前未参与逻辑）
  "history": [{"role":"user","content":"…"}],   // V5.0：会话最近 ≤6 轮（不含当前问题），注入生成阶段理解指代
  "top_k": 5,
  "filters": {"metadata": {"department": "HR"}},
  "kb_id": "default",              // 13.5：目标知识库（与 collection 二选一，kb_id 优先，缺省 default）
  "debug": true
}
```
- `filters`（可选）：检索过滤条件，`metadata` 内键值须与入库时 `Chunk.metadata` 一致（如 `department`/`kind`/`source`）。未提供时检索全部知识库。
- `collection`（可选）：指定查询的知识库集合，默认 `vector_store.collection`（见 4.2；多知识库规划见 12 章）。
响应：
```json
{ "code": 0, "message": "ok", "data": {
   "answer": "根据员工手册，年假需提前 3 天在 OA 系统申请[1]。",
   "sources": [ {"document_id": "doc_12ab34cd", "filename": "员工手册.md", "page": 12,
                 "chunk": "年假申请需提前3天…", "score": 0.82} ],
   "citations": { "used": ["[1]"], "confirmed": 1.0 },
   "hallucination_check": "passed",
   "iterations": 1,
   "elapsed_ms": 118,
   "debug_trace": [ {"iter":0, "evaluator_score":0.71, "reason":"ok"},
                    {"iter":1, "rewritten":"申请年假的流程" } ]
} }
```
- `top_k` 受 `retriever.top_k` 上限约束；
- `sources[].filename` 为入库时的原始文件名（与 `Chunk.metadata.source` 一致），评测脚本据此匹配期望来源；
- 反思超限返回 `code=21001`，`data.answer` 为兜底文案（`"抱歉，暂未找到相关答案…"`）；
- 超时/LLM 失败返回 `code=21002/30001`。

### 6.5 文档管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/documents?page=1&size=20&kb_id=` | 分页（按创建时间倒序；kb_id 可选过滤，V5.0） |
| GET | `/api/documents/{id}` | 单文档元数据 |
| DELETE | `/api/documents/{id}` | 删除文档（级联清向量块；**按文档所属库解析 collection**，V5.0 修 P0-1） |

> 文档索引持久化：`data/documents.json`（V5.0，含 content_hash 幂等索引）；启动时索引为空且 Chroma 有数据
> → 自动全量重建（消除重启后幽灵文档）。

### 6.6 插件
| 方法 | 路径 | 入参 | 说明 |
|---|---|---|---|
| GET | `/api/plugins/status` | —— | 各模块当前 provider 信息 |
| POST | `/api/plugins/switch` | `{"module":"reranker","strategy":"bge","config":{}}` | 热切换；失败回滚 |

### 6.7 健康
- `GET /api/health` → 一次探活各组件（embedding 模型已加载、向量库可达、LLM 最小叫通单位 1 步），返回：

```json
{ "code": 0, "message": "ok", "data": {
   "status": "ok"|"degraded"|"down",
   "checks": {"embedding":"ok","vector_store":"ok","llm":"ok"},
   "version": "1.0.0" } }
```

### 6.8 认证与用户管理（V5.1）

- 登录：`POST /api/auth/login`（`{username, password}` → `{token, user}`）；Token 为 HMAC 签名、有效期 `auth.token_ttl_days`（默认 7 天）、payload 含 `token_version`（`ver`）；
  `GET /api/auth/me`；`POST /api/auth/change-password`。
- **防爆破**：连续登录失败 5 次锁定来源 IP 5 分钟（进程内 `_LoginGuard`，成功登录重置；逻辑见 13.11.1 S1）。
- **Token 吊销（S1）**：改密/删号/降权时 `token_version += 1`，旧 token 再访问一律 40101。
- 用户管理（仅 admin）：`GET/POST /api/users`、`POST /api/users/import`（CSV 批量导入：表头 `用户名,姓名,初始密码,角色`，全量校验通过才写入，失败返回逐行明细 `data.errors`）、
  `POST /api/users/{id}/reset-password`、`POST /api/users/{id}/role`、`DELETE /api/users/{id}`（禁删自己）。
- 认证豁免白名单：`/api/health`、`/api/plugins/status`、`/ui`+`/assets`、`/api/auth`（`AUTH_WHITELIST_PATHS/PREFIXES`）。
- 密码策略：新密码/重置密码 ≥8 位且同时含字母与数字；已有用户不强求改密（产品策略）。

---

## 第 7 章 内置 Web 测试页 `/ui`

- **目标**：开发完成后，无任何前端工程，浏览器直接打开 `http://<host>:8000/ui` 即可测全部功能。
- 文件：`web/index.html` + `web/app.js` + `web/style.css`，FastAPI `app.mount("/ui", StaticFiles(directory="web", html=True))`。
- **五个区块（与 API 一一对应）**：

| 区块 | 调用的 API | 展示 |
|---|---|---|
| 上传 | `POST /api/documents/upload` + `GET /api/jobs/{id}` 轮询 | Job 状态、chunk 数、错误码 |
| 文档 | `GET/POST/DELETE /api/documents/...` | 列表、删除 |
| 问答控制台 | `POST /api/query` | 回答 + 引用 + 来源 + 迭代数 + 耗时 + 幻觉检测徽标 |
| 反思过程（debug） | `/api/query`（`debug:true`） | `debug_trace` 折叠面板（每次迭代、得分） |
| 插件与健康 | `/api/plugins/status`·`/switch`、`/api/health` | 当前 provider、切换按钮、健康状态 |

- 交互细节：上传后 500ms 轮询 `/jobs`；`code!=0` 时高亮显示错误码；全局一个 polyfill 的 fetch 封装 `api()`。

---

## 第 8 章 测试与验收

### 8.1 单元测试矩阵（`tests/unit/`）

| 模块 | 测试项 |
|---|---|
| domain | 模型正例（空/超长）行为 |
| RRF | 构造确定 score dict 断言顺序（回归「key=lambda x」bug） |
| Evaluator | 阈值两侧确定性用例 + 空上下文 |
| Detector | 4 组答案：真支撑/假支撑/无引用/空串 → verified 断言 |
| VectorEngine | dimension 一致、查询前缀命中（把 query_instruction 拼接实时校验） |
| 配置 | 非法 provider、缺失 llm → SystemConfigError |
| Orchestration | 用 Mock 各接口驱动 3 次循环、超时、兜底路径 |

### 8.2 集成测试
- 真实 Chroma + 本地 BGE→资源 + DeepSeek（或 Mock），跑通：上传→入库→问答→检测→删除全链路（pytest「integration」标记）。
- 断点提示：集成测试不加 `llm` 依赖，失败时不追究 LLM（先验证链路）。

### 8.3 评测（`app/cli/rageval.py` + `tests/eval/qa_pairs.json`）
- `qa_pairs.json`：`[{query, golden_answer, source_doc}]`，至少 20 条起步，覆盖"有答案/无答案/改"的场景。
- 指标：`ContextPrecision / ContextRecall / Faithfulness / AnswerRelevancy / Hit@5`。
- 输出 Markdown 报告（逐条 + 聚合）到 `reports/eval-{timestamp}.md`。

---

## 第 9 章 开发计划与验收

**MVP（W1~W4）与 Phase2（W5~W8）均已交付并验收通过**（详见第 13 章交付状态）。
V1.0 发布标准已达成：pytest 全绿（`-m "not integration"`，293 项，2026-08-15 实测）、`/ui` 与 SPA 闭环
（上传→问→看引用→删）、`rageval` 可运行且报告非空（`reports/` 存档 20+ 份）。

---

## 第 10 章 部署与运维

### 10.1 运行环境
- 本地（MVP）：`uvicorn app.main:app --host 0.0.0.0 --port 8000`（同时托管 `/ui`）；DeepSeek 走外网。
- 容器（可选）：`docker-compose.yml` 服务 = `api`（含 `/ui`）、`chromadb`、`prometheus`、`nginx`（代理）。
- 生产演进：`vector_store.provider=chroma→milvus`（迁移脚本已预留）。

### 10.2 健康检查与监控
- `/api/health` 每种组件探测（嵌入模型已加载、Chroma 可连、LLM 通）。
- Prometheus 指标：各接口延迟分位、检索耗时、`reflection_iterations_count`、幻觉率、入库队列深度。
- 日志：JSON 结构化、`trace_id` 全链路贯穿。

### 10.3 环境部署文档
- `README.md` 写明：准备 DeepSeek key、首启动自动下载模型、`make run`/`make test`、`/ui` 路径。

---

## 第 11 章 风险与对策（开发/运营双视角）

| 风险 | 应对 |
|---|---|
| 本地嵌入 CPU 慢（Air 上 mvp） | 默认 `bge-small` 已轻量；仍慢则改 `batch_size` 或设备 mps；超时容忍（入库不阻塞 HTTP） |
| 查询指令忘拼（bge_zh 特性） | 5.3 已放入接口契约；单测覆盖 `embed_query` 是否含 `query_instruction` |
| DeepSeek 不可用/限流 | 超时+重试 3 次→返回 30001；One-pass 后可选 Ollama 兜底 |
| 幻觉检测误杀 | `similarity_threshold` 下调（0.8→0.75）→ 复测 Effect；用 `rage_eval` Faithfulness 校准 |
| 反思死锁 | `max_reflection_iterations` 配置 + `request_timeout_seconds` 强制超时 |
| 重复上传/幂等 | 以 `content_hash` 去幂等（已设计） |
| 解析乱版 Excel/PPT | Parser 单测覆盖表格/多 sheet 抽取 |

---

## 第 12 章 业务场景适配指引

本系统按"接口抽象 + 配置热切换"设计，面对不同业务场景有三种适配深度。开发不同业务实例时，**优先参考本章判断改动范围，不要盲目改核心代码**。

### 12.1 适配深度分级

| 深度 | 典型场景 | 需要做的事 | 是否动核心代码 |
|---|---|---|---|
| **A. 零改动** | 换一批业务文档（制度/产品/FAQ） | 换文档集重入库、调阈值、换同义词表/分词词典 | 否，纯配置/资源 |
| **B. 小适配（推荐/正常）** | 新文档格式（.log/.ipynb/表单）、按字段检索、不同问答风格 | 新增 `Parser` 适配器；`/api/query` 传 `filters`、`collection`；改 Prompt 模板与评测集 | 仅新增 `app/adapters/` 下文件，接口契约不动 |
| **C. 需重新设计** | 多租户/权限隔离、秒级增量索引、跨系统实时同步 | 见 12.3 | 是，需按新设计评审 |

### 12.2 常规场景落地模板（第二层常见做法）

1. **新文档格式**：在 `app/adapters/parser/` 新增 `parse_<fmt>.py`，实现 `parse_file` 契约；在 `app/adapters/chunker/` 按需实现分块逻辑。注册进 `PluginRegistry`（key=`parser.<kind>`）即可，`/api/documents/upload` 自动按扩展名分发。
2. **字段级检索**（如只查 HR 部门 / 只查某产品线）：
   - 入库时给 `Chunk.metadata` 打标签（如 `department`、`product_line`、`source`）；
   - 调用方在 `/api/query` 传 `filters: {"metadata": {"department": "HR"}}`；
   - 实现须保证 `HybridRetriever` 把 filters 同时用在 BM25 与向量两路（见 3.3）。
3. **不同问答风格**（报告/客服/法规）：仅替换 5.7 的 prompt 模板与 `temperature`，以及配套的评测集（第 8.3）。
4. **多知识库**：`Chunk.metadata` 写入 `kb_id` 或用独立 `collection` 名（`vector_store.collection` + `/api/query.collection`）；MVP 先按"一个 collection = 一个知识库"组织，属零维护方案。

### 12.3 哪些情况需要重新设计（不建议过早做）

| 需求 | 现架构缺什么 | 何时再动 |
|---|---|---|
| 多租户/细粒度权限（不同用户只能看部分库） | 需引入"用户→collection/过滤条件"的鉴权映射，并防 metadata 伪造 | **已落地（轻量两级，V5.1）**：`Authorizer` + kb owner/scope（shared/private），用户→库判定见 `app/services/rbac.py`；权限矩阵：`can_upload/can_query` 仅限 shared 或 owner 自己的 private 库；`can_delete_kb` —— admin 可删任意库，private 库 owner 可删自己的库，shared 库仅 admin 可删；细粒度到文档级/角色矩阵扩展仍属深度 C |
| 秒级实时同步（文档经常更新） | 需增量索引 + 消息队列、文档变更监听 | 内容更新频率成瓶颈时 |
| 跨业务系统对接（OA/工单/数据库） | 需"接入器 + 数据变更事件"而非通用 RAG | 单独立项 |
| 亿级向量高并发 | Milvus + 多副本 + 横向扩展 | 单机瓶颈量化后再做（另见 10.1） |

> **总原则**：MVP 只做深度 A/B；深度 C 的四个方向，全部等"真实业务指标或用户量"驱动后再投入，不要提前债务化。核心的接口抽象与 `filters/collection` 已在本版预留。

---

## 第 13 章 第二阶段：效果优化与多端（Phase 2 交付状态）

> MVP（第 1~12 章）与 Phase2（W5~W8）**均已交付**。本章为交付状态 + 沿用规格：
> 各节"✅ 已交付"标注实现落点；13.10 P3 仍为"只立规格、不排期"；13.11 为非功能约束（与功能同权）。

### 13.1 目标与度量协议

- 阶段目标：把"检索-评估-生成"从 MVP 原型提升到可对比衡量的生产基线，并交付 **网页 + 桌面双形态** 前端。**已达成**。
- **度量协议（硬要求，持续有效）**：每完成一个里程碑，必须执行 `make eval` 存档报告 `reports/eval-{ts}.md`，并与基线报告对比后再回复"达标/不达标"。
- 基线（MVP 实测，2026-08-08，Stub LLM 进程内模式）：通过率 18/21；Hit@5/ContextPrecision/ContextRecall/AnswerRelevancy 100%；Faithfulness=0%（Stub 假答案所致）；no_answer 口径不一致。
- **V5.0 实测（2026-08-11，真实 DeepSeek，`--kb default`，报告 `reports/eval-20260811-002820.md`）**：
  - 通过率 14/21；**Faithfulness(direct)=56%**（Stub 假 0% 之下的真实值，接近目标 60%）；Hit@1=44%（受"评测集文档与当前知识库内容不匹配"影响，见第 14 章）；AnswerRelevancy 100%。
- **目标值执行情况**：
  - ✅ Faithfulness（direct）由假 0% 提升至真实 56%（semantic detector 落地）；
  - ✅ no_answer 判据修订（13.6）落地：107 条全量回归通过（Stub 模式 107/107）；
  - ✅ 多知识库隔离自动化用例全通过；口语化/改写类通过率 ≥80% 达成（qa_pairs 回归）。
  - ⚠️ Faithfulness ≥60% 目标：真实模式 56%，差值源于评测集/知识库内容差异，非检测器缺陷（Stub 全量模式验证过语义检测路径）。

### 13.2 检索精排（Reranker）—— ✅ 已交付

- 交付：`app/adapters/reranker/bge_reranker.py`（provider `bge_reranker`，默认），`identity` 保留回退。
- 模型：`BAAI/bge-reranker-base`（当前配置，CPU 单轮快一个量级）；改 `model_name` 可切 `BAAI/bge-reranker-v2-m3`（实现无需改动）。
- 实现：`sentence-transformers.CrossEncoder`（现有依赖）；对（query, 候选块）打分后降序；输出 `ScoredChunk.notion="rerank"`。
- 编排接入：检索 RRF 输出（`top_k=20` 候选）→ rerank → `rerank_top_k=5` 进入评估器。
- 懒加载/探活/异常包装继承 `LocalModelAdapter`（13.11.2 公共基类）。
- 测试：`tests/unit/test_bge_reranker.py`（确定性打分排序 + 空输入 + 桩模型注入）。

### 13.3 评估·改写·闭环（Evaluator / Rewriter / Orchestration）—— ✅ 已交付

#### 13.3.1 Evaluator：`rule` → `semantic_v2`（默认）
- 交付：`evaluator.provider: semantic_v2`（`app/adapters/evaluator/semantic_v2.py`，默认）；`rule` 保留回退。
- 算法：主信号 = query 与 top_n 块嵌入余弦的**候选集内相对分**（z-score 归一 [0,1]，护栏 0.35，不依赖绝对阈值）；
  副信号 = 去停用词字面覆盖；`score = 0.7×主 + 0.3×副`，passed ≥ `semantic_v2_threshold`（0.45）。
- 与 13.2 衔接：评估输入必须是 **rerank 后的 top_n**。

#### 13.3.2 Rewriter：`llm` provider（默认）
- 交付：`rewriter.provider: llm`（`app/adapters/rewriter/llm_rewriter.py`，默认）；`rule` 保留回退。
- 输入 `(query, reason)`，输出 1~3 个改写/扩句变体（JSON 数组，含代词消解）；超时 30s；
  失败/超时/限流/解析失败 → **自动回退** `rule` 改写；仅当 `result.passed=False` 才调用（成本控制）。
- **多路融合**：循环内对每个变体分别检索，候选用 `rrf_fusion`（5.4）融合后进入该轮评估。

#### 13.3.3 循环硬约束（已落地于编排）
1. **每轮 query 必须与上轮不同**（文本 diff 校验；若不变则自动换路：BM25-only / 向量-only / 去停用词重切）；
2. `max_reflection_iterations=3`、`search_timeout_seconds=10`、`request_timeout_seconds=30` 保持；
3. `filters`/`kb_id`（13.5）必须贯穿所有变体检索；
4. LLM 改写不可用 → 回退规则；规则也无变化时仍计入迭代（防死循环，超限走 21001 兜底）；
5. **预算硬收口（V5.0）**：LLM 调用后、幻觉检测后各补一次预算检查，超时抛 21002。

### 13.4 幻觉检测 v2（Detector）—— ✅ 已交付

- 交付：`detector.provider: semantic`（`app/adapters/detector/semantic_verifier.py`，默认）；`citation` 保留回退。
- 实现：① 规则不变：按 `。！？` 切句、正则 `[(\d+)]` 取引用、无引用整句 → suspected；
  ② 判定升级：句⊕引用块的**嵌入余弦**（阈值 `semantic_similarity_threshold=0.6`）替代字符 2-gram；
  ③ 输出 `verified/verified_rate/suspected` 契约不变。
- 实测：Faithfulness（direct）由 Stub 假 0% → 真实 DeepSeek 56%（13.1）。

### 13.5 多知识库（通用化，一库一 collection）—— ✅ 已交付

- 交付：**kb_id → Chroma collection（`rag_<kb_id>`）一一映射，物理隔离**；完全复用现有 collection 贯穿机制。
- 服务：`app/services/kb_store.py`（`KbStore`）：内存 + 持久化映射文件 `data/kb.json`；字段 `kb_id/name/description/collection/created_at`；内置默认库 `default`（不可删）。含 `on_kb_changed` 事件钩子（P3 增量索引预留）。
- API（`/api/kbs`）：
  | 方法 | 路径 | 行为 |
  |---|---|---|
  | GET | `/api/kbs` | 列表（含 `default`） |
  | POST | `/api/kbs` | `{name, description?}` → `{kb_id}`（新建集合） |
  | DELETE | `/api/kbs/{kb_id}` | 删除映射 + `drop_collection` 级联；拒绝删 `default` |
- 上传：`POST /api/documents/upload` 可选表单字段 `kb_id`（缺省 `default`）；入库时写 `Chunk.metadata.kb_id`。
- 问答：`POST /api/query` 可选 `kb_id`（与 `collection` 二选一，`kb_id` 优先；缺省 `default`）；`filters` 仅限库内。
- 隔离保障：检索双路（BM25/向量）都按 kb 的 collection 作用域执行；`KbStore` 校验查询/上传目标库存在，否则一律按参数错误拒绝。
- 验收：隔离自动化用例全过（`tests/integration/test_kbs_isolation.py`）。

### 13.6 评测体系 v2 —— ✅ 已交付

- `tests/eval/` 已扩容至 **107 条**（`qa_pairs/{default,docs}/*.jsonl`），类型覆盖 direct/rewrite/no_answer/no_invent/口语/表格/多文档。
- **no_answer 判据修订（已落地）**：`pass =（返回 code0 且 answer 含"未找到相关依据"类文案）∨（返回 21001）`（`rageval.py` 单一常量 + 单测）。
- 指标：`Faithfulness` 分类型、`Hit@1`、按库汇总、基线 diff 列。
- CLI：`--kb`（按库）、`--all-kbs`、`--baseline <path>`（对比模式）、`--in-process`（Stub LLM 进程内）/ `--base-url`（HTTP 真实后端）；认证启用时 HTTP 模式加 `--login <用户> <密码>` 或 `--token <token>`。
- **评测口径统一（13.11.2）**：进程内模式走 `orchestration.run` 同一链路，禁止两套实现。
- 报告存档：`reports/eval-{ts}.md`（20+ 份，含本轮真实 DeepSeek 报告）。

### 13.7 前端与多端（前后端分离）—— ✅ 已交付

- **决策**：前后端彻底分离。前端工程 `frontend/`：**Vue3 + Vite + Vue Router + Pinia + Element Plus**；桌面端复用同一 SPA。
- 页面（五区块）：
  1. 问答控制台：库选择、提问、引用 `[N]`、来源、幻觉徽标、已证率、debug 反思面板（默认收起）；
  2. 文档管理：按库上传（`kb_id`，带上传/入库进度）+ 按库列表 + 删除；
  3. 知识库管理：`/api/kbs` CRUD（删除带确认弹窗）；
  4. 设置总览：服务状态红绿灯（`/api/health` 10 秒自动轮询）、组件详情（折叠，中文+英文专名对照）、用户管理（仅管理员）、登录/退出、外部后端（IPC）；
  5. 错误码提示条（`api()` 统一语义：网络/解析失败返回 `{code:50001}` 契约包，不 throw）。
- 状态管理：问答会话在 Pinia store（切页不丢）；携带 `history`（最近 6 轮，不含当前问题）供后端指代解析。
- **Web 模式**：dev 用 vite proxy（`/api → http://127.0.0.1:8000`，CORS 放行 `http://localhost:5173`）；生产 `vite build` 产出 `frontend/dist`，由 FastAPI 挂载 `/`（SPA catch-all，未知 `/api/*` 返回 404 JSON）。
- **桌面模式（Electron，已交付）**：
  - `frontend/electron/main.cjs`：① 探测端口（默认 8000，占用自动换）→ ② 以项目 `.venv` 拉起 uvicorn（仅 127.0.0.1）→ ③ 轮询 `/api/health` 就绪（带 splash 窗口）→ ④ 退出级联停止后端；
  - **运行中后端意外退出 → 自动重启**（带 splash 重新等待就绪，最多 2 次；恢复失败弹错误框并以 `app.exit()` 强退——避免 macOS 模态弹窗下退不掉）；关窗常驻期间后端退出则静默，Dock 激活时自动拉起；
  - 外部后端模式（userData 持久化，不本机拉起）；设置页经 IPC 回显真实值；
  - macOS 惯例：关窗保留 Dock 与后端，`activate` 重建窗口，Cmd+Q 才退出；
  - S8：`contextIsolation=true`、`nodeIntegration=false`、`sandbox=true`，仅 loopback/自托管地址；
  - 测试：`npm run test:desktop`（`node --test electron/*.test.cjs`，11 用例）；`npm run desktop` 启动。
- 旧 `web/` 测试页保留为冒烟/降级入口（README 注明）。

### 13.8 配置与迁移（settings.yaml 已落地）

- **当前完整 schema 以第 4.2 节为准**（含 reranker/evaluator/rewriter/detector 新 provider、`cors_origins`、V5.1 的 rate/queue/zip 上限、`documents/kbs` 持久化路径）。
- 各模块可配置切换 provider；`identity/rule/citation` 为可回退默认。依赖：reranker 用 `sentence-transformers.CrossEncoder`（现有依赖，零新增）；
  如改 `FlagEmbedding` 须先登记 `pyproject` 并说明理由（附录 A 约束）。
- **迁移纪律**：未识别配置键会被静默忽略（留隐患）——settings.yaml 只保留已生效键且带注释；V5.1 起 W5~W8 全部键已落地，不再有"规划注释段"。

### 13.9 演进路线与 DoD（W5~W8）—— ✅ 全部交付

| 周 | 范围 | 状态 |
|---|---|---|
| W5 | 13.2 + 13.3 + 13.4（P0 后端三件套） | ✅ 交付（reranker/semantic_v2/llm_rewriter/semantic detector） |
| W6 | 13.5 + 13.6（多库 + 评测扩容） | ✅ 交付（/api/kbs CRUD、隔离用例、qa_pairs 107 条、no_answer 修订判据） |
| W7 | 13.7 Web 前端 | ✅ 交付（SPA 五区块、CORS、FastAPI 托管 dist） |
| W8 | 13.7 Electron 桌面 + 生产项 | ✅ 交付（内置拉起后端/splash/外部后端/macOS 惯例；`npm run desktop` 可跑） |

> 新功能开发时在 commit 中标注里程碑（如 `PHASE2-Wn` / `V5.0-*`）。

### 13.10 可选适配器包（P3 预留规格，暂不实现）

> 本节的五类扩展**只立规格、不排期**；启动时机以 13.1 度量协议 + 用户决策为准。全部属第 12 章「深度 B」：仅新增/扩展 `app/adapters/` 实现，接口契约与核心编排不动。

| # | 扩展 | 要点 | 验收 |
|---|---|---|---|
| 1 | OCR（扫描件 PDF） | 文本层缺失/字密度极低 → `needs_ocr` 走 OCR 适配器；懒加载 + `parser.ocr.enabled=false` 默认关；评估集单列（识别错字影响检索） | 10 份含图 PDF 抽文且检索命中 ≥ 阈值 |
| 2 | 表格结构保留 | xlsx/表格区输出结构化标记（`\|列名\|值\|`）、表头成块（`kind==tab`）；支持列-值问答，检索覆盖 `table_cells` 元数据 | 表格问答 qa_pairs 子集通过率达标 |
| 3 | 多分块策略+混合分派 | `chunker.provider: mixed` 按 kind 自动分派；父/子保留为 `hierarchical`；**每加策略必须先用 qa_pairs 标定再上配置** | 各 kind 命中率 ≥ 基准 + `make eval` 对比报告 |
| 4 | embedding 多模型 | 配置切换 `bge-small/base/large-zh`/`bge-m3`；换模型=维度变化 → `make reindex` 全量重入库 + `meta.embed_model` 版本管理 | 两档切换指标（Hit@1/Faithfulness）对比有记录 |
| 5 | LLM 多协议 | 补 `OllamaLocalLLM`/`OpenAICompatibleLLM`（§3.8 接口）+ 前端模型选择下拉；DeepSeek 失败可 One-pass→Ollama 兜底 | 三 provider 各跑通 1 条问答 |

### 13.11 非功能需求与工程化底线（安全 / 复用 / 拓展）

> 本节约束 W5 之后的所有里程碑，**与功能同权**。分「安全底线（必须）」「复用/拓展（规范）」「优先级（建议实施序）」三块；安全底线未达标视为 DoD 不通过。

#### 13.11.1 安全底线（必须）—— 状态表

| # | 项 | 状态（V5.0） | 落点 |
|---|---|---|---|
| S1 | **API 认证** | ✅ 密码登录 + HMAC Token（V5.1；契约默认 `auth_enabled=false`，当前部署已开启）；启用后 `RAG_ADMIN_USER/RAG_ADMIN_PASSWORD` 引导首管理员；**防爆破**：连续失败 5 次锁定来源 IP 5 分钟（`_LoginGuard`，成功重置）；改密/删号/降权吊销旧 token（`token_version`） | `app/api/auth.py` |
| S1a | **密码策略（V5.1）** | 新密码/重置密码：≥8 位且同时含字母与数字（纯标准库校验，不引依赖）；已有用户不强求改密（产品策略），弱口令告警见 README「已知限制」 | `app/services/user_store.py`（validate_password_strength） |
| S2 | **监听地址** | ✅ 默认 `127.0.0.1` | `api.host` |
| S3 | **XSS 治理** | ✅ 前端统一模板插值，禁 innerHTML/v-html | SPA `api()` + 组件渲染 |
| S4 | **Prompt 注入防护** | ✅ 系统提示声明"文档区不可执行指令" | `orchestration.SYSTEM_PROMPT` |
| S5 | **敏感数据** | ✅ 脱敏开关 `ingestion.desensitize_enabled`（默认 false，V5.0 落地）；**启用条件与合规建议（V5.1）**：HR/财务等含身份证/手机号/银行卡的文档建议开启——正则替换为 `***` 后入库（全文+向量均脱敏）；开启后影响检索命中（号码类查询将失效），需业务侧评估；改策略前必须跑 `make eval` 对比 | `app/services/desensitize.py` |
| S6 | **审计** | ✅ query/upload 结构化审计（user_id/kb_id/摘要/trace_id，不含 key 与完整敏感文本，V5.1）；**日志含用户查询与回答摘要，属个人数据**：日志文件 0o600（桌面 `backend.log` 已收紧），访问控制与留存由部署方制度化管理 | `app/api/query.py`、`documents.py`、`core/logging.py` |
| S7 | **依赖与模型** | ✅ pyproject 版本锁定；扫描/哈希校验为可选（未接） | pyproject.toml |
| S8 | **Electron 安全** | ✅ contextIsolation/nodeIntegration:false/sandbox、仅 loopback、密钥运行时注入 | `frontend/electron/main.cjs` |

#### 13.11.2 复用性规范（必须遵守的工程约定）

- **评测口径统一**：✅ 已落地——`rageval` 与 `/api/query` 走同一编排链路核心（`orchestration.run`），no_answer 判据单一常量（13.6）。
- **provider 公共基类**：✅ 已落地——`app/adapters/base.py` `LocalModelAdapter`（懒加载/device/探活/异常包装复用）；本地模型 adapter（embedding/reranker）已继承。
- **前端资产化**：✅ 桌面与 Web 共享 `BASE_URL` 抽象（`frontend/src/api/base.js` 单一常量 + `localStorage`/IPC 外部后端覆盖）。
- **样例/评测集资产化**：✅ qa_pairs 107 条按库归档；新格式/新策略上线前必须跑资产回归。
- **契约同步**：⚠️ 未自动化——后端 `openapi.json` → 前端 TS 类型仍为手写（前端规模小、接口稳定，暂不引入生成器；接口变更时必须同步更新前端调用）。

#### 13.11.3 拓展性规范（预留扩展点，禁止封闭实现）

- **热切换延续**：✅ 已实现——`/api/plugins/switch` 运行期换 provider（校验/重建/原子替换/回滚），P3 新增 provider 继续遵守。
- **事件/增量预留**：✅ 部分——KbStore `on_kb_changed` 钩子已留；文档变更→缓存失效已由 `mutation_epoch`（add_chunks/delete_document 递增）覆盖，BM25/父块缓存按 epoch 显式失效（2026-08-16 核实）；通用事件总线留待增量索引需求。
- **API 版本化**：⚠️ 未启用——当前无破坏性变更；一旦出现，新端点挂 `/api/v1` + 兼容层。
- **多租户扩展点**：✅ `user→kb` 鉴权映射做成接口（`app/services/rbac.py`，默认全放；注入 `user_kb_map` 即收窄）。
- **指标可插拔**：⚠️ 指标在 rageval 内实现（MRR/NDCG 未注册化）；未来扩展时按注册式改造。
- **CI 基线门禁**：⚠️ 未接入 CI；人工对比报告为最低要求（reports/ 存档）。

#### 13.11.4 优先级（建议实施序）

| 序 | 项 | 时机 |
|---|---|---|
| 1 | S3 XSS 治理 + S2 默认 127.0.0.1 | 立即（随 W7 前端一并落地） |
| 2 | 评测口径统一 + CI 基线门禁（13.11.2/13.11.3） | W5/W6 内 |
| 3 | S1 认证 + RBAC 预留 | W8（桌面发布前） |
| 4 | S4~S8、其余复用/拓展规范 | 随功能迭代逐项落实，禁止"上线前突击" |

> 13.11.4 各优先级项均已落地（CI 门禁为建议项，人工对比报告为最低要求）。

---

## 第 14 章 当前状态与遗留（V5.1，2026-08-15）

> 给后续开发/运维 Agent 的**现状快照**：当前可运行形态、常用命令、已知遗留与触发条件。

### 14.1 当前运行形态

- **后端**：`uvicorn app.main:app --host 127.0.0.1 --port 8000`（S2）；**当前部署 `auth_enabled=true`（团队模式，本机账号 admin/admin123、user1）**；契约默认 `false`（个人模式）；团队模式首管理员经 `RAG_ADMIN_USER/RAG_ADMIN_PASSWORD`（或 CLI 引导）创建。
- **模型**：embedding `bge-small-zh-v1.5`（本地）+ reranker `bge-reranker-base`（本地）+ LLM `deepseek-chat`（唯一外部调用，key 经 `RAG_DEEPSEEK_API_KEY`）。
- **数据**：`data/chroma`（向量，`rag_<kb>` 集合）+ `data/kb.json`（知识库映射）+ `data/documents.json`（文档索引，重启自动从 Chroma 兜底重建）。
- **前端**：`frontend/dist` 由后端托管（SPA，静态资源 no-store）；桌面端 `npm run desktop`（或打包 App，自动拉起后端）。
- **评测集**：`tests/eval/qa_pairs/{default,docs}/` 107 条；报告 `reports/eval-{ts}.md`。

### 14.2 常用命令

| 命令 | 说明 |
|---|---|
| `make bootstrap` | venv + 依赖 |
| `make run` | 启动后端（8000） |
| `make test` | 全量单测（`-m "not integration"`，293 项，2026-08-15 实测） |
| `make eval` | 进程内评测（Stub LLM）并存档报告 |
| `make desktop` | 启动 Electron 桌面（前端需先 `npm run build`） |
| `cd frontend && npm run build` | 重建 SPA（静态托管实时生效） |
| `cd frontend && npm run test:desktop` | Electron 壳测试（11 用例） |
| `python -m app.cli.rageval --base-url http://127.0.0.1:8000 --all-kbs --baseline reports/<最新>` | 真实 DeepSeek 评测 + 基线对比 |

### 14.3 当前实测指标（2026-08-11 存档，历史基线）

- Stub 进程内全量（107 条）：**107/107 通过**，Hit@1=90%，Hit@5=100%，AnswerRelevancy=100%（Faithfulness 为 Stub 假 0%，无参考意义）。
- 真实 DeepSeek（`--kb default`，21 条，当前知识库 = 5 份业务文档）：**14/21 通过**，Faithfulness(direct)=**56%**，Hit@1=44%。
  - Hit@1 低于 Stub 基线的主因：评测集用例按 `tests/eval/sample_data`（IT 手册/FAQ/差旅等）编写，而当前线上知识库只有 5 份业务文档，大量用例天然无命中——**评测集是资产回归，不代表线上质量**；上线评估应以"线上真实知识库 + 真实问答记录"另建用例集。

### 14.4 已知遗留与触发条件（不做不阻塞，触发再做）

| 遗留 | 状态 | 触发条件 |
|---|---|---|
| 团队模式启用 | 代码就绪且**当前部署已启用**（V5.1） | RBAC 深度 C 扩展（文档级/角色矩阵）：12.3 续 |
| RBAC 拦截 | 已接线（V5.1：Authorizer + kb owner/scope） | 文档级权限/多角色矩阵（12.3 深度 C 续） |
| 元数据富化（section/date） | ✅ 已交付（2026-08-16，T5）：md/txt/docx/pdf 章节标题提取→Chunk.metadata.section；pdf/docx date；支持 `filters={"metadata":{"section":...}}` | 已完结 |
| 13.10 P3 包（OCR/表格/混合分块/多 embedding/多 LLM） | 规格已立 | 用户决策 + 13.1 度量协议验证 |
| 契约同步自动化（openapi→TS） | 手写中 | 接口变更频繁或前端规模扩大时 |
| 事件钩子（文档变更→缓存失效） | ✅ 已覆盖（2026-08-16）：`mutation_epoch` 在 add_chunks/delete_document 递增，BM25 缓存（hybrid_rrf）与父块缓存（orchestration）均按 epoch 失效；通用事件总线留待增量索引需求 | 增量索引/多端同步需求出现时 |
| CI 基线门禁 | 人工对比 | 接 CI 时纳入 |
| 会话持久化/流式输出 | 未做（D 组评估结论） | 产品明确要求时单独立项 |
| 发布打包 | 前端壳可打包（`electron-builder --mac dmg`）；**Python 运行时捆绑未做** | 正式发布前做捆绑决策 |

### 14.5 对本文档的维护约定

- 任何实现变更后，更新第 4.2（schema）、3.10（provider 状态）、13.11（安全/规范状态）与 14.3（指标）对应小节；
- 每完成一个里程碑执行 `make eval` 存档并与上一份基线对比（13.1 硬要求）；
- 新增适配器/接口时必须先改 `core/interfaces.py` + `core/domain.py`（附录 A 第 1 条）。

---

## 第 15 章 已知边界与演进路线（2026-08-16 立档）

### 15.1 当前定位（如实声明）

本项目当前为 **Iterative / Self-Correcting RAG**（检索侧自我修正循环），而非完整 Agentic：

- 编排循环（`app/services/orchestration.py` `run()`）：多路检索 → 精排 → 评估 →（通过则生成 / 未通过则改写或换路再检索）。
- **无**规划器、**无**工具调用注册表、**无**"自主决定何时检索/调外部 API"。
- "Agentic"命名偏包装，对外口径须按本节省描述澄清。

### 15.2 输入格式边界

| 格式 | 状态 |
|---|---|
| PDF / Word(.docx) / Excel(.xlsx) / PPT(.pptx) / Markdown / 文本 | ✅ 已支持（`parser/` 注册表分发，5.1） |
| HTML / 邮件(.eml/.msg) | ✅ 已支持（标准库实现，零新增依赖） |
| 图片 OCR（扫描件 PDF / png/jpg） | ⏳ 预留接口 `_parse_ocr`（默认抛错），需接引擎（paddleocr/pytesseract）+ 配置开关（13.10 P3） |

### 15.3 安全现状三层

| 层 | 现状 |
|---|---|
| 解析安全 | ✅ 魔数校验 + zip 炸弹防御（`parser/__init__.py`，S2） |
| 提示注入 | ✅ 生成阶段 SYSTEM_PROMPT 约束（S4）+ **入库侧扫描 `adapters/injection_scanner.py`（P-C）**：默认 audit（命中记日志 + metadata 标记），可配 reject（40002 拒收）；误杀白名单可配 |
| 内容审核 | ✅ `adapters/content_moderation/`（P-C）：内置可编辑词表 `configs/moderation_keywords.csv`（缺失/损坏兜底内置最小词表），`moderation.enabled=false` 默认关；reject（40003 拒收）/ flag（仅标记 metadata） |

### 15.4 演进路线（分期）

| 期 | 内容 | 状态 |
|---|---|---|
| P-A | 数据清洗层 `adapters/cleaner/`（归一/去噪/段落 simhash 去重/质量过滤），`cleaner.enabled=false` 默认关 | ✅ 已交付（15.5） |
| P-B | 解析增强：PDF 表格保真（find_tables → Markdown）、PARSERS 注册表、HTML/EML 格式、OCR 预留接口 | ✅ 已交付（15.5） |
| P-C | 入库安全：提示注入扫描（默认审计标记、可选拦截）+ 内容审核层（内置可编辑词表，默认关） | ✅ 已交付（开发指令 01） |
| P-D | 真 Agentic：**结构化 Agent**（工具注册表 + 规划器，复用现有组件为工具），单独立项，需 `make eval` 基线验证不回退 | 📋 规划 |

### 15.5 决策记录

- **清洗/处理层架构**：设 `adapters/cleaner/` 适配器层（与 retriever/reranker/detector 同级），编排仍留在 `ingestion.py` pipeline，不引入独立"数据处理层"（避免过度设计）。
- **OCR 引擎**：本轮仅预留接口不接引擎（13.10 P3 规格：默认关、懒加载）；引擎决策另行评估。
- **Agentic 方向**：结构化 Agent（非 ReAct 文本循环）。
- **内容审核规则**：内置可编辑中文词表 csv + 配置开关（默认关）。
- 清洗/新格式均默认关闭或向后兼容：`cleaner.enabled=false` 时 `ingestion.py` 行为与旧版完全一致。

---

## 附录 A：Agent 开发执行要点（速查）

1. 先在 `core/interfaces.py` + `core/domain.py` 完成契约与模型，再写具体实现。
2. 任何模块都从 `core/config.py` 的 `SystemConfig` 取参数，无散落全局/硬编码。
3. **RRF 排序按分数降序，`key=lambda item: item[1]`**；别参考正则旧版本。
4. **BGE 中文查词：只对 `embed_query` 加 `query_instruction` 前缀**（`embed_documents` 不做），否则检索效果差。
5. 上传走异步 Job，`/api/query` 全同步链路并带全局超时。
6. 密钥只走环境变量（`RAG_DEEPSEEK_API_KEY`），`.env.example` 不许含真值。
7. 每一步记 `trace_id` JSON 日志 —— 也是 UI 反思面板的数据来源。
8. `filters`/`collection` 必须贯穿 Retriever→VectorStore 与整条反思循环（BM25/向量双路过滤），默认 `collection=kb`。
9. 新增业务格式/字段检索时，先看第 12 章判断深度：深度 A/B 走适配器与配置，深度 C 须先评审。
10. 全链路结束后跑 `make test` + `rage_eval`，并更新 README。

(全文完)
