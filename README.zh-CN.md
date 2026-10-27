<p align="center">
  <img src="./frontend/resources/icon-1024.png" width="240" alt="Agentic RAG 应用图标">
</p>

<h1 align="center">Agentic RAG</h1>

<p align="center">自托管知识库问答：检索、自查，可选先规划，只回答有证据支撑的内容。</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/zingaltern/Agentic-RAG/actions/workflows/ci.yml"><img src="https://github.com/zingaltern/Agentic-RAG/actions/workflows/ci.yml/badge.svg" alt="CI 状态"></a>
  <img src="https://img.shields.io/badge/Python-3.10--3.12-3776AB?style=flat-square" alt="Python 3.10 至 3.12">
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-4B5563?style=flat-square" alt="FastAPI 0.110 及以上">
  <img src="https://img.shields.io/badge/Chroma-0.5%2B-4B5563?style=flat-square" alt="Chroma 0.5 及以上">
  <img src="https://img.shields.io/badge/DeepSeek-LLM-4B5563?style=flat-square" alt="DeepSeek 大模型">
  <img src="https://img.shields.io/badge/Vue-3.5-3776AB?style=flat-square" alt="Vue 3.5">
  <img src="https://img.shields.io/badge/Electron-43-4B5563?style=flat-square" alt="Electron 43">
  <img src="https://img.shields.io/badge/Docker-ready-0891B2?style=flat-square" alt="支持 Docker">
</p>

多数 RAG 示例止步于"向量化、检索、生成"。这个项目把后半程补齐：首轮召回不理想时会改写问题重试；Agent 模式先做规划再调用工具（本仓库配置默认开启，可关闭）；回答会与真正用到的证据做一致性校验；并用一套 197 条开发验证集与独立的 120 条冻结基准给自己打分。文档、向量与索引都留在本机。联网关闭时（服务端默认 `web.enabled=false`），回答链路只调用 DeepSeek API（首次下载模型除外）；开启请求级「联网」开关（需配置 Tavily Key）后，问题文本会额外发送至 Tavily。

## 核心特性

| 特性 | 价值 |
|---|---|
| BGE 中文向量 + BM25 双路检索，RRF 融合排序 | 精确词与同义转述都有机会被召回，不把结果押在单一路径上。 |
| 反思循环与 Agent 两种回答模式（本仓库默认开启，可关闭） | 默认走成本更低的迭代闭环，问题需要拆解时再升级为规划式 Agent。 |
| 回答绑定证据 | 每个知识回答都带来源、引用标号与幻觉检测结论；问候与证据不足的兜底绕过检测，返回兜底码而不是编一个答案。 |
| 一个知识库对应一个 Chroma collection | 上传、查询、删除都在服务端按库隔离，库与库之间不会串内容。 |
| 支持 46 种可识别扩展名 | PDF、Office、Markdown、HTML、邮件、CSV/TSV、JSON；代码格式在有 tree-sitter grammar 时用 AST 提取符号，配置格式回退纯文本。`.msg` 仅保留以给出明确的 `.eml` 指引。 |
| 评测随仓库交付 | 197 条开发验证集、160 条外部测试集、吞吐与延迟基准均通过 `make` 运行；120 条冻结基准通过 `tests/eval_suite/runner.py` 对运行中的服务执行。 |

## 架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Clients   Vue 3 SPA (/)  ·  Electron shell  ·  HTTP API      │
└──────────────────────────────────────────────────────────────┘
                                │
                                │  REST / JSON
                                ▼
┌──────────────────────────────────────────────────────────────┐
│ FastAPI   auth · RBAC · rate limit · audit · trace_id        │
└──────────────────────────────────────────────────────────────┘
                                │
                                │
            ┌───────────────────┴──────────────────┐
            │ upload                               │ query
            ▼                                      ▼
┌────────────────────────┐            ┌────────────────────────┐
│ Ingestion              │            │ Query orchestration    │
│ parse · clean · chunk  │            │ reflect / agentic      │
│ embed with BGE         │            │ plan · tools · cite    │
└────────────────────────┘            └────────────────────────┘
            │                                      │
            ▼                                      ▼
