# AGENTS.md

> 本项目为 **Agentic RAG 智能知识库系统**（Python 3.10~3.12 + FastAPI）。
> 你是本项目的开发 Agent，请遵守本文件的工程规范与任何任务指令的要求。

## 文档体系（先读这里，别乱猜）

| 文件 | 作用 | 必读性 |
|---|---|---|
| `DEVELOPMENT.md` | **唯一规格来源**（接口契约、数据模型、算法、配置、API、验收）。开发前**必须**按任务范围读取对应章节，禁止凭别的理解自由发挥 | 高 |
| `TASK.md` | 当前任务简报（某周里程碑与验收判据）——由任务指令给定 | 高 |
| `README.md` | 快速启动（`make run / make test`） | 中 |
| `PRODUCT_MANUAL.md` | 产品说明书（面向终端用户，界面/功能口径） | 中 |
| `docs/review-followup.md` | 评审延后项清单与状态 | 低 |
| `tests/eval_suite/README.md` | 独立基准评测规范（120 条冻结集，第二道门禁） | 评测时必读 |

- 冲突时：`DEVELOPMENT.md` > 任务指令 > `AGENTS.md` 中与上述冲突的表述。
- 若你发现规格确实需要变更：**先改 DEVELOPMENT.md 再改代码**，并在 commit message 里说明。

## 常用命令

```bash
make bootstrap      # 虚拟环境 + 安装依赖（首次）
make run            # 启动 API（SPA + /ui 测试页）→ http://127.0.0.1:8000
make test           # 跑全部单测（含覆盖率门禁 ≥80%）
make eval           # 评测（reports/eval-*.md）
python -m app.cli.rageval --kb default   # 评测（按库；--all-kbs 全库；--in-process 进程内）
# —— 第二阶段（第 13 章）——
cd frontend && npm install && npm run dev     # Web 前端（vite，proxy → :8000）
cd frontend && npm run build                  # 产物 dist，由 FastAPI 挂载
make desktop        # Electron 桌面（内置拉起后端）【W8 落地】
```

- 第二阶段规格见 DEVELOPMENT.md **第 13 章**（W5~W8 里程碑 + DoD）。改动都必须跑 `make eval` 留下**前/后对比报告**才算完成（13.1 度量协议）。

## 环境与密钥

- LLM 为 DeepSeek API：`RAG_DEEPSEEK_API_KEY`（只走环境变量，**禁止**写入文件/配置/代码/commit）。
- 参考 `env.example`；真实 `.env`、`data/`（Chroma）、模型缓存必须在 `.gitignore` 中排除。

## 代码规范

- 中文注释；标识符 `snake_case`；抽象接口放 `app/core/interfaces.py`，实现只放 `app/adapters/` 下，类名=`策略名+接口名`（如 `LocalBGEBellEmbedding(VectorEngine)`）。
- **所有配置从 `app/core/config.py` 的 `SystemConfig` 读取**，禁止业务代码出现 `os.getenv` 散落。
- 外部 I/O（LLM、文件、模型加载）必须有超时与异常包装，异常统一用 `core/errors.py`。
- 不新增 `DEVELOPMENT.md` 之外的新第三方依赖；确需引入先在 `pyproject.toml` 登记并说明理由。
- 每次请求必须带 `trace_id` 贯穿 JSON 结构化日志。

## 容易踩坑的规格细节（重复看重点）

- **RRF 排序按分数降序**：`sorted(fused.items(), key=lambda item: item[1], reverse=True)`，不要按元组排。
- **BGE 中文查询指令**：只对 `embed_query` 加 `query_instruction` 前缀（`embed_documents` 不加）。
- **`filters`/`collection` 必须贯穿** Retriever→VectorStore（BM25 与向量双路过滤）与整个反思循环。
- 上传走异步 Job；`/api/query` 全同步并设有全局超时；单点号注意 `max_reflection_iterations`。
- 默认嵌入模型 `BAAI/bge-small-zh-v1.5`（512 维），更换只改配置不改代码。
- 第二阶段（第 13 章）增量坑位：
  - **多知识库**：`kb_id → collection（rag_<id>）` 一一映射，上传/查询缺省 `default`；删除 `default` 必须拒绝；BM25/向量双路都要按 kb 作用域过滤。
  - **反思循环**：每轮 query 必须与上轮不同（13.3.3）；LLM 改写失败必须回退 `rule`，不得阻塞。
  - **前端**：不要手写 CORS 白名单之外的源；桌面端不得硬编码端口，探测/参数化；`frontend/dist` 之外的构建产物不提交。

## 测试要求

- 完成任何功能后必须 `make test` 全绿（293 项，2026-08-15 实测，含覆盖率门禁 ≥80%）；新模块补齐对应单测（对照 DEVELOPMENT 第 8.1 矩阵）。
- 评测回归：开发集 `tests/eval/qa_pairs/{default,docs}/` 107 条经 `app.cli.rageval` 出报告（13.1 度量协议，改检索/改写/检测等影响指标的功能必须前后对比）；
  独立基准集 `tests/eval_suite/`（120 条冻结）规范见该目录 README，不随开发周期增删。

## 提交与 GitHub（用户要求时执行）

- commit 遵循 Conventional Commits：`feat/fix/docs/refactor/test/...`，中文描述可选。
- 提交前自查：无密钥、无 `data/`、无 `.env`；`git status` 只提交目标文件。
- 用户要求推送时流程（本地已配置 SSH key）：
  1. 若项目未 `git init` 先初始化；确认远端是**空仓库**（若已有内容先 pull --rebase）。
  2. `git add <目标>` → `git commit` → `git branch -M main`。
  3. `git remote add origin git@github.com:<user>/<repo>.git`（已存在则跳过）。
  4. `git push -u origin main`；失败时先检查 SSH（`ssh -T git@github.com`）。
- 除非用户明确要求，**不要**擅自 commit/push。

## 交付要求

- 每完成一个里程碑，回看 `DEVELOPMENT.md` 对应周验收（Definition of Done）确认自检通过，再报告给用户。