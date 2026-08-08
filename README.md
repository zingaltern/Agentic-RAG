# Agentic RAG 智能知识库系统

基于 FastAPI + BGE(中文嵌入) + Chroma + BM25/RRF 混合检索 + DeepSeek(LLM) 的企业知识库问答系统，带 Agentic 反思循环与幻觉检测。

## 快速开始

```bash
make bootstrap      # 创建 .venv 并安装依赖（首次，含 torch/嵌入模型依赖，较慢）
make run            # 启动服务 → http://127.0.0.1:8000（SPA 与 /ui 均由本服务托管）
make test           # 跑全部单测（含覆盖率门禁 ≥80%）
make eval           # 评测（进程内模式，输出 reports/eval-*.md）
make demo-ingest    # 入库 demo 文档并检索验证
```

> **监听地址（S2）**：默认仅绑定 `127.0.0.1`（本机/桌面场景）；对外暴露需显式
> `uvicorn app.main:app --host 0.0.0.0` 或改 `configs/settings.yaml` 的 `api.host`。

## 前端 SPA（W7，Vue3 + Vite + Element Plus）

前后端分离：`frontend/` 独立工程（Vue3 + Vue Router + Pinia + Element Plus），五区块
（问答控制台 / 文档管理 / 知识库管理 / 设置总览 / 错误码提示条）全部对接真实 API；
`src/api/base.js` 是 `BASE_URL` 单一常量（13.11.2，Web 与桌面共享的后端地址策略唯一入口）。

```bash
make frontend-install   # 首次安装前端依赖（cd frontend && npm install）
make frontend-dev       # dev 模式 → http://localhost:5173（vite proxy /api → 127.0.0.1:8000）
make frontend-build     # 构建产物 → frontend/dist
make run                # 生产托管：SPA 由 FastAPI 同源托管在 http://127.0.0.1:8000/
```

- **dev 模式 CORS**：`configs/settings.yaml` 的 `api.cors_origins: ["http://localhost:5173"]`
  已启用（后端 CORSMiddleware）；生产同源托管无 CORS 限制。
- **托管**：`frontend/dist` 由 FastAPI 挂载（API 路由在前、静态在后），SPA history 路由
  （如 `/documents`）自动回退 index.html；`/api/*` 不受影响。
- **旧测试页降级入口**：`http://127.0.0.1:8000/ui`（`web/` 静态页，保留为冒烟/降级入口，
  新 SPA 上线后逐步取代）。
- 构建产物 `frontend/dist` 随仓库提交（AGENTS.md 约定）；`frontend/node_modules/` 不入库。

## 准备 DeepSeek Key

- 复制 `.env.example` 为 `.env`，填入真实 Key：`RAG_DEEPSEEK_API_KEY=sk-...`（只走环境变量，`.env` 已被 gitignore，绝不入库）。
- LLM 装配失败（无 key）时服务照常启动，`/api/health` 的 `llm` 显示 `not_loaded`；问答时返回错误码 30001。

## 首次启动会自动下载嵌入模型

- 默认嵌入模型 `BAAI/bge-small-zh-v1.5`（约 100MB，下载到 HF 缓存）。
- 国内网络需设镜像：`export HF_ENDPOINT=https://hf-mirror.com`（Makefile 已内置兜底，`make run` / `make demo-ingest` 无需手敲）。
- 模型缓存位于 `~/.cache/huggingface`，只需联网一次。

## 常用命令

| 命令 | 说明 |
|---|---|
| `make bootstrap` | venv + 依赖（清华镜像，失败兜底官方 PyPI） |
| `make run` | 启动 API（含 `/ui` 测试页）→ http://localhost:8000/ui |
| `make test` | 单测 + 覆盖率（`fail_under=80`，默认过滤 integration） |
| `make coverage` | 仅输出覆盖率报告（不设失败门禁） |
| `make eval` | 评测：`reports/eval-{时间戳}.md`（逐条 + Hit@5 等聚合指标） |
| `make integration` | 集成测试（需要本机 BGE 模型缓存，不联网） |
| `make demo-ingest` | 入库 demo 文档并检索验证 |
| `make docker-up` | 构建并启动 api 容器（可选） |