┌────────────────────────┐            ┌────────────────────────┐
│ Chroma + BM25 index    │◀───────────│ Retriever · RRF        │
│ one collection per KB  │            │ vector + BM25 fusion   │
└────────────────────────┘            └────────────────────────┘
                                                   │
                                                   ▼
                                      ┌────────────────────────┐
                                      │ DeepSeek LLM           │
                                      │ answer + detector      │
                                      └────────────────────────┘
```

上传走后台任务：解析、可选清洗、切父子块，用 `BAAI/bge-small-zh-v1.5` 生成向量，写入该知识库的 Chroma collection。查询时同时打向量索引与内存 BM25 索引，用 RRF 融合两路排名，再把上下文交给反思循环或 Agent 规划器。知识型正常回答返回前会过一遍幻觉检测；问候快路与证据不足的兜底返回不经过 detector。

组件名称与英文版保持一致，便于两版对照阅读。

## 使用示例

启动服务后，向内置的 `default` 知识库提问：

```bash
curl -s http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "入库流水线用的是哪种分块策略？", "kb_id": "default"}'
```

接口返回 HTTP 200，body 是统一响应包 `{code, message, data}`。成功时 `code` 为 `0`，`data` 同时带回答案与证据链：

| 字段 | 含义 |
|---|---|
| `answer` | 生成的答案文本，其中 `[1]` 这类标号指向 `sources`。 |
| `sources` | 实际作为上下文使用的检索块，附带文档元数据。 |
| `citations` | 答案中出现过哪些引用标号，以及检测器确认的比例。 |
| `hallucination_check` | `passed` 或 `suspected`。 |
| `iterations` / `elapsed_ms` | 实际执行的检索轮数与服务端耗时。 |
| `mode` / `agent_steps` / `termination_reason` | 本次走的是哪条回答路径，以及为什么停下。 |

业务失败同样返回 HTTP 200，原因写在 `code` 里：`30001` 表示 LLM 不可用（未配置密钥或调用失败），`21001` 表示没有找到支撑证据，`40101` 表示调用方没有该知识库的读权限。

## 安装

需要 Python 3.10 至 3.12、`make` 和 Git。首次安装会拉取 PyTorch 与嵌入依赖，下载量较大。

```bash
git clone https://github.com/zingaltern/Agentic-RAG.git
cd Agentic-RAG
make bootstrap
```

`make bootstrap` 会创建 `.venv` 并安装项目与开发依赖。默认使用清华 PyPI 镜像，镜像不可用时自动回退官方源；可用 `make bootstrap PIP_INDEX_URL=<url>` 覆盖。

## 快速开始

```bash
cp .env.example .env                 # 随后在 .env 中填写 RAG_DEEPSEEK_API_KEY
make run                             # API 与 SPA 同时在 http://127.0.0.1:8000 提供服务
```

浏览器打开 `http://127.0.0.1:8000` 即为 Web 控制台（SPA 由随仓库提交的 `frontend/dist` 托管）。先确认组件在线：

```bash
curl -s http://127.0.0.1:8000/api/health
```

响应会给出 `status` 与 `checks`，分别对应 `embedding`、`vector_store`、`llm`。默认是装配探活：`llm: ok` 只表示 provider 已装配，不代表 Key 真实可用；外部监控若要确认 LLM 真能叫通，请用 `GET /api/health?probe=1`（最小真实请求，结果缓存 60s）。嵌入模型 `BAAI/bge-small-zh-v1.5` 首次使用时会下载到 Hugging Face 缓存，后台预热期间可能短暂显示 `not_loaded`。没有配置 `RAG_DEEPSEEK_API_KEY` 时服务照常启动，`llm` 保持 `not_loaded`，问答返回 `30001`。

上传一份文档并等待入库任务结束：

```bash
curl -s -F "file=@./README.md" -F "kb_id=default" http://127.0.0.1:8000/api/documents/upload
curl -s http://127.0.0.1:8000/api/jobs/<job_id>
```

上传接口返回 `job_id`，轮询到 `status` 不再是 `pending` 或 `running` 即完成。此后再执行上面的「使用示例」，就能针对这份文档得到带引用的回答。

若机器没有外网且模型已缓存，请给命令加 `HF_HUB_OFFLINE=1` 前缀，否则加载器会卡在适配器配置的联网检查上。

## 常用命令

