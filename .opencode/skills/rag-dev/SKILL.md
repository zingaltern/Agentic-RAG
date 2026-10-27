---
name: rag-dev
description: 本项目的开发指南。在编写或修改 Agentic-RAG 前后端代码、运行测试、构建前端时使用。
---

# Agentic-RAG 项目开发指南

## 技术栈
- 后端：Python 3.10~3.12（推荐 3.11）+ FastAPI + Chroma + BGE 中文 Embedding（`bge-small-zh-v1.5`）
- 前端：Vue 3 + Element Plus + Pinia + Vite（构建产物 `frontend/dist` 由 FastAPI 静态托管）
- 桌面端：Electron（`frontend/electron/`），自动拉起后端进程

## 常用命令（项目根目录）
- 跑测试：`make test`（基线 877 项（≥850），含覆盖率门禁 ≥80%，默认过滤 integration）
- 评测：`make eval`（验证集 197 条 → `reports/eval-*.md`）；`make review`（外部基准 CMRC 2018）；`make perf`（性能基准）
- 构建前端：`cd frontend && npm run build`
- 启动后端：`make run` → http://127.0.0.1:8000（SPA 与 `/ui` 均由此托管）
- 桌面壳测试：`cd frontend && npm run test:desktop`

## 关键约定
- 先读 `AGENTS.md`（工程规范）与 `docs/README.md`（文档索引与归档约定）；规格唯一来源是 `DEVELOPMENT.md`，冲突时以其为准。
- 配置一律走 `app/core/config.py` 的 `SystemConfig`（禁止散落 `os.getenv`）；错误统一用 `core/errors.py`；日志带 `trace_id`。
- 后端 API 路径统一 `/api/*`，统一响应包 `{code,message,data}`。
- 文档索引已持久化（`data/documents.json`，启动可从 Chroma 兜底重建）；多知识库按 `kb_id → rag_<kb_id>` collection 隔离。
- 涉及检索/改写/检测/兜底等功能改动必须按 13.1 度量协议跑 `make eval` 前后对比并留报告。
- 文档引用按文件名（`docs/specs/`、`docs/dev-tasks/`；已交付批次在 `docs/dev-tasks/archive/`）。