## Docker（可选）

```bash
make docker-up      # docker compose up --build -d api
curl http://localhost:8000/api/health    # 期望 checks 全 ok
```

- 镜像内已内置 `HF_ENDPOINT=https://hf-mirror.com` 兜底；DeepSeek key 通过 compose 的 `env_file: .env` 注入（不入镜像）。
- MVP 用 embedded Chroma（进程内 SQLite），`./data` 挂载为持久卷。

## 桌面端（W8，Electron）

内置拉起后端三步走（启动/就绪/退出），`make desktop` 一条命令即起：

```bash
make desktop   # 依赖：.venv（make bootstrap）、frontend 依赖（make frontend-install）、构建产物（make frontend-build）
```

- **启动**：探测端口（默认 8000，占用自动后移，最多试 5 个）→ 以项目 `.venv/bin/python` 拉起
  `uvicorn app.main:app`（仅 `127.0.0.1` 监听）；子进程失败弹错误框而非白屏。
- **就绪**：轮询 `/api/health`（≤30s，窗口就绪后才显示）→ 加载与 Web 端同一 SPA。
- **退出**：关窗/退出 → 级联终止后端子进程（Windows taskkill /T，其他 SIGTERM→SIGKILL 兜底），
  可用 `lsof -i :8000` 自验无残留。
- **外部后端模式**：设置页「外部后端」填写 `127.0.0.1:port` → 保存后重启应用，不再自动拉起本地服务。
- **打包**：`cd frontend && npx electron-builder --mac dmg`（配置在 frontend/package.json 的 build 段）。
  Python 运行时捆绑为后续演进（本次仅打包前端壳；部署机需 Python 3.11 + `.venv` 与模型缓存）。
- **安全基线（S8）**：`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`、
  仅加载 loopback/自托管地址；真实密钥只经环境变量运行时注入（RAG_DEEPSEEK_API_KEY 等）。

## 认证（S1，13.11.1）

- `configs/settings.yaml` 的 `api.auth_enabled: true` + 环境变量 `RAG_API_KEY`（禁止写盘/入库）启用 API Key 认证。
- 启用后：`/api/query`、文档、kbs 等业务请求须带 `X-API-Key` 头，未带/错误 → `40101`；
  `/api/health`、`/api/plugins/*`、`/ui` 白名单放行；响应保持 `{code,message,data}` 契约。
- 前端：设置总览页填写并本地持久化 API Key；401 时全局提示条引导。
- **RBAC 预留**（13.11.3）：`app/services/rbac.py` 提供 `resolve_user_kb(user_id, kb_id)` 接口
  （默认全放，可注入 `user_kb_map` 收窄）；对接 §12.3 深度 C 时替换策略即可，路由层不改。
- 审计（S6）：query/upload 事件含 `user_id`（`X-User-Id` 透传，缺省 anonymous）、kb_id 与回答摘要，
  不含 key 与完整敏感文本；`trace_id` 全链路贯穿（前端设置页/问答区可查）。

## API 速览

统一响应包 `{code, message, data}`；错误码见 DEVELOPMENT.md 6.7 表。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 组件健康（embedding/vector_store/llm） |
| POST | `/api/documents/upload` | 上传入库（可选表单字段 `kb_id`，缺省 default） |
| GET/DELETE | `/api/documents/{id}` | 文档元数据 / 删除（级联清向量块） |
| GET | `/api/jobs/{job_id}` | 上传任务状态轮询 |
| POST | `/api/query` | 问答（可选 `kb_id`，与 `collection` 二选一，kb_id 优先） |
| GET/POST/DELETE | `/api/kbs` | 知识库 CRUD（13.5：内置 default 不可删） |
| GET/POST | `/api/plugins/status` `/api/plugins/switch` | 插件 provider 状态 / 热切换（switch 待 W 后续） |