| 命令 | 作用 |
|---|---|
| `make bootstrap` | 创建 `.venv`，安装运行与开发依赖。 |
| `make run` | 启动 API，并在 `http://127.0.0.1:8000` 托管已构建的 SPA。 |
| `make test` | 跑单测，覆盖率门禁 80%。 |
| `make eval` | 跑 197 条验证集，输出 `reports/eval-*.md`。 |
| `make review` | 用 CMRC 2018 的 160 条外部测试集，打到独立 `cmrc` 知识库。 |
| `make perf` | 测 50/150/300 文档规模下的入库吞吐、查询延迟分位与 Hit@5。 |
| `make integration` | 基于本机模型缓存跑端到端集成测试。 |
| `make demo-ingest` | 入库 demo 文档并验证检索。 |
| `make rebuild KB=<id>` | 用归档原件重建单个知识库。 |
| `make frontend-install` / `make frontend-dev` | 安装前端依赖并在 `http://localhost:5173` 起开发服务。 |
| `make frontend-build` | 构建 SPA 到 `frontend/dist`。 |
| `make desktop` | 启动 Electron 桌面壳，由其拉起并守护后端。 |
| `make docker-up` / `make docker-down` | 用 Docker Compose 起停 API 容器。 |

## API

所有响应统一为 `{code, message, data}`。`frontend/dist` 存在时 SPA 挂在 `/`；`/ui` 保留为旧的冒烟测试页。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/health` | 嵌入、向量库、LLM 的组件健康状态。 |
| POST | `/api/query` | 提问，接受 `query`、`kb_id`、`mode`、`top_k`、`filters`、`web_search`。 |
| POST | `/api/documents/upload` | 上传入库，接受 `file`、`kb_id`、`mode`（`add` 或 `upsert`）、`force`。 |
| GET | `/api/documents` | 列出某知识库下的文档。 |
| GET / DELETE | `/api/documents/{document_id}` | 读取元数据，或删除文档及其向量块。 |
| POST | `/api/documents/batch-delete` | 单次最多删除 200 篇文档。 |
| GET | `/api/jobs/{job_id}` | 轮询入库任务。 |
| GET / POST | `/api/kbs` | 列出或创建知识库。 |
| DELETE | `/api/kbs/{kb_id}` | 删除知识库及其向量集合。 |
| GET / POST | `/api/plugins/status`、`/api/plugins/switch` | 查看或热切换 provider。 |
| POST | `/api/auth/login` | 登录并获取 bearer token。 |
| GET | `/api/auth/me`、POST `/api/auth/change-password` | 读取当前用户或修改密码。 |
| GET / POST | `/api/users` | 列出或创建用户，仅管理员。 |
| POST | `/api/users/import` | 用 CSV 批量创建用户。 |
| POST | `/api/users/{user_id}/reset-password`、`/role` | 重置密码或调整角色。 |
| DELETE | `/api/users/{user_id}` | 删除用户。 |
| GET | `/api/metrics` | 暴露进程内计数器。 |

`mode` 可传 `reflect`、`agentic` 或 `auto`。`auto` 把简单问题交给反思循环、复杂问题升级给 Agent，且仅在 `orchestration.agent.enabled` 打开时生效。

## 配置

配置优先级为：内置默认值 < `configs/settings.yaml` < `RAG_` 前缀环境变量。业务代码统一经 `app/core/config.py` 读取，密钥只放环境变量。

| 环境变量 | 作用 |
|---|---|
| `RAG_DEEPSEEK_API_KEY` | DeepSeek API Key，生成答案必需。 |
| `RAG_ADMIN_USER` / `RAG_ADMIN_PASSWORD` | 开启团队模式时用于引导首个管理员。 |
| `HF_ENDPOINT` | 模型下载镜像，Makefile 中默认 `https://hf-mirror.com`。 |
| `HF_HUB_OFFLINE` | 设为 `1` 时模型加载完全离线。 |

服务默认监听 `127.0.0.1:8000`。需要对外暴露时，用 `uvicorn app.main:app --host 0.0.0.0`，或改配置文件里的 `api.host`。

## 安全与权限

