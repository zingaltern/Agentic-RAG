# Agentic RAG 智能知识库系统

基于 FastAPI + BGE(中文嵌入) + Chroma + BM25/RRF 混合检索 + DeepSeek(LLM) 的企业知识库问答系统，带 Agentic 反思循环与幻觉检测。

> **面向第一次使用的用户**：请先阅读 [产品说明书（PRODUCT_MANUAL.md）](PRODUCT_MANUAL.md)——从怎么启动、怎么上传文档到"认证与连接"该不该填，都有图文讲解。

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

前后端分离：`frontend/` 独立工程（Vue3 + Vue Router + Pinia + Element Plus），四区块
（问答控制台 / 文档管理 / 知识库管理 / 设置总览）全部对接真实 API；
`src/api/base.js` 是 `BASE_URL` 单一常量（13.11.2，Web 与桌面共享的后端地址策略唯一入口）。
错误提示为中文人话文案（错误码/trace_id 仅入日志，不上界面）。

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
- **异常自动恢复**：运行中后端进程意外退出（含被外部信号终止）→ 桌面壳自动重启并重新等待就绪（最多 2 次），恢复失败才弹错误框退出，不再卡死。
- **退出**：关窗/退出 → 级联终止后端子进程（Windows taskkill /T，其他 SIGTERM→SIGKILL 兜底），
  可用 `lsof -i :8000` 自验无残留。
- **外部后端模式**：设置页「外部后端」填写 `127.0.0.1:port` → 保存后重启应用，不再自动拉起本地服务。
- **打包**：`cd frontend && npx electron-builder --mac dmg`（配置在 frontend/package.json 的 build 段）。
  Python 运行时捆绑为后续演进（本次仅打包前端壳；部署机需 Python 3.11 + `.venv` 与模型缓存）。
  打包后的 `.app` 需要项目根定位后端子进程：双击前设置
  `launchctl setenv RAG_PROJECT_DIR /path/to/Agentic-RAG`（或终端
  `RAG_PROJECT_DIR=/path/to/Agentic-RAG open -a AgenticRAG`），未配置时应用会弹出引导提示。
- **安全基线（S8）**：`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`、
  仅加载 loopback/自托管地址；真实密钥只经环境变量运行时注入（RAG_DEEPSEEK_API_KEY 等）。

## 认证与权限（V5.1，S1/13.11.1）

**① 个人模式（契约默认）**：`auth_enabled=false` 时无登录页、无鉴权，所有操作可用，行为与单用户版一致（当前部署已开启团队模式）。

**② 团队模式三步**（小团队共享，管理员/普通用户两级）：
1. `configs/settings.yaml` 设 `api.auth_enabled: true`；
2. 配置环境变量 `RAG_ADMIN_USER` / `RAG_ADMIN_PASSWORD`（首管理员引导，禁止写盘/入库）后重启；
   也可省略（日志提示后）由部署方经 CLI 创建首管理员；
3. 管理员在 Web「设置总览 → 用户管理」建用户（用户名/初始密码/角色），登录即用。

- 登录：`POST /api/auth/login` → `{token, user}`，前端本地持久化（`Authorization: Bearer <token>`）；
  `GET /api/auth/me`、`POST /api/auth/change-password`；未带/错误凭证 → `40101`；
  `/api/health`、`/api/plugins/status`、`/ui`、`/api/auth` 白名单放行；响应保持 `{code,message,data}` 契约。
  密码 scrypt 哈希存储；登录失败返回通用文案「用户名或密码错误」（不泄露账号是否存在）；
  **防爆破：连续失败 5 次锁定来源 IP 5 分钟**（进程内计数，成功登录重置）。
- **权限（知识库级，V5.1）**：共享库（`scope=shared`，含 default）全员可读、仅管理员可写/删；
  私有库（`scope=private`）仅 owner 与管理员可见可管理；普通用户建库服务端强制为私有。
- **用户管理 API（仅 admin）**：`GET/POST /api/users`、`POST /api/users/import`（CSV 批量导入：表头
  `用户名,姓名,初始密码,角色`，全量校验通过才写入，失败返回逐行明细）、`POST /api/users/{id}/reset-password|role`、`DELETE /api/users/{id}`（禁删自己）。
- 审计（S6）：query/upload 事件含 `user_id`（认证态取真实用户，否则 anonymous）、kb_id 与回答摘要，
  不含密码/Token/完整敏感文本；`trace_id` 全链路贯穿（记录于后端 JSON 日志，前端不展示）。
  **注意（V5.1）**：审计日志含用户查询与回答摘要，属个人数据——日志文件权限已收紧（桌面 `backend.log` 0o600），
  留存与访问控制由部署方制度化管理。
- 密码策略（V5.1）：新密码/重置密码 ≥8 位且同时含字母与数字；已有用户不强求改密。
- 敏感数据脱敏（S6）：`ingestion.desensitize_enabled=true` 时身份证/手机号/银行卡入库前替换为 `***`
  （默认关闭；HR/财务等合规场景建议开启，开启后号码类检索会失效，改策略前跑 `make eval` 对比）。
- 并发与限速（V5.1）：并发查询上限 `orchestration.max_concurrent_queries`（默认 4，超限 43001）；
  `/api/query` 20 次/分/IP、上传 10 次/分/IP（超限 43002）；入库队列上限 64（超限 43003）。
- CLI 评测（认证启用时）：`python -m app.cli.rageval --base-url http://127.0.0.1:8000 --login <user> <pass>` 或 `--token <token>`。

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
| GET/POST | `/api/plugins/status` `/api/plugins/switch` | 插件 provider 状态 / 热切换（switch 需登录且仅管理员） |

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

开发完成，规格见 `DEVELOPMENT.md` 第 13 章：

| 周 | 内容 | 状态 |
|---|---|---|
| W5 | 真重排（bge-reranker）+ 语义评估/LLM 改写/真闭环 + 幻觉检测 v2 | 已落地 |
| W6 | 多知识库（一库一 collection，`/api/kbs`）+ 评测集 | 已落地 |
| W7 | Web 前端：前后端分离，`frontend/` Vue3+Vite SPA | 已落地 |
| W8 | Electron 桌面壳（内置拉起后端）+ 收尾 | 已落地 |

当前阶段面向收尾与加固：桌面打包（Python 运行时捆绑待决策）、认证/权限（`api.auth_enabled` + `RAG_ADMIN_USER/RAG_ADMIN_PASSWORD`，当前部署已开启团队模式）、文档索引持久化等。

Web 前端开发期用 `cd frontend && npm run dev`（proxy 到 :8000）；桌面端 W8 落地后 `make desktop`。

## 目录速览

```
application.py           # 装配层：中间件（CORS/安全头/限速/审计）+ 依赖注入
app/
├── main.py            # FastAPI 入口（装配 DI → 启动，托管 /ui）
├── api/               # health / documents / query / plugins / jobs / kbs / users / auth 路由
├── core/              # domain 数据模型 / interfaces 接口契约 / config / errors / logging / registry / rate_limit
├── adapters/          # embedding / vector_store / retriever / reranker / evaluator / rewriter / detector / llm / parser / chunker
├── services/          # orchestration（反思循环）/ ingestion / jobs_store / documents_store / kb_store / user_store / rbac / desensitize
└── cli/               # demo_ingest / rageval
web/                   # /ui 内置测试页（无前端工程，冒烟/降级入口）
frontend/              # Vue3 SPA + Electron 桌面壳（electron/main.cjs、bootstrap.cjs）
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
