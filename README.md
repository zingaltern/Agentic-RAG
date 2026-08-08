# Agentic RAG 智能知识库系统

基于 FastAPI + BGE(中文嵌入) + Chroma + BM25/RRF 混合检索 + DeepSeek(LLM) 的企业知识库问答系统，带 Agentic 反思循环与幻觉检测。

## 快速开始

```bash
make bootstrap      # 创建 .venv 并安装依赖（首次，含 torch/嵌入模型依赖，较慢）
make run            # 启动服务 → http://localhost:8000/ui
make test           # 跑全部单测（含覆盖率门禁 ≥80%）
make eval           # 评测（进程内模式，输出 reports/eval-*.md）
make demo-ingest    # 入库 demo 文档并检索验证
```

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

## 评测报告与已知限制

- `make eval` 报告输出到 `reports/eval-*.md`（`reports/` 已 gitignore，产物不入库）。
- 指标：Hit@5 / ContextPrecision / ContextRecall / AnswerRelevancy / Faithfulness / 兜底率。
- **已知限制**（如实列出，供检索/改写调优）：
  - 口语化问法（如"咋整"）可能因改写未覆盖而评估分不足 → 触发 21001 兜底；同义词表见 `configs/synonyms.csv`（缺失时用内置小表）。
  - LLM 生成的规范化总结句对幻觉检测 0.8 阈值达标率低，报告 Faithfulness 可能偏低；阈值可在 `configs/settings.yaml` 的 `detector.similarity_threshold` 调整。

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
| W4 | 测试全覆盖≥80% + 集成 + 评测 + 可选 Docker（当前） |

## 测试

```bash
make test                        # 单测 + 覆盖率门禁
.venv/bin/python -m pytest -m integration   # 集成全链路（需本机 BGE 缓存）
make eval                        # 评测报告
```
