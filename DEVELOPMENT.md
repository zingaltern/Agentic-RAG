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
│   ├── eval/                 # qa_pairs/{default,docs,complex}/*.jsonl（107+90 条）
│   ├── review/               # 外部基准测试集（CMRC 2018，make review）
│   └── eval_suite/           # 独立冻结基准集（120 条，第二道门禁）
├── data/                     # 运行期数据：chroma/ kb.json documents.json
├── reports/                  # eval/review/perf 报告存档（13.1 度量协议；gitignore）
├── docs/                     # 文档体系（唯一入口 docs/README.md）
│   ├── README.md             # 文档索引与归档约定
│   ├── review-followup.md    # 专业评审跟进清单
│   ├── RAG系统介绍.html       # 功能与特殊场景处理一览（介绍页）
│   ├── specs/                # 方案/规格类输入文档（在线侧输入处理 / 数据清洗与格式）
│   └── dev-tasks/            # 批次开发指令（02~08，规格先行）
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
| 30005 | 文档解析质量不达标（疑似扫描件/损坏文件；P0-1 质量门禁 `ingestion.quality_gate.mode=reject` 命中时） |
| 30006 | 索引配置与向量库不一致（P1-3 `index_dna.mode=reject` 命中；请执行 `python -m app.cli.rebuild --kb <id>` 重建） |
| 30007 | 未提取到有效文本（V5.2：解析/清洗后 0 块且 `ingestion.empty_result=reject`；空文件/纯图片扫描件/全部段落被过滤） |
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

    def count_tokens(self, text: str) -> int | None:
        """【可选能力，V5.2】精确 token 计数（分块目标测量用）。

        默认返回 None（不支持）→ 分块回退 token_char_ratio 估算；
        本地 BGE 实现覆写为真实分词器计数。新增引擎实现可选择性覆写，不破坏契约。
        """
        return None
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
  token_char_ratio: 1.8      # 仅 ratio/回退口径使用（中文约 1.0 字符/token，见 5.2）
  measure: auto              # auto=优先引擎 count_tokens（精确）；ratio=强制估算（旧口径）

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
  hopeless_shortcut: true                # 13.13 T1：首轮评估分远低于阈值 → 跳过改写轮直接 21001
  hopeless_score_threshold: 0.15         # 13.13 T1：无望判定阈值（通过线 0.45）

ingestion:
  max_file_size_mb: 50
  allowed_extensions: [pdf, docx, xlsx, pptx, doc, xls, ppt, md, txt, html, htm, eml, msg,
                       json, jsonl, csv, tsv,
                       py, js, mjs, cjs, ts, tsx, jsx, java, go, rs, c, h, cpp, hpp, cs,
                       rb, php, kt, swift, sh, sql, vue, css, scss, yaml, yml, toml, ini, xml]
  embed_batch_size: 64
  max_queue_size: 64             # V5.1：入库队列上限，满时返回 43003
  zip_max_total_mb: 200          # V5.1：zip 系格式解压总大小上限（防炸弹 S2）
  zip_max_entries: 1000          # V5.1：zip 条目数上限（防炸弹 S2）
  max_pages: 2000                # V5.2：单文档解析页数上限（PDF 超限截断 + 打标，防超大 PDF 拖垮解析）
  empty_result: reject           # V5.2：解析/清洗后无有效文本（0 块）→ reject=拒收（30007）| allow=旧行为
  tabular_max_rows: 200000       # V5.2：表格类（xlsx/xls/csv/tsv）行数上限（超限截断 + 打标）
  # desensitize_enabled: false   # S5：脱敏开关（config.py 默认 false，settings.yaml 未显式配置）
  json_flatten:                  # 16.1：JSON/JSONL 路径扁平化上限（防超深/超大结构爆炸）
    max_items: 5000              # 单文档最多产出多少行（超出截断 + 尾部标记）
    max_depth: 12                # 最大递归深度（超出以 <max-depth> 占位）
  office_convert:                # 16.2：旧版 .doc/.ppt 经 LibreOffice 转换后复用 docx/pptx 解析
    enabled: true
    soffice_path: ""             # 空 = 自动探测 soffice/libreoffice（PATH）
    timeout_seconds: 60          # 单次转换超时；超时/失败 → ParseError 30003
  code_symbols:                  # 16.5：代码 AST 符号提取（tree-sitter；缺 grammar 自动回退纯文本）
    enabled: true                # false = 代码退化为纯文本解码
    index_in_text: true          # 正文前置 [CODE_INDEX] 符号索引块
    max_symbols: 500             # 单文件符号数上限