- 个人模式是默认值：`api.auth_enabled` 为 `false`，没有登录页与鉴权，所有操作可用。
- 团队模式提供密码登录、scrypt 哈希存储与知识库级权限。共享库全员可读、仅管理员可写；私有库仅 owner 与管理员可见。内置 `default` 库不可删除。
- 连续登录失败会锁定来源 IP 五分钟。问答与上传按 IP 限流，并发查询由信号量封顶。
- `trace_id` 从请求贯穿到回答，写入 JSON 日志。审计记录包含用户、知识库与回答摘要，不含密码与 Token。
- 解析侧做魔数校验与压缩炸弹防御。提示注入扫描、内容审核、PII 脱敏均已实现但默认关闭；开启注入扫描后默认走 audit 模式，只打标不拦截。

## 评测与基准

报告输出到 `reports/`，该目录不入版本库。

- `make eval` 覆盖 `default`、`docs`、`complex` 三组共 197 条问答，输出 Hit@1、Hit@5、上下文精确率与召回率、答案相关性、分类型忠实度与兜底率。
- `make review` 用 CMRC 2018 采样作为外部测试集，打进独立的 `cmrc` 库，与验证集物理隔离。
- `make perf` 测 50、150、300 文档规模下的入库吞吐、查询延迟分位与 Hit@5 曲线。
- `make eval-quality` 在脚本化 fixture 上跑一轮检索冒烟。

## 已知限制

- 默认回答路径仍是检索侧的自我修正循环。Agent 模式的规划器、工具调用与证据账本已具备基础版本；本仓库配置默认开启（可将 `orchestration.agent.enabled` 设为 false 关闭），专用评测尚未完成。
- 进程内评测的忠实度接近 0（最近一次全量 0–3%），因为 Stub LLM 的输出与检索上下文不匹配；接真实 DeepSeek 后分数显著提高。进程内数字应看作检索信号，而非答案质量。
- 口语化问法若超出改写覆盖范围，仍会以 `21001` 兜底。同义词表在 `configs/synonyms.csv`。
- `.doc` 与 `.ppt` 依赖宿主机安装 LibreOffice，未安装时上传会明确报错并给出安装指引。`.msg` 暂不解析，接口会提示改用 `.eml`。图片 OCR 只有接口，尚未接引擎。
- 清洗层 `cleaner.enabled` 默认关闭。开启会改变入库结果，需重建受影响的知识库。

## 目录结构

```text
app/
├── main.py            # FastAPI 入口
├── application.py     # 中间件、依赖注入、SPA 挂载
├── api/               # 路由：auth、users、kbs、documents、jobs、query、health、metrics、plugins
├── core/              # 领域模型、接口契约、配置、错误、日志、限流
├── adapters/          # 嵌入、向量库、检索器、精排、解析、切块、清洗、LLM、Agent
├── services/          # 编排、Agent 运行时、入库、任务与文档存储、RBAC
└── cli/               # demo 入库、评测、测试集构建、性能基准
web/                   # 旧的 /ui 冒烟测试页
frontend/              # Vue 3 SPA 与 Electron 桌面壳
configs/settings.yaml  # 系统配置
docs/                  # 文档索引、方案规格与任务书
tests/                 # 单测、集成、验证集、外部测试集、冻结基准集
```

## 文档

| 主题 | 内容 | 链接 |
|---|---|---|
| 产品使用 | 界面、上传流程与各设置项的含义 | [PRODUCT_MANUAL.md](./PRODUCT_MANUAL.md) |
| 规格说明 | 接口契约、数据模型、算法与验收标准 | [DEVELOPMENT.md](./DEVELOPMENT.md) |
| 文档索引 | `docs/` 的组织方式与新增文档的归档约定 | [docs/README.md](./docs/README.md) |
| Agentic 设计 | 规划器、工具调用、证据账本与路线图 | [docs/specs/真Agentic_RAG设计.md](./docs/specs/真Agentic_RAG设计.md) |
| 验证集 | `make eval` 的判据与打分方式 | [tests/eval/README.md](./tests/eval/README.md) |
| 外部测试集 | CMRC 测试集的构建与运行方式 | [tests/review/README.md](./tests/review/README.md) |
| 冻结基准集 | 120 条冻结集的规范 | [tests/eval_suite/README.md](./tests/eval_suite/README.md) |
| 贡献约定 | 开发 Agent 的工程规范 | [AGENTS.md](./AGENTS.md) |

## 状态

第一、第二阶段已交付：检索闭环、多知识库隔离、Vue 3 SPA、Electron 桌面壳与团队模式认证。当前处于加固阶段，主要是桌面打包方案决策与 Agentic 评测线。