**多知识库（13.5）**：`kb_id → Chroma collection` 物理隔离；上传/问答按 `kb_id` 作用域，
A/B 库内容互不可见；`/api/kbs` 建库（name 自动 slug 化生成 kb_id）、删库级联清空向量集合。
默认库 `default` 沿用存量 collection `kb`，不可删除。

## 评测报告与已知限制

- `make eval` 报告输出到 `reports/eval-*.md`（`reports/` 已 gitignore，产物不入库）。
- 全库评测（default+docs，107 条）：`make eval`（进程内模式）；HTTP 真链路：`python -m app.cli.rageval --all-kbs --base-url http://localhost:8000 --baseline <报告>`。
- 指标：Hit@1 / Hit@5 / ContextPrecision / ContextRecall / AnswerRelevancy / Faithfulness（分类型）/ 兜底率；按库汇总 + 基线 diff 列。
- no_answer 判据（13.6 修订）：`code=0 且回答含"未找到相关依据"类文案 ∨ code=21001`。
- **已知限制**（如实列出，供检索/改写调优）：
  - 口语化问法（如"咋整"）可能因改写未覆盖而评估分不足 → 触发 21001 兜底；同义词表见 `configs/synonyms.csv`（缺失时用内置小表）。
  - 进程内评测（Stub LLM）的 Faithfulness 恒 0%（Stub 答案与上下文不匹配属预期），真 DeepSeek 模式约 69%。
  - LLM 生成的规范化总结句对幻觉检测阈值达标率有限，阈值可在 `configs/settings.yaml` 调整。

## 第二阶段（Phase 2）规划速览

开发中，规格见 `DEVELOPMENT.md` 第 13 章，分 W5~W8：

| 周 | 内容 | 状态 |
|---|---|---|
| W5 | 真重排（bge-reranker）+ 语义评估/LLM 改写/真闭环 + 幻觉检测 v2 | 待开发 |
| W6 | 多知识库（一库一 collection，`/api/kbs`）+ 评测集 100+ | 待开发 |
| W7 | Web 前端：前后端分离，`frontend/` Vue3+Vite SPA | 待开发 |
| W8 | Electron 桌面壳（内置拉起后端）+ 收尾 | 待开发 |

Web 前端开发期用 `cd frontend && npm run dev`（proxy 到 :8000）；桌面端 W8 落地后 `make desktop`。

## 目录速览

```
app/
├── main.py            # FastAPI 入口（装配 DI → 启动，托管 /ui）
├── api/               # health / documents / query / plugins / jobs 路由
├── core/              # domain 数据模型 / interfaces 接口契约 / config / errors / logging / registry
├── adapters/          # embedding / vector_store / retriever / reranker / evaluator / rewriter / detector / llm / parser / chunker
├── services/          # orchestration（反思循环）/ ingestion / jobs_store / documents_store
└── cli/               # demo_ingest / rageval
web/                   # /ui 内置测试页（无前端工程）
configs/settings.yaml  # 系统配置
tests/unit/            # 单测；tests/integration/ 集成测试（-m integration）
```

## 里程碑（DEVELOPMENT.md 第 9 章）

| 周 | 交付 |
|---|---|
| W1 | 工程骨架、接口契约、Config、Parser + Chunker、UI 骨架 |
| W2 | VectorEngine + VectorStore + Retriever(RRF) + 上传 Job 流水线 |
| W3 | Orchestration 反思循环 + Detector + `/api/query` + DeepSeek |
| W4 | 测试全覆盖≥80% + 集成 + 评测 + 可选 Docker |
| W5 | 精排 bge_reranker + 语义评估/LLM 改写/语义检测（Phase2） |
| W6 | 多知识库隔离 + 评测体系 v2 |
| W7 | Vue3 SPA 五区块 + CORS/静态托管（Phase2） |
| W8 | Electron 桌面壳 + API Key 认证 + RBAC 预留（当前） |

## 测试

```bash
make test                        # 单测 + 覆盖率门禁
.venv/bin/python -m pytest -m integration   # 集成全链路（需本机 BGE 缓存）
make eval                        # 评测报告
```