cleaner:                         # 数据清洗层（解析后、分块前；默认关不改变现有行为）
  enabled: false
  normalize: true                # 宽度归一（全角字母数字/表意空格/控制字符）
  normalize_punct: false         # V5.2：中文标点→半角 ASCII（。→. 等；默认关）
  noise: true                    # 页码/页眉页脚/模板串去噪
  dedup: true                    # 段落级 simhash 近重复去重
  quality: true                  # 质量过滤（过短/无实词）
  dedup_threshold: 0.9
  min_chunk_chars: 20
  paragraph_mode: auto           # V5.2：auto=空行优先/无空行按行 | blank | line
  max_drop_ratio: 0.5            # V5.2：质量过滤删除占比超限则整体跳过
  dedup_max_paragraphs: 5000     # V5.2：段落数超限跳过 dedup（防 O(n²)）
  # exempt_kinds：代码/结构化类（py/json/csv/…）豁免 quality/dedup（默认表见 config.py）

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
- 格式：`.pdf/.docx/.xlsx/.pptx/.md/.txt` + `.html/.htm/.eml`（P-B）+ 代码/JSON/CSV/旧版 Office（第 16 章）+ `.msg`（V5.2：仅作明确报错提示，见下）；全量白名单见 `ingestion.allowed_extensions`（大小 <= `max_file_size_mb`）。
- 统一产出 `Document`：`text` 归一为 UTF-8；元数据写入 `metadata`：`source`（原文件名）、`page`（PDF/Word 页码）、`kind`（扩展名）。
- PDF → 用 `pymupdf`（fitz）逐页提取；Word → `python-docx` 段落级；Excel → 每 sheet 每行判为一段文本，`metadata` 记录 `sheet` 与行号；PPT → `python-pptx`。
- 失败（损坏/不支持）→  抛 `ParseError（code 30003），Job 标记 failed。
- **表格一等公民（P1-4）**：PDF（`find_tables`）与 Word（按 body 文档流**保序**，修复旧实现表格追加在段落末尾的问题）解析时，表格以 `[TABLE]\n{markdown}\n[/TABLE]` 原子块进入正文，并登记 `metadata["tables"] = [{index, header, rows, page}]`（顺序对应正文第 N 个 [TABLE] 块）；xlsx 整文档即表，v1 不动。清洗后执行 `reindex_tables(doc)` 重对齐（cleaner 删表则丢弃对应 JSON + warning）。cleaner 对 [TABLE] 块内容豁免（normalize 白名单 + dedup/quality 跳过含表格段落）。
- **解析器降级链（P3-8，`ingestion.parser_routing`，默认空=现状）**：`ParserSpec(name, extensions, fn, priority)` 注册表（`PARSER_REGISTRY`，旧 `PARSERS` dict 由注册表展开保留兼容）；路由 `{"pdf": {"chain": ["pymupdf", "ocr_dummy"]}}`（key 为不带点的扩展名，链按序尝试）。链内逐实现尝试：抛错或空文本（复用 P0-1 `evaluate_parse_quality` 的 `min_text_chars`）→ warning（含 parser 名）降级下一实现；链尽失败按 P0-1 mode 处理（reject → 30005，否则 30003）。成功后 `metadata["parser"]` / `metadata["parser_version"]` 溯源；**空路由配置走旧单实现路径，零差异**。新格式接入 SOP（四动作，其余阶段零改动）：①实现函数（签名 `fn(raw: bytes, config: IngestionConfig | None = None) -> tuple[str, dict]`） → ②注册 ParserSpec → ③`ingestion.allowed_extensions` 加扩展名 → ④`parser_routing` 加链一行 → 补单测。模块划分见 16.0。`ocr_dummy` 占位 spec 已注册（保持抛错，不入默认链，用于验证挂载机制）。
- **解析探针（P0-1）**：`_parse_pdf` 逐页统计写入 `metadata["text_stats"] = {total_chars, page_count, pages_with_text}`（`pages_with_text`=有实质文本的页数）；`garbled_ratio`（U+FFFD 字符数 / 文本总字符数，无字符时为 0.0）在 `_build_document` 对全部解析器统一计算——txt/html/eml 的 `errors="replace"` 解码同样产生 U+FFFD——并入 `metadata["text_stats"]`（非 PDF 解析器按单页文档补齐该结构）。`text_stats` 仅作解析事实探针，不入 Chroma chunk metadata（`chroma_store._clean_metadata` 剔除 Chroma 无法存储的 dict 等非标量值）。
- **解析健壮性（V5.2，复杂/危险文档兜底）**：
  - **加密 PDF**：pymupdf 检出 `needs_pass/is_encrypted` → 明确 `ParseError`（"PDF 已加密，请解除口令后上传"），不误报"文件损坏"；
  - **文本回退**：dict 抽取为空但 `get_text("text")` 有内容（异常排版 PDF）→ 回退纯文本抽取，不丢页；
  - **页数上限**：`ingestion.max_pages`（默认 2000）截断 + `text_stats["truncated_pages"]=true` 打标 + warning；
  - **文本解码链**：`.md/.txt` 与代码/JSON 统一走 `_decode_text`（utf-8-sig → gb18030 → replace），GBK/GB18030 老文档不再整篇乱码；
  - **HTML 去噪标签**：解析跳过 `script/style/noscript/template/nav/footer/header/aside/form/button/svg/iframe`；
  - **`.msg` 明确不支持**：OLE2 检出后抛 `ParseError` 并提示"请导出为 `.eml`"（不静默、不误报二进制伪造）；`.eml` 正常支持（15.2 口径同步）；
  - **OOXML 实体膨胀防御**：`docx/xlsx/pptx` 预检各 XML 部件含 `<!ENTITY` → 直接拒绝（防 billion laughs/XXE）；
  - **表格类行数上限**：`ingestion.tabular_max_rows`（默认 200000）对 xlsx/xls/csv/tsv 统一生效，超限截断 + 尾部标记 + `metadata["truncated"]=true`。
- **质量检查纯函数（P0-1，P3-8 降级链复用）**：parser 模块级 `evaluate_parse_quality(text, text_stats, *, min_text_chars, max_garbled_ratio, min_pages_with_text_ratio) -> list[str]`，返回 flag 列表（空=通过）：`empty_text`（去首尾空白后文本少于 `min_text_chars`，含扫描件抽出空文本）/ `high_garbled`（`garbled_ratio` 超过 `max_garbled_ratio`）/ `few_pages_with_text`（仅 `page_count>1` 的多页 PDF 生效：`pages_with_text/page_count` 低于 `min_pages_with_text_ratio`）。

### 5.2 智能分块 `split_chunks`（父子块）
- 参数：`chunker.parent_target_tokens=512`、`child_target_tokens=128`、`measure=auto|ratio`、`token_char_ratio=1.8`（仅 ratio 口径与回退使用）。
- **token 计量（V5.2 关键修复）**：`measure=auto` 时优先使用引擎可选能力 `count_tokens`（BGE 真实分词器；中文实测 ≈1.0 字符/token）——旧 ratio=1.8 口径把 512 token 当 922 字符，父块被模型 512 上限静默截断（实测约 39% 文本）；引擎不支持/异常/返回 None 时自动回退 ratio 估算，`measure=ratio` 可强制旧口径（行为可回退）。
- 规则：
  1. 以自然段落/行聚合为父块，块内以 `\n` 连接（保留行结构；V5.2 修复 `"".join` 行粘连）；段落超目标按 `。！？；` 与英文 `.?!`（空白后断句，防小数/版本号误切）继续切句；禁止在词中断。
  2. 每产出**父块**（按实际计量口径 ≤ parent_target_tokens，硬保证）；父块再按 `child_target_tokens` 切**子块**（≤ child 目标）。
  3. 父块 `parent_id=None`；子块 `parent_id=父块chunk_id`。
  4. 父块本身 ≤ 子块目标时不再产出重复子块。
  5. `chunk_id = sha256(document_id + ":" + str(order))[:16]`。
- 计量口径变化会改变块形态 → 索引 DNA 含 `pipeline_version`（V5.2=2）；存量库需 `python -m app.cli.rebuild --kb <id>` 重建对齐（13.5）。
- 检索命中子块时，编排层回溯其父块全文进入 LLM 上下文。
- **标题硬边界 + 上下文前缀 + 页码溯源（P1-5，`chunker.hard_boundary`/`chunker.context_prefix`，默认关）**：① 硬边界——父块聚合遇 section 归属变更强制断开（标题行为新块首行），同 section 连续内容不误断；② 页码——PDF 解析记录 `metadata["page_spans"]=[[start_line, end_line, page], ...]`（pptx 以 slide 号为页码），chunk 按段落起始行查表写 `metadata["page"]`（查不到不写）；③ 前缀——`chunk.text = template.format(doc_title, section) [+ " (p页码)"] + "\n" + 原文`（模板默认 `{doc_title} > {section}`，无 section 降级为仅 doc_title；前缀不计入切块 token 目标、计入 embedding 输入，超 64 token 截断 + warning；父子块一致加前缀）。索引 DNA 指纹纳入三组配置（变更即需重建）。
- **表格原子块（P1-4，`chunker.table_aware`，默认关）**：`[TABLE]...[/TABLE]` 预合并为原子单元，不参与句子聚合、不与正文粘块——整表 ≤ 父块目标 → 独立父块（`metadata["chunk_type"]="table"`、`metadata["table_json"]`（对应 tables[index] 的 JSON，超 `json_max_bytes`(4096B) 截断+warning），不产子块；超限 → 按「语义行组」切（每组 ≤ `max_rows_per_chunk`(30) 行且强制携带完整表头，`metadata["table_part"]="2/3"`）。开启后 chunk metadata 剔除文档级探针键（`text_stats`/`tables`/`sections`）。

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
- **S5 脱敏（V5.0，P2-6 两级化重构）**：PII 处理两级设计——检测打标 + 按策略脱敏。`app/services/desensitize.detect_pii(text) -> [{type: id_card|phone|bank_card, count, sample_hash}]`（**不含原文**，sample_hash 为首个命中 sha256 前 8 位）；`desensitize_text(text, mode="mask", types=None)`：mask=`***`（现行为）/ replace=`[身份证]` 等占位符 / delete=移除。配置两级：全局 `ingestion.pii = {enabled: false, mode: mask, types: [...]}`；库级覆盖 `KbInfo.pii`（kb.json 兼容，创建/更新 API 接受），**生效优先级 kb.pii > ingestion.pii > 不处理**（kb.pii {enabled:false} 显式关闭全局）。命中文档 `metadata["pii_types"]` 记录类型集合（仅打标）。执行点与顺序不变（parse 之后、清洗之前）。**兼容**：旧 `desensitize_enabled` 与新 `pii.enabled` 同开时新逻辑优先 + warning，旧开关标记 deprecated（两个周期后移除）。
- **幂等并发（V5.0）**：`_run` 内以 content_hash 为粒度加进程锁（`_lock_for`），幂等检查与写入原子化，杜绝 TOCTOU 双份入库。
- **解析质量门禁（P0-1）**：`ingestion.quality_gate = {enabled: false, mode: reject|flag, min_text_chars: 30, max_garbled_ratio: 0.05, min_pages_with_text_ratio: 0.2}`（默认关闭=行为与现状完全一致）。启用时在 parse 之后、脱敏之前执行 5.1 的 `evaluate_parse_quality`：命中任一 flag 时 `mode=reject` → 抛 `QualityGateError`（30005，Job failed）；`mode=flag` → `doc.metadata["quality_flags"]` 打标 + warning 日志（含 flags 与 trace_id）继续入库；`mode` 非法值启动即抛 `SystemConfigError`（config field_validator）。
- **增量/全量更新（V5.2）**：
  - **文档级增量更新**：`mode=upsert` 按 `(kb_id, filename)` 定位旧版本 → 新版本完整入库（parse→chunk→embed→写新 document_id chunks）→ `DocumentsStore.replace` 原子替换索引（版本 +1）→ 删除旧版本向量与归档（先写后删，查询不中断）。旧版本清理失败仅 warning，由 `doctor` 巡检兜底。
  - **强制重建**：`force=true` 跳过 content_hash 幂等短路（同内容也重建），用于修复 0 块文档/解析器升级后的重解析。
  - **全量重建**：`python -m app.cli.rebuild --kb <id>`（P1-3，读归档原件按当前配置重建整个集合，含失败回滚）。
  - **一致性巡检**：`python -m app.cli.doctor [--kb <id>] [--fix]`——核对 `documents.json` ↔ Chroma ↔ 归档：幽灵文档（索引有/向量无）、孤儿向量（向量有/索引无）、幂等映射错位、归档缺失；默认只读，`--fix` 安全修复前三类。
  - **索引字段**：`DocEntry` 增 `version`/`updated_at`/`content_hash`（旧 `documents.json` 缺省兼容）。
- **空结果策略（V5.2）**：分块后 0 块（空文件/纯扫描件无文本层/清洗过滤殆尽）时，`ingestion.empty_result=reject`（默认）→ 抛 `EmptyDocumentError`（30007），Job failed 且日志含原因（parse 空文本 / cleaner 过滤），杜绝"成功入库 0 块"的静默失败；`allow` 维持旧行为（success + chunk_count=0 + warning）。
- **数据清洗层（V5.2 增强，默认 `cleaner.enabled=false` 时全部无副作用）**：① 分段 `paragraph_mode=auto`——空行优先；整篇无空行（PDF/纯行文本）且 ≥3 行时按行细分，修复"整篇=一段"导致 dedup/quality 失效；`[TABLE]` 原子块始终独立成段；② 归一拆两档——宽度归一（全角字母数字/表意空格，默认开）与中文标点→半角（`normalize_punct`，默认关，避免改写中文排版）；③ 按类型豁免 `clean(kind=...)`——代码/JSON/CSV/表格类（`cleaner.exempt_kinds`）跳过 quality/dedup，防误杀；④ 安全阀——`max_drop_ratio`（质量过滤删除占比超限整体跳过 + warning）、`dedup_max_paragraphs`（超限跳过去重，防 O(n²)）。清洗产物变化由索引 DNA 检测。
- **索引 DNA（P1-3）**：`compute_index_dna(config)`（`app/core/index_dna.py` 纯函数）对 embedding.model_name/dimension/query_instruction + chunker 参数（含 `measure`）+ cleaner 配置 + `pipeline_version`（V5.2=2，解析/分块行为版本）做 sha256 规范化指纹（[:12]）；入库成功时写入 `chunk.metadata["index_dna"]` 与 `DocEntry.index_dna`，新 Chroma collection 建集合时 metadata 携带（存量无 dna 的 collection 视为 legacy）。任一相关配置变更即 dna 变化。
- **原件归档（P0-2）**：`ingestion.archive = {enabled: false, dir: "./data/archive"}`（默认关闭）。启用时入库成功路径（幂等锁内、duplicate 判定之后）将**原始字节**原子写至 `{dir}/{kb_id}/{document_id}{ext}`（ext 只取 `Path(filename).suffix`；`core/persist.atomic_write_bytes` tmp+rename 原子写）→ `DocEntry.archive_path` 随 register 登记；写失败仅 warning 不回滚入库。**级联清理**：删除文档/删库时同步删除归档原件（被遗忘权）。归档是"换 embedding 全量重建（rebuild CLI）"、解析器升级重解析与审计取证的前置。

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
- `multipart/form-data` 字段：`file`（必填）、`kb_id`（缺省 `default`）、
  `mode`（V5.2：`add`（默认，现行为）| `upsert`（按 (kb_id, filename) 视为同一逻辑文档，新版本替换旧版本））、
  `force`（V5.2：`true` 时跳过 content_hash 幂等短路，强制重建入库——修复场景）。
- 返回：

```json
{ "code": 0, "message": "ok", "data": {
   "job_id": "ab12cd34", "status": "pending", "kb_id": "default",
   "replaced_document_id": "doc_old1234" } }
