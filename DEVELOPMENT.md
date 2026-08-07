# Agentic RAG 智能知识库系统 — 开发设计文档（V4.0 开发版）

> 本文件是开发本系统的**唯一事实依据（Single Source of Truth）**，内容自包含（Self-Contained），无需参考任何其他文档。
> 任何开发本项目的智能体（Code Agent）与工程师必须严格遵守本文档的**接口契约、数据模型、算法、配置、验收标准**。
> 若实现与本文档冲突，以本文档为准；确需偏离时，必须在代码中显著标注原因并申请修订本文件。

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
| 前端测试页 | 原生 HTML+JS | 无依赖 | 由 FastAPI 静态托管于 `/ui` |
| 容器 | docker / docker-compose | 可选 | 集成测试/部署（MVP 可不装） |

依赖清单统一写入 `pyproject.toml`，禁止任何代码直接 `import` 清单外的第三方库。

### 1.2 仓库结构（Agent 须按此骨架落盘）

```
rag_kb/
├── app/
│   ├── main.py               # FastAPI 入口：装配 DI → 启动
│   ├── api/
│   │   ├── documents.py      # upload / list / get / delete
│   │   ├── query.py          # /api/query
│   │   ├── plugins.py        # switch / status
│   │   ├── jobs.py           # /api/jobs/{id}
│   │   └── health.py         # /api/health
│   ├── core/
│   │   ├── config.py         # SystemConfig（pydantic-settings）
│   │   ├── domain.py         # 全部数据模型（第 2 章）
│   │   ├── interfaces.py     # 全部抽象接口（第 3 章）
│   │   ├── registry.py       # PluginRegistry + wire() 装配
│   │   ├── errors.py         # 异常层级与错误码（6.7）
│   │   └── logging.py        # JSON 结构化日志 + trace_id
│   ├── services/
│   │   ├── ingestion.py      # IngestionPipeline（上传/入库）
│   │   ├── orchestration.py  # Agentic 循环编排（5.9）
│   │   ├── citation.py       # 引用溯源相似度算法（5.8）
│   │   └── jobs_store.py     # Job 存储（内存实现 + 锁）
│   └── adapters/
│       ├── embedding/        # local_bge_bell / bge_m3 / remote_api
│       ├── vector_store/     # chroma / milvus（Milvus 仅后续）
│       ├── retriever/        # hybrid_rrf / hybrid_rerank
│       ├── reranker/         # identity / bge
│       ├── evaluator/        # rule / model
│       ├── rewriter/         # rule / llm
│       ├── detector/         # citation / llm_crosscheck
│       ├── llm/              # deepseek / ollama / openai_compat
│       ├── parser/           # pdf/docx/xlsx/pptx/md/txt
│       └── chunker/          # parent_child
├── web/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── configs/
│   └── settings.yaml         # 见第 4 章
├── tests/
│   ├── unit/
│   ├── integration/
│   └── eval/
│       ├── raga_eval.py      # RAGEvaluator CLI
│       └── qa_pairs.json
├── migrations/
│   └── migrate_chroma_to_milvus.py   # 预留
├── pyproject.toml
├── docker-compose.yml        # 可选
├── .env.example               # 环境变量样例（不含真实密钥）
├── Makefile                  # make bootstrap / make run / make test
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
| 40101 | 鉴权失败（`api.auth_enabled=true` 时） |
| 40401 | 文档/Job 不存在 |
| 30001 | LLM（DeepSeek）调用失败/超时 |
| 30002 | 向量化失败 |
| 30003 | 解析失败（损坏文件/格式不支持） |
| 21001 | 兜底：反思达到上限仍未找到可靠答案 |
| 21002 | 全局请求超时 |
| 50001 | 未知内部错误 |

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

---

## 第 4 章 配置体系

### 4.1 加载与覆盖优先级

`内置默认值 < configs/settings.yaml < 环境变量（RAG_ 前缀）`

环境变量命名：例如 `RAG_LLM_PROVIDER=deepseek`、`RAG_EMBEDDING_MODEL_NAME=...`（`_` 分隔大写），优先级最高；允许 `.env` 文件（本地）。

### 4.2 settings.yaml 完整 Schema（MVP 默认）

```yaml
env: dev

api:
  host: 0.0.0.0
  port: 8000
  auth_enabled: false