```
- `replaced_document_id` 仅在本次将被替换（upsert 命中同名旧版本 / force 命中同内容旧版本）时返回；
  重复上传且未 `force` 时仍返回 `duplicate=true`（不创建 Job）。
- 更新语义（先写后删）：新版本解析/分块/向量全部成功后才替换索引并删除旧版本向量与归档，
  更新期间查询不中断；版本号 `version` 在 `GET /api/documents*` 可见；失败时旧版本保持可用。

### 6.3 GET /api/jobs/{job_id}

```json
{ "code": 0, "message": "ok", "data": {
   "job_id": "ab12cd34", "status": "success",
   "document_id": "doc_12ab34cd", "chunk_count": 120,
   "error_code": null, "replaced_document_id": null } }
```
- `replaced_document_id`（V5.2）：增量更新替换掉的旧版本 id（无替换为 null）。

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
| GET | `/api/documents/{id}` | 单文档元数据（P0-2：含 `archive_path` 归档路径） |
| DELETE | `/api/documents/{id}` | 删除文档（级联清向量块；**按文档所属库解析 collection**，V5.0 修 P0-1；P0-2：级联删除归档原件） |

> 文档索引持久化：`data/documents.json`（V5.0，含 content_hash 幂等索引；V5.2 增 `version`/`updated_at`/`content_hash`
> 字段，旧文件缺省兼容）；启动时索引为空且 Chroma 有数据 → 自动全量重建（消除重启后幽灵文档）。
> P0-2：删除文档 / 批量删除 / 删库级联时同步删除归档原件（`DocEntry.archive_path`，best-effort；归档开关默认关）。

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

### 8.3 评测（`app/cli/rageval.py` + `tests/eval/qa_pairs/<kb>/*.jsonl`）
- 用例：分库 jsonl（`{id, type, query, expected_sources, golden_answer, expected_behavior}`），契约与扩充方式见 `tests/eval/README.md`；default/docs 自建 107 条，覆盖"有答案/无答案/防编造"等场景。
- 指标：`ContextPrecision / ContextRecall / Faithfulness / AnswerRelevancy / Hit@5`。
- 输出 Markdown 报告（逐条 + 聚合）到 `reports/eval-{timestamp}.md`。
- no_answer 判据（13.6 修订）：`code=0 且回答含"未找到相关依据"类文案 ∨ code=21001`（`rageval.py` 单一常量）。

### 8.4 外部基准测试集（`make review`，两层评测策略，2026-08-30 新增）

> 评测体系分两层：**验证集（自建）与测试集（外部公开数据集）物理分离**——自建集做回归
> 门禁，公开数据集做能力测试，避免「自己出题自己答」的过拟合，并补齐真实世界文档分布盲区。

| 层 | 命令 | 数据 | 作用 |
|---|---|---|---|
| 验证集 | `make eval` | `tests/eval/qa_pairs/` 自建 197 条（default/docs 107 + complex 90；语料 `tests/eval/sample_data/`） | 开发回归门禁（13.1 度量协议：改动后不回退） |
| 测试集 | `make review` | `tests/review/` 外部公开数据集（首个：CMRC 2018，CC BY-SA 4.0，独立 kb `cmrc`） | 度量 RAG 真实效果（Hit@1/Hit@5、ContextPrecision/Recall、兜底率） |

- 数据来源、用例（测试对）结构与再生成方式见 `tests/review/README.md`；原始数据集下载到 `tests/review/raw/`（gitignore，不入库）。
- 转换器：`python -m app.cli.build_review_set`（GitHub 原始 JSON → 语料文档 + qa_pairs；固定 seed 可复现；纯标准库，不新增第三方依赖）。
- `rageval` 新增 `--fresh-kb`（进程内模式）：评测前清理该 kb 的 Chroma 集合与文档幂等表（`drop_collection` + `documents.delete_by_kb`，与 KB 删除端点 C7 级联一致），保证每轮全新入库、结果可复现。
- 报告输出 `reports/review-{ts}.md`；基线对比 `--baseline reports/review-<基线>.md`（判据与口径与 8.3/13.6 单一实现一致）。
- 判读纪律：`make review` 指标**只看趋势不看单点**——首轮运行结果存档为基线，后续改动对比 Δ；不作为 `make test`/`make eval` 之外的硬门禁（外部集无"全绿"预期，如实记录）。

### 8.5 验证体系 v2：复杂度分层验证集 + 性能基准（2026-08-30 新增）

> 针对「验证语料全是 <2KB 纯文本、题面过于直白」的复杂度盲区，将验证体系升级为
> 四道门禁，覆盖**体量 / 复杂度 / 难度 / 准确性 / 性能**五个维度。

| 门禁 | 命令 | 覆盖维度 | 判据纪律 |
|---|---|---|---|
| 单元回归 | `make test` | 代码逻辑 | 全绿 + 覆盖率 ≥80%（硬门禁） |
| 准确性验证集 | `make eval` | 准确性 + 复杂度 + 难度 | default/docs 库保持全绿；**complex 库按难度分层看逐层通过率**，首轮基线存档、后续不回退 |
| 外部测试集 | `make review` | 真实分布准确性 | 只看趋势 Δ（8.4） |
| 性能基准 | `make perf` | 体量 + 性能 | 首轮基线存档，后续对比 Δ；不设绝对门禁 |

#### 8.5.1 complex 难度分层验证集（kb `complex`）

- 生成器：`python -m app.cli.build_eval_stress`（固定 seed 可复现；纯用既有依赖
  pymupdf/python-docx/openpyxl/python-pptx/标准库，**不新增第三方依赖**），
  产物落 `tests/eval/sample_data/complex/` 与 `tests/eval/qa_pairs/complex/qa_pairs.jsonl`（随仓库提交）。
- 语料（43 份，虚构企业"云帆科技"域，事实可核验）：多页 **PDF**（含表格）、**DOCX**（含真实表格）、
  **XLSX**、**PPTX**、**HTML**、**EML 邮件线程**、**长文 Markdown**（≥3000 字埋点事实）、
  **噪声 TXT**（错别字/OCR 空格/广告行/样板重复）、近重复文档对与完全重复文档。
  噪声比例必须低于解析质量门阈值（garbled 检测不触发 30005/30003）。
- 测试对（~90 条），`type` 沿用 8.3 四类，`variant` 为难度分层标签，报告"特征分布"逐层出通过率：

| variant | 考察点 | type |
|---|---|---|
| `direct` | 单文档事实题（实体明确） | direct |
| `paraphrase` | 口语化/错字改写 | rewrite |
| `multi_doc` | 跨 2 文档综合（expected_sources=2） | direct |
| `table` | 仅能从表格（PDF/DOCX/XLSX）作答的数值题 | direct |
| `noisy` | 噪声文档中的事实 | direct |
| `long_doc` | 长文档埋点事实 | direct |
| `cross_fmt` | 同一事实族分布在不同格式文档 | direct |
| `oov` | 域外（期望 21001 兜底） | no_answer |
| `no_invent` | 域内相关但无据 | no_invent |

- 生成器必须**回环自检**：单测用项目自身解析器解析生成的 PDF/DOCX/XLSX/PPTX，
  断言事实文本可抽出且魔数合法（否则入库即 30003）。

#### 8.5.2 性能基准（`app/cli/perfbench.py` + `make perf`）

- 进程内模式（真实 BGE+Chroma，Stub LLM），独立 kb `perf`，每个规模点前清空集合与幂等表（同 8.4 `--fresh-kb` 级联）。
- 规模点：`--scales 50,150,300`（文档数），合成语料进程内生成不入仓（固定 seed）；每个规模点测：
  - **入库吞吐**：总耗时、docs/sec、chunk 总数、按 `Job.stage_timings` 拆解 embed/parse 占比；
  - **查询延迟**：固定 30 条查询各测 `orchestration.run` 耗时，出 avg/P50/P90/P95/max（ms）；
  - **准确率-规模曲线**：该 30 条的 Hit@5 随规模变化（体量增长是否伤检索）。
- 报告 `reports/perf-{ts}.md`（逐规模点表格）；`--baseline reports/perf-<基线>.md` 对比 Δ。
- 暖机（模型加载/索引构建）显式排除，与 rageval 同口径；`make perf` 不作硬门禁，首轮基线存档后看趋势。

### 8.6 质量评测基础设施（P0-P2 落地，2026-10-19）

> 设计稿：`docs/specs/测试与评测体系设计.md`。定位是**补充**而非替换 8.3/8.4：
> `make eval` 仍是检索/兜底回归门禁；质量评测先观测，人工校准通过后才升级为门禁。

- **工具**：`app/eval/`（纯函数指标 `metrics.py`、统一 schema `schema.py`、LLM judge `judge.py`）、
  `app/cli/eval_quality.py`、`make eval-quality`（默认 fresh/Stub；`JUDGE=1` 仅判分，`REAL=1` 才真实生成）；
  前端最小单测 `make frontend-test`（`node --test`，零新增依赖）。
- **数据**：`tests/quality/datasets/smoke_quality.jsonl`（30 条自动转换的 smoke，**非人工校准真值**）；
  统一 schema 字段见 `app/eval/schema.py`；旧 `golden/golden_answer/expected_doc` 自动兼容。
- **指标**：IR（Hit@k/Recall@k/Precision@k/MRR/nDCG）、兜底正确率、Bootstrap 95% CI；`app/eval/metrics.py`
  另提供 claim Faithfulness、引用 P/R/F1（函数已提供，尚未接入 CLI 报告）；judge 仅作试点，
  需按设计稿 6.4/6.5 做人工校准。
- **rageval P0 修复**：进程内 `elapsed_ms` 正确读取；入库文件过滤隐藏/不支持扩展（`.DS_Store`）；
  基线 diff 支持 `通过率` 计数比且只取首库表；新增 `--export-jsonl`（含完整 contexts/gold，仅进程内）；
  `orchestration.run(capture_contexts=True)` 返回不截断的评测上下文（默认关，API 行为不变）。
- **门禁**：`make test`/CI 保持硬门禁；`make eval-quality` 报告落 `reports/eval-quality-{ts}.md`，
  人工校准与 D3 holdout 完成前不作发布阻断。2026-10-19 已跑通 30 条 smoke（真实 LLM+judge，
  同族自评偏差、用例偏简单，仅作链路验证）。

---

## 第 9 章 开发计划与验收

**MVP（W1~W4）与 Phase2（W5~W8）均已交付并验收通过**（详见第 13 章交付状态）。
V1.0 发布标准已达成：pytest 全绿（`-m "not integration"`，收集口径以当前 `make test` 为准；历史基线 599 项）、`/ui` 与 SPA 闭环
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
- **阶段级指标（P2-7）**：`/api/metrics`（admin 权限，Prometheus exposition 文本）——`rag_ingest_stage_seconds{stage}`（histogram：parse/clean/chunk/embed/index）、`rag_ingest_total{result}`（success/failed/duplicate/rejected，按 error_code 分 label）、`rag_ingest_queue_depth`（gauge）、`rag_query_total{result}`。零新依赖（core/metrics.py 自实现）。
- **建议告警阈值（P2-7）**：入库失败率 >5%（5min 窗口）、队列深度持续 >80% 容量、/api/query 5xx 突增。无 Prometheus 部署时可人工 curl /api/metrics 观测。

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
- **索引 DNA 校验与重建（P1-3）**：查询侧（`/api/query` collection 解析后）比对 collection metadata dna 与当前配置——`index_dna = {enabled: false, mode: audit|reject}`；`audit`（默认）命中仅 warning + 响应标记 `index_dna_mismatch: true`；`reject` 抛 `IndexDnaMismatchError`（30006）；存量无 dna 视为 legacy 仅日志提示。重建：`python -m app.cli.rebuild --kb <id> [--yes]`——读 P0-2 归档原件 → 完整入库链写临时 collection（`rag_<id>__rebuild`）→ 校验文档数/dna → 切换 `KbInfo.collection` 指针 → 删旧集合与旧归档；**重建期间暂停上传与查询**（v1 单机语义，失败自动回滚指针并恢复索引快照）。**V5.2**：DNA 增 `pipeline_version`（解析/分块行为版本），升级后存量 collection 被 audit 标记，需 rebuild 对齐（建议随 V5.2 一次性重建）。

### 13.6 评测体系 v2 —— ✅ 已交付

- `tests/eval/` 已扩容至 **107 条**（`qa_pairs/{default,docs}/*.jsonl`），类型覆盖 direct/rewrite/no_answer/no_invent/口语/表格/多文档；同日并入 complex 难度分层 90 条（8.5.1，合计 197 条）。
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
- **样例/评测集资产化**：✅ qa_pairs 按库归档（197 条，含 complex 分层）；新格式/新策略上线前必须跑资产回归。
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

### 13.12 输入侧治理（`docs/specs/在线侧输入处理.md` 落地，P1+P2）—— ✅ 已交付（2026-08-18）

> 把 `docs/specs/在线侧输入处理.md` 的输入侧治理方案按「可配置、可降级、默认关不改变现有行为」原则落地。
> 每个新配置段都有独立 `enabled` 总开关：关闭时行为与旧版完全一致（客户端传 `history`、LLM 生成反问、无查询侧安全前置、无服务端会话）。

#### 13.12.1 配置总览（新增 SystemConfig 段，全部默认关）

| 配置段 | 对应设计文档 | 默认 | 作用 |
|---|---|---|---|
| `query_input` | L1 输入标准化 | `enabled=false` | 无效字符过滤 / ASR 顺滑 / 相对时间映射（纯规则、确定性） |
| `query_security` | 4.6 安全风控 | `enabled=false` | 查询侧提示注入 + 内容审核前置（复用入库侧组件） |
| `greeting` | L0 极简通道（4.1.2） | `enabled=false` | 问候语/闲聊规则直达（不检索、不调 LLM） |
| `clarification` | 4.4.5 反问机制 | `enabled=true` | A2 反问模板化（固定模板+槽位，LLM 仅可选增强） |
| `dialogue` | L2 对话状态引擎（4.3） | `enabled=false` | 服务端会话（session_id 生效）：生命周期 + 新话题边界判定 |

#### 13.12.2 `query_input` —— L1 输入标准化管线

- 步骤（每步独立 `enabled` 子开关，**每步独立 try/except：异常即跳过该步**，输出必为合法文本，最差等于输入）：
  1. `filter_invalid_chars`：Emoji/控制字符清理 + 连续重复标点压缩（`！！！`→`！`）；
  2. `asr_smooth`：语音转写语气词清理（`asr_filler_words` 词典可配，最长优先）+ 连续单字重复压缩（`对对对`→`对`）；
  3. `resolve_relative_time`：相对时间 → 绝对时间（`昨天`→`2026-08-17`；`time_reference_date` 缺省取当天，支持 大前天~大后天、前年~明年）。
- 挂接点：`OrchestrationService.run()` 内 `normalize_query`（全角/空白归一）之后执行；`deps.normalizer` 为 None（未启用）时零开销。
- **降级**：任一步失败 → 保留上一步文本；整体异常 → 原 query 透传。
- 实现：`app/adapters/normalizer/rule_normalizer.py`（`RuleQueryNormalizer`，注册 `normalizer.rule` 由 `wire` 直接装配）。

#### 13.12.3 `query_security` —— 查询侧安全前置

- 复用入库侧 `InjectionScanner`（`injection.patterns/whitelist` 追加模式，内置表始终生效）与 `KeywordModerator`（`keywords_csv` 缺失/损坏兜底内置词表），在 `/api/query` 入口（限速之后、库路由之前）执行：
  - `mode=audit`（默认）：命中仅记 `query_security_audit` 结构化日志，不拦截；
  - `mode=reject`：命中抛 `InjectionError(40002)` / `ContentModerationError(40003)`（对外契约包）。
- **降级**：检测组件内部异常 → `verdict=ok` 放行（安全检测故障不误伤可用性，仅记日志）。
- 实现：`app/adapters/query_guard.py`（`QueryGuard`）。

#### 13.12.4 `greeting` —— L0 极简通道

- `patterns`（正则列表，命中任一即直达）+ `reply`（固定回复）。
- 命中 → `Answer(code=0, text=reply, iterations=0)`，**不检索、不调 LLM**；`debug_trace` 记录 `{"fast_path": "greeting"}`。
- **降级**：单个正则非法 → 跳过该正则继续匹配；未命中 / 未启用 → 走正常链路。
- 挂接点：`OrchestrationService.run()` 起点（预热后、归一化前）。

#### 13.12.5 `clarification` —— A2 反问模板化（升级）

- `use_llm=false`（默认）：`_build_clarification` 直接按 query 特征选模板（`templates.no_entity`：长度<4 / `templates.missing_issue`：其余），**LLM 仅可选增强**（`use_llm=true` 时先 LLM 生成，失败/超时回退模板）；
- `enabled=false`：维持旧版「反思超限直接 21001 兜底」（不反问）；
- `max_rounds`（默认 2）：每会话连续反问上限 —— `dialogue` 启用后由服务端会话计数（`session.clarify_count`），达到上限后不再反问、直接 21001 兜底；
- `fallback_message`：模板兜底文案。
- 触发条件不变：反思达 `max_reflection_iterations` 且 `low_streak>=2` 且存在候选（`top` 非空）。

#### 13.12.6 `dialogue` —— L2 会话状态引擎（服务端会话）

- 总开关 `enabled=false`（默认）：`session_id` 仍不参与逻辑，沿用客户端传 `history` 的现状语义；`enabled=true` 后：
  - **生命周期**（`dialogue.session`）：`ttl_seconds`（默认 1800，空闲过期重置）、`max_rounds`（默认 5，保留最近 N 轮）、`enable_rollback` + `rollback_keywords`（命中"不对/说错了/重来"等 → 移除上一轮记录）；
  - **边界判定**（`dialogue.boundary`）：当前 query 与上轮 query 的嵌入余弦（`use_embedding` 可关）+ jieba 词集 Jaccard 双维度；**两维都低于阈值 → 新话题 → 清空短期上下文**（保留画像等长期信息由后续扩展负责）；
  - **存储**：内存 `SessionStore`（`app/services/session_store.py`），TTL 过期清除 + `max_sessions`（默认 100）LRU 淘汰；生产可换 Redis（接口 get/new/put 不变）。
- API 接线（`app/api/query.py`）：`session_id` 提供且 `dialogue.enabled=true` → 服务端历史优先（忽略客户端 `history`）；问答结束后回写历史 + 澄清计数。
- **降级矩阵**：边界判定 embedding 失败 → 仅实体维度；两维均失败 → 判定非新话题（保守保留历史）；会话存储异常 → 维持无会话语义（等同现状）。

#### 13.12.7 验收与回归

- 新增单测 `tests/unit/test_input_governance.py`（标准化/安全前置/问候直达/模板反问/边界判定/会话存储）+ `tests/unit/test_input_governance_api.py`（API 层接线：问候短路、reject 拦截、session 历史延续与回溯）；A2 旧单测按模板化语义更新。
- `make test` 全绿（含覆盖率门禁 ≥80%）；`make eval` 前后对比存档（13.1 度量协议）。

---

### 13.13 输入侧治理激活批次（V1，`docs/dev-tasks/archive/06-输入侧治理激活批次.md`）—— ✅ 已交付（2026-08-30）

> 依据《docs/specs/在线侧输入处理.md》可落地性评估（2026-08-30）：激活已建成能力并补齐两个轻件。
> 原则延续 13.12「可配置、可降级」；唯一默认开启项为 T1（见下，评估分过低的查询本就必然
> 以 21001 收尾，仅缩短空转，且以 `make eval` 全量回归为门禁）。

#### 13.13.1 T1 绝望短路（编排循环，对应方案 4.1「快收尾」）

- 新增 `orchestration.hopeless_shortcut`（默认 True）与 `orchestration.hopeless_score_threshold`（默认 0.15）。
- 行为：`iteration == 0` 且评估未通过且 `score <= threshold` 且无联网候选（`web_active=False`）
  且**无会话历史（history 为空）** → 跳过改写轮，直接返回 21001 兜底文案；**不触发 21003 澄清**
  ——域外查询的诚实答案是"未找到"而非反问，同时使 no_answer 类评测项回归 `code=21001` 判据
  （修复基线 D059/D061）。
- 历史豁免（冒烟结论 2026-08-30）：带会话历史的低分查询可能是针对历史的追问
  （如"我上一条消息说了什么"），其答案不在 KB 语义内，短路会截断正常追问 → 不短路。
- 动机：域外/闲聊查询首轮评估分已暴露无望信号，原实现仍空转约 2 轮检索 + LLM 改写。

#### 13.13.2 T2 L0 极简通道扩容并默认部署启用（方案 4.1.2）

- `GreetingConfig` 新增 `rules: [{name?, pattern, reply}]`（每条规则独立回复）；匹配顺序
  `rules` → 旧版 `patterns+reply`（兼容字段保留）；单个正则非法跳过；挂接点不变（`run()` 起点）。
- `configs/settings.yaml`：`greeting.enabled: true` + 7 条规则（问候/在场/致谢/身份认知/
  是否机器人/实时天气/实时日期时间），identity/realtime 回复明确声明能力边界；
  **代码默认仍 false**（部署级激活，与 `auth_enabled` 同范式）。
- 已知取舍：greeting 上移 API 层为纯重构，不在本批次（列后续项）。

#### 13.13.3 T3 服务端会话接通（激活 13.12.6 L2）

- 前端：`stores/app.js` 增 `sessionId`（`localStorage.rag_session_id` 持久化、惰性生成）+
  `ensureSessionId()`/`resetSession()`；登出重置会话；`QueryConsole` 提问携带 `session_id`
  （仍发送 `history` 作 dialogue 关闭时的兼容），「清空对话」同时重置会话。
- 部署配置：`configs/settings.yaml` `dialogue.enabled: true`（代码默认仍 false）。
- 生效语义（13.12.6 不变）：服务端历史优先；新话题清空短期上下文；回溯关键词移除上一轮；
  澄清按 session 计数（max_rounds=2），正常回答清零。有效上下文长度不变（均收敛 `history[-6:]`）。

#### 13.13.4 验收

- `make test` 全绿（429 项）；`make eval` 对比基线不回退且 no_answer 类改善（**107/107**，基线 105/107）；
  前端 `npm run build` 成功（dist 随仓库约定提交）；真实后端冒烟通过（详见 `docs/dev-tasks/archive/06-输入侧治理激活批次.md` §4.1）。
- **已知边界（冒烟结论，详见 dev-tasks/archive/06-输入侧治理激活批次.md §4.2）**：① 规则边界判定器对元问题（"我上一条说了什么"）
  存在盲区（query-vs-query 双维比较，规格内行为，根治需 L3 合并调用）；② SYSTEM_PROMPT 的
  "只基于知识库上下文+强制引用"契约会压制从会话历史复述用户自述事实（历史记忆问答产品化单独立项）。

---

## 第 14 章 当前状态与遗留（V5.1，2026-08-15 立档，2026-10-19 修订）

> 给后续开发/运维 Agent 的**现状快照**：当前可运行形态、常用命令、已知遗留与触发条件。

### 14.1 当前运行形态

- **后端**：`uvicorn app.main:app --host 127.0.0.1 --port 8000`（S2）；**当前部署 `auth_enabled=true`（团队模式，本机账号 admin/admin123、user1）**；契约默认 `false`（个人模式）；团队模式首管理员经 `RAG_ADMIN_USER/RAG_ADMIN_PASSWORD`（或 CLI 引导）创建。
- **模型**：embedding `bge-small-zh-v1.5`（本地）+ reranker `bge-reranker-base`（本地）+ LLM `deepseek-chat`（唯一外部调用，key 经 `RAG_DEEPSEEK_API_KEY`）。
- **数据**：`data/chroma`（向量，`rag_<kb>` 集合）+ `data/kb.json`（知识库映射）+ `data/documents.json`（文档索引，重启自动从 Chroma 兜底重建）。
- **前端**：`frontend/dist` 由后端托管（SPA，静态资源 no-store）；桌面端 `npm run desktop`（或打包 App，自动拉起后端）。
- **评测集**：验证集 `tests/eval/qa_pairs/` 197 条（default/docs 自建 107 + complex 难度分层 90；报告 `reports/eval-{ts}.md`）；
  外部测试集 `tests/review/`（CMRC 2018，独立 kb `cmrc`，报告 `reports/review-{ts}.md`，见 8.4）。

### 14.2 常用命令

| 命令 | 说明 |
|---|---|
| `make bootstrap` | venv + 依赖 |
| `make run` | 启动后端（8000） |
| `make test` | 全量单测（`-m "not integration"`，收集口径以当前 `make test` 为准；历史基线 599 项） |
| `make eval` | 进程内评测（Stub LLM）并存档报告 |
| `make review` | 外部基准测试集评测（CMRC 2018 → 独立 kb `cmrc`，`--fresh-kb` 全新入库；`BASELINE=reports/review-<基线>.md` 可对比）并存档报告 |
| `make perf` | 性能基准（规模点 50/150/300 文档：入库吞吐 + 查询延迟分位 + Hit@5 规模曲线；`BASELINE=reports/perf-<基线>.md` 可对比） |
| `make desktop` | 启动 Electron 桌面（前端需先 `npm run build`） |
| `cd frontend && npm run build` | 重建 SPA（静态托管实时生效） |
| `cd frontend && npm run test:desktop` | Electron 壳测试（11 用例） |
| `python -m app.cli.rageval --base-url http://127.0.0.1:8000 --all-kbs --baseline reports/<最新>` | 真实 DeepSeek 评测 + 基线对比 |
| `python -m app.cli.doctor [--kb <id>] [--fix]` | V5.2：索引三方一致性巡检（幽灵文档/孤儿向量/幂等映射/归档缺失） |

### 14.3 当前实测指标（2026-08-11 存档，历史基线）

- Stub 进程内全量（107 条）：**107/107 通过**，Hit@1=90%，Hit@5=100%，AnswerRelevancy=100%（Faithfulness 为 Stub 假 0%，无参考意义）。
- 真实 DeepSeek（`--kb default`，21 条，当前知识库 = 5 份业务文档）：**14/21 通过**，Faithfulness(direct)=**56%**，Hit@1=44%。
  - Hit@1 低于 Stub 基线的主因：评测集用例按 `tests/eval/sample_data`（IT 手册/FAQ/差旅等）编写，而当前线上知识库只有 5 份业务文档，大量用例天然无命中——**评测集是资产回归，不代表线上质量**；上线评估应以"线上真实知识库 + 真实问答记录"另建用例集。

### 14.4 已知遗留与触发条件（不做不阻塞，触发再做）

| 遗留 | 状态 | 触发条件 |
|---|---|---|
| 团队模式启用 | 代码就绪且**当前部署已启用**（V5.1） | RBAC 深度 C 扩展（文档级/角色矩阵）：12.3 续 |
| RBAC 拦截 | 已接线（V5.1：Authorizer + kb owner/scope） | 文档级权限/多角色矩阵（12.3 深度 C 续） |
| A 组智能体体验（记忆压缩/澄清/联网/多模型） | ✅ 已交付（2026-08-16）：A1 历史摘要压缩（memory 段）、A2 主动澄清（21003+need_clarify）、A3 同义词扩充、A5 Tavily 联网检索（web 段，key 走环境变量）、A6 多模型候选+熔断（llm.candidates）；**A4 多库自动路由已移除（2026-08-30，docs/dev-tasks/archive/05-移除A4多库自动路由.md）**：多库基础设施（collection 隔离+库级权限）保留，选库回归显式 kb_id / 缺省 default | 除 A4 外均可独立开关（enabled） |
| 输入侧治理（在线侧输入处理落地） | ✅ 已交付（2026-08-18，13.12）：query_input 标准化管线 / query_security 查询侧安全前置 / greeting 极简通道 / clarification 模板化反问 / dialogue 服务端会话（边界判定+生命周期） | 全部默认关（clarification 默认开）；前端传 session_id 支持为后续项（当前后端可接、前端未发） |
| 输入侧治理激活批次（T1 绝望短路 / T2 极简通道扩容 / T3 服务端会话接通） | ✅ 已交付（2026-08-30，13.13 + docs/dev-tasks/archive/06）：域外查询首轮短路 21001；greeting rules 扩容并部署启用；前端携带 session_id + dialogue 部署启用（L2 激活） | T1 代码默认开（eval 门禁通过）；T2/T3 代码默认关、settings.yaml 部署启用 |
| 元数据富化（section/date） | ✅ 已交付（2026-08-16，T5）：md/txt/docx/pdf 章节标题提取→Chunk.metadata.section；pdf/docx date；支持 `filters={"metadata":{"section":...}}` | 已完结 |
| 13.10 P3 包（OCR/表格/混合分块/多 embedding/多 LLM） | 规格已立 | 用户决策 + 13.1 度量协议验证 |
| 查询智能补强（08：Q1 多策略改写 / Q2 Scope 路由 lite） | Q1 实现完成（HyDE/Step-Back，默认关；随代码在途）；Q2 未开工 | `docs/dev-tasks/08` DoD + 13.1 度量协议 |
| 契约同步自动化（openapi→TS） | 手写中 | 接口变更频繁或前端规模扩大时 |
| 事件钩子（文档变更→缓存失效） | ✅ 已覆盖（2026-08-16）：`mutation_epoch` 在 add_chunks/delete_document 递增，BM25 缓存（hybrid_rrf）与父块缓存（orchestration）均按 epoch 失效；通用事件总线留待增量索引需求 | 增量索引/多端同步需求出现时 |
| CI 基线门禁 | ✅ 已交付（4.5，`.github/workflows/ci.yml`）：PR 跑 `make test`（覆盖率门禁）+ 前端 `npm run build`，评测对比留痕不阻断 | 已纳入；后续按需加回归基线对比 |
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
| HTML / 邮件(.eml) | ✅ 已支持（标准库实现，零新增依赖）；**`.msg`（Outlook OLE2）不解析**：白名单保留以返回明确错误（"请导出为 .eml"），V5.2 |
| 代码文件（py/js/ts/java/go/…，含 yaml/toml/ini/xml 配置类） | ✅ 已支持（16.1 + 16.5）：tree-sitter AST 符号提取（15 种语言 grammar），无 grammar 回退纯文本 |
| JSON / JSONL | ✅ 已支持（16.3，路径扁平化，纯标准库） |
| CSV / TSV | ✅ 已支持（16.4，标准库 csv，与 xlsx 同口径） |
| 旧版 Excel(.xls) | ✅ 已支持（16.2，新增依赖 `xlrd`） |
| 旧版 Word(.doc) / PPT(.ppt) | ⚠️ 条件支持（16.2）：本机存在 LibreOffice `soffice` 时自动转换解析，否则 `ParseError` 30003 并提示安装 |
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
| P-A | 数据清洗层 `adapters/cleaner/`（归一/去噪/段落 simhash 去重/质量过滤；V5.2 增分段策略/标点开关/类型豁免/安全阀），`cleaner.enabled=false` 默认关 | ✅ 已交付（15.5；V5.2 增强） |
| P-B | 解析增强：PDF 表格保真（find_tables → Markdown）、PARSERS 注册表、HTML/EML 格式、OCR 预留接口 | ✅ 已交付（15.5） |
| P-C | 入库安全：提示注入扫描（默认审计标记、可选拦截）+ 内容审核层（内置可编辑词表，默认关） | ✅ 已交付（规格见 `docs/specs/数据清洗与格式_开发要点.md` P-C 与 15.5 决策） |
| P-D | 真 Agentic：**结构化 Agent**（工具注册表 + 规划器，复用现有组件为工具），单独立项，需 `make eval` 基线验证不回退 | 📋 规划 |

### 15.5 决策记录

- **清洗/处理层架构**：设 `adapters/cleaner/` 适配器层（与 retriever/reranker/detector 同级），编排仍留在 `ingestion.py` pipeline，不引入独立"数据处理层"（避免过度设计）。
- **OCR 引擎**：本轮仅预留接口不接引擎（13.10 P3 规格：默认关、懒加载）；引擎决策另行评估。
- **Agentic 方向**：结构化 Agent（非 ReAct 文本循环）。
- **内容审核规则**：内置可编辑中文词表 csv + 配置开关（默认关）。
- **A4 多库自动路由移除（2026-08-30）**：多库（kb_id → collection 隔离 + 库级权限）保留为企业 RBAC 载体；自动路由层（kb_router/KbRouter/kb_id="auto"）整体移除——前端已显式选库，企业场景路由误选代价高，且休眠路径存在普通用户 50001 缺陷（`_route_kb` 无匹配时 `can_read_kb(user, None)` 崩溃）。规格与改动清单见 `docs/dev-tasks/archive/05-移除A4多库自动路由.md`。
- **两层评测策略（2026-08-30）**：验证集（自建 107 条，`make eval`）与测试集（外部公开数据集，`make review`）物理分离——自建集只做回归门禁，公开数据集（首个 CMRC 2018，CC BY-SA 4.0）度量真实检索/问答效果；`rageval` 增 `--fresh-kb`（清理集合+幂等表）保证每轮全新入库可复现。规格见 8.4，数据结构见 `tests/review/README.md`。
- **验证体系 v2（2026-08-30）**：四道门禁覆盖体量/复杂度/难度/准确性/性能（8.5）——complex 难度分层验证集（多格式语料 PDF/DOCX/XLSX/PPTX/HTML/EML/长文/噪声，9 层 variant，生成器回环自检）并入 `make eval`；性能基准 `make perf`（规模点吞吐/延迟分位/Hit@5 曲线）。生成器只用既有依赖（pymupdf 可生成含 CJK 的 PDF），零新增第三方依赖。
- 清洗/新格式均默认关闭或向后兼容：`cleaner.enabled=false` 时 `ingestion.py` 行为与旧版完全一致。

---

## 第 16 章 新增解析格式：代码 / JSON / 表格 / 旧版 Office（2026-09-08 立档，2026-09-09 拆分）

> 依据：用户需求（代码、旧版 Office、JSON、CSV）。性质：**向后兼容的增量格式**——只新增扩展名与解析实现，
> 不改动既有 10 种格式的任何行为；全部走 5.1 的「新格式接入 SOP」。
> **职责边界**：解析器只做「解析 + 统一化」（原始字节 → `Document`），**不做清洗/重构**
> （清洗归 `adapters/cleaner/`，分块归 `adapters/chunker/`）。
> 依赖策略：新增 `xlrd`（读 `.xls`）与 `tree-sitter` + 15 个语言 grammar（16.5）；
> `.doc/.ppt` 不引 Python 依赖，改用**可选** LibreOffice 外部转换。

### 16.0 模块划分（2026-09-09 拆分，行为零变化）
原 `parser/__init__.py` 单文件 938 行按职责拆分，公开 API 不变（`ingestion.py` 与测试零改动）：

| 模块 | 职责 |
|---|---|
| `__init__.py` | 门面：re-export 公开 API + 注册装配（新增格式在此加一行） |
| `formats.py` | 格式常量：`CODE_LANGUAGES`/`CODE_EXTENSIONS`/`TABULAR_EXTENSIONS`/`LEGACY_OFFICE_EXTENSIONS`/`SUPPORTED_EXTENSIONS`/OLE2 魔数 |
| `common.py` | 统一化层：`_build_document`（Document 产出）/ `_decode_text`（编码回退链） |
| `security.py` | 解析前预检：`_check_magic` / `_check_zip_bomb` |
| `registry.py` | `ParserSpec` / `PARSER_REGISTRY` / `PARSERS` / `_register_parser` / `_routing_chain` |
| `dispatcher.py` | `parse_file` 分发入口（单实现路径 + P3-8 降级链） |
| `quality.py` | `evaluate_parse_quality` / `reindex_tables` / 表格渲染工具 |
| `pdf.py` | PDF（含表格一等公民） |
| `office.py` | docx / xlsx / pptx + 旧版 xls / doc / ppt |
| `office_convert.py` | LibreOffice 外部转换（`.doc/.ppt`） |
| `tabular.py` | CSV / TSV |
| `text.py` | txt / md / html / eml |
| `json_doc.py` | JSON / JSONL（路径扁平化） |
| `code.py` | 代码（tree-sitter AST 符号提取 + 纯文本回退） |
| `ocr.py` | OCR 预留接口 |

### 16.1 代码文件（`code.py`）
- 扩展名 29 个（与 `ingestion.allowed_extensions` 同步）：
  脚本类 `py/js/mjs/cjs/ts/tsx/jsx/java/go/rs/c/h/cpp/hpp/cs/rb/php/kt/swift/sh/sql/vue/css/scss`；
  配置类 `yaml/yml/toml/ini/xml`。
- 解析：按字节解码（`utf-8-sig` → `gbk` → `utf-8 errors="replace"` 三级回退），**原文保真**（不去噪/不重排）。
- `metadata`：`language`（扩展名 → 语言名映射）、`kind`、`text_stats`（按 5.1 统一补齐）；
  接入 grammar 时另有 `symbols` / `symbol_count`（16.5）。
- 分块沿用 5.2 既有规则（按行/段落聚合 + 超长硬切），不为代码单独加分块器（避免过度设计）。

### 16.2 旧版 Office（`.xls` / `.doc` / `.ppt`）
- **`.xls` → `xlrd`**（新增依赖，理由见 `pyproject.toml` 注释）：逐 sheet 逐行取值，输出与 5.1 xlsx 同款
  「行 → 制表符连接」文本，`metadata["sheets"]` 记录表名。
- **`.doc` / `.ppt` → LibreOffice 外部转换**（可选，配置 `ingestion.office_convert`）：
  1. `shutil.which` 探测 `soffice`/`libreoffice`（可用 `office_convert.soffice_path` 指定绝对路径）；
  2. `soffice --headless --convert-to docx|pptx --outdir <临时目录> <临时源文件>`
     （`subprocess` 参数列表化、无 shell、带 `timeout_seconds` 超时）；
  3. 转换产物交给既有 `_parse_docx` / `_parse_pptx` 解析（复用，不重复实现）；
  4. `metadata["parser"] = "soffice_convert"`、`metadata["converted_from"] = 原扩展名` 溯源。
- 未安装 / 转换失败 / 超时 → `ParseError`（30003），文案含安装指引
  （macOS `brew install --cask libreoffice`；Debian/Ubuntu `apt-get install libreoffice`）。
- **安全**：转换在 `tempfile.mkdtemp()` 临时目录进行且用后清理；不联网；`office_convert.enabled=false` 时直接拒绝。

### 16.3 JSON / JSONL（`json_doc.py`）
- 呈现形式：**路径扁平化**，每行 `路径: 值`。
  - dict → `a.b`；list → `a[0]`；标量 → `path: str(value)`（`None` → `null`，`True/False` → `true/false`，数字原样）。
  - `.jsonl`：按行解析，每条记录路径前缀 `[N].`（N 从 1 起）。
- 上限（`ingestion.json_flatten`）：`max_items`（默认 5000 行，超出截断并追加 `…（已截断，共 N 行）`）、
  `max_depth`（默认 12，超出写 `<max-depth>` 占位）。
- 非法 JSON / 非对象行 → `ParseError`（30003）；空文件 → 空文本（沿用既有语义）。
- 不保留 JSON 原文（避免同一内容双份入库；且深层嵌套原文检索效果差）。

### 16.4 CSV / TSV（`tabular.py`）
- 与 xlsx/xls **同口径**：标准库 `csv` 逐行读取 → 去掉空单元格 → 制表符连接（空行忽略），
  `metadata["columns"]` 记录首行列名；`kind` 为 `csv`/`tsv`。
- 编码走 `_decode_text` 回退链（GBK 中文表头可正常解码）；带引号字段（含分隔符/换行）按 RFC 4180 解析。
- 上限 `_MAX_ROWS = 200_000`（超出截断 + 尾部标记）；空文件 → 空文本。
- 不做列名映射改写（那是清洗/重构范畴）；检索侧若需「列名: 值」形态，走 cleaner 或后续单独评估。

### 16.5 代码 AST 符号提取（tree-sitter）
- **依赖**：`tree-sitter` + 15 个官方 grammar（python/javascript/typescript/java/go/rust/c/cpp/c_sharp/
  ruby/php/bash/kotlin/swift/css）。**按需 import**：缺任一 grammar 只影响该语言，自动回退纯文本，不影响启动。
- **提取规则**：深度优先遍历 AST，按「语言 → 定义节点类型映射」收集符号：
  函数/方法/类/结构体/接口/枚举/协议/trait/impl/module/namespace 等；
  名称优先取 `name` 字段，无该字段时依次尝试 `declarator`/`selectors`/`type`，最后兜底首个标识符
  （C/C++ 声明符、Go `type_spec`、CSS 选择器）。类内函数统一记为 `method` 并加容器前缀（如 `A.m`）。
- **产出**：
  - `metadata["symbols"] = [{name, kind, line_start, line_end}, ...]`（按 AST 遍历顺序）；
  - `metadata["symbol_count"]`（标量，便于统计与健康检查）；
  - `ingestion.code_symbols.index_in_text=true` 时，正文前置
    `[CODE_INDEX language=<lang> symbols=N]` + 每行 `kind name (L起-L止)` + `[/CODE_INDEX]`
    （与 `[TABLE]` 同为机器可识别的原子块，提升符号名可检索性）；**源码原文紧随其后、原样保留**。
- **配置**（`ingestion.code_symbols`）：`enabled`（默认 true；false = 纯文本解码）、
  `index_in_text`（默认 true）、`max_symbols`（默认 500，超出截断）。
- **回退**：无 grammar 的语言（yaml/toml/ini/xml/vue/scss/sql）、grammar 未安装、AST 解析异常
  → 一律回退纯文本解码（不抛错、不阻塞入库），`logger.debug/warning` 留痕。

### 16.6 接入清单与验收
- 四动作（5.1 SOP）：①实现函数 → ②注册 `ParserSpec` → ③`allowed_extensions` 加扩展名 →
  ④需要降级链时在 `parser_routing` 加一行。本批**默认走单实现路径**（`parser_routing` 保持 `{}`，零差异承诺）。
- 魔数校验（5.1 / S2）新增分支：`.doc/.xls/.ppt` 校验 OLE2 头 `D0 CF 11 E0 A1 B1 1A E1`；
  代码/JSON/JSONL/CSV/TSV 走文本嗅探（样本含 NUL 即拒）。
- 单测（`tests/unit/test_parser.py`、`tests/unit/test_parser_routing.py`）：各新格式正常解析、编码回退、非法输入、
  魔数不符、soffice 缺失与成功两条路径（monkeypatch）、JSON 截断与深度上限、AST 符号提取与回退、扩展名白名单。
- 验收：`make test` 全绿（覆盖率 ≥80%）；`make eval` 与基线无回退（新增格式不改既有解析行为）。

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