embedding:
  provider: local_bge_v15          # local_bge_v15（MVP）| bge_m3 | remote_api
  model_name: BAAI/bge-small-zh-v1.5
  query_instruction: "为这个句子生成表示以用于检索相关文章："
  device: auto                   # auto|cpu|mps（MPS 可选，若不稳定回 cpu）
  batch_size: 64
  dimension: 0                   # 0 表示由模型自动识别（运行时回填实际维度）

vector_store:
  provider: chroma
  persist_dir: ./data/chroma
  collection: kb

retriever:
  provider: hybrid_rrf
  top_k: 20
  candidates: 50                # 融合后返回候选数（生产精排用）
  rr_fused_k: 60                # RRF 的 k 参数

reranker:
  provider: identity
  rerank_top_k: 5               # 最终给 LLM 的上文块数

evaluator:
  provider: rule
  rule_threshold: 0.45
  top_n: 5

rewriter:
  provider: rule
  synonyms_csv: ./configs/synonyms.csv

detector:
  provider: citation
  similarity_threshold: 0.8

llm:
  provider: deepseek
  model: deepseek-chat
  base_url: https://api.deepseek.com
  temperature: 0.1
  max_tokens: 1024
  timeout_seconds: 30

orchestration:
  max_reflection_iterations: 3
  search_timeout_seconds: 10
  request_timeout_seconds: 30

ingestion:
  max_file_size_mb: 50
  allowed_extensions: [pdf, docx, xlsx, pptx, md, txt]
  embed_batch_size: 64

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

### 5.5 规则评估器（RuleEvaluator）

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

### 5.6 规则改写器（RuleRewriter）按序执行
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

### 5.8 幻觉检测（CitationTracingDetector）

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

### 5.9 编排（OrchestrationService）——Agentic 循环

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

- MVP JobStore：进程内字典，重启后 Job 状态丢失（可接受，生产再上 Redis/MQ）。
- 说明：**解析/向量化全程不占用 HTTP worker 阻塞线程**；/query 时才同步走各模块。

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
- `/api/plugins/switch`：临时切换某模块实现（MVP 语义=更新内存 config + 重建该模块单例；服务进程内即时生效，不回退）。失败时回滚并返回 50001。
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
  "session_id": "uuid-123",
  "top_k": 5,
  "filters": {"metadata": {"department": "HR"}},
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
| GET | `/api/documents?page=1&size=20` | 分页（按创建时间倒序） |
| GET | `/api/documents/{id}` | 单文档元数据 |
| DELETE | `/api/documents/{id}` | 删除文档（级联清向量块） |

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

## 第 9 章 开发计划（4 周）与验收

| 周 | 交付 | DoD |
|---|---|---|
| W1 | 工程骨架、接口、Config、Parser + Chunker + UI 骨架 | `make test` 契约测试通过；`/ui` 返回 200 |
| W2 | VectorEngine + VectorStore + Retriever(RRF) + 上传 Job 流水线 | `make demo-ingest`+RAG 搜索级联通过；/ui 上传流程走通 |
| W3 | Orchestration（Agentic 循环）+ Detector + `/api/query` + DeepSeek | curl 问答返回引用与迭代数；/ui 问答区通过 |
| W4 | 单测全覆盖≥80%、集成、`README`、docker 可选 | pytest 绿、docker-compose up 可用 |

**V1.0 发布标准**：pytest 全绿、`/ui` 完成"上传→问→看引用→删"闭环、`rageval` 可运行且报告非空。

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
| DeepSeek 不可用/限流 | 超时+重试 3 次→回 sign 30001；One-pass 后可选 Ollama 兜底 |
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
| 多租户/细粒度权限（不同用户只能看部分库） | 需引入"用户→collection/过滤条件"的鉴权映射，并防 metadata 伪造 | 有真实权限诉求再评审 |
| 秒级实时同步（文档经常更新） | 需增量索引 + 消息队列、文档变更监听 | 内容更新频率成瓶颈时 |
| 跨业务系统对接（OA/工单/数据库） | 需"接入器 + 数据变更事件"而非通用 RAG | 单独立项 |
| 亿级向量高并发 | Milvus + 多副本 + 横向扩展 | 单机瓶颈量化后再做（另见 10.1） |

> **总原则**：MVP 只做深度 A/B；深度 C 的四个方向，全部等"真实业务指标或用户量"驱动后再投入，不要提前债务化。核心的接口抽象与 `filters/collection` 已在本版预留。

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

（全文完）