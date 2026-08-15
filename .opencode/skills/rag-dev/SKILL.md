---
name: rag-dev
description: 本项目的开发指南。在编写或修改 Agentic-RAG 前后端代码、运行测试、构建前端时使用。
---

# Agentic-RAG 项目开发指南

## 技术栈
- 后端：Python 3.11 + FastAPI + Chroma 向量库 + BGE 中文 Embedding
- 前端：Vue 3 + Element Plus + Pinia + Vite（构建产物由 FastAPI 静态托管）
- 桌面端：Electron（frontend/electron/），自拉后端进程

## 常用命令
- 启动后端：`export HF_ENDPOINT=https://hf-mirror.com && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- 构建前端：`cd frontend && npm run build`
- 运行测试：`cd frontend && npm run test:desktop`

## 关键约定
- 后端 API 路径统一 /api/*，附带 X-Trace-Id 请求头
- 文档索引为内存态（不落盘），重启后列表为空，向量仍留在 Chroma
- 前端改动只需 rebuild，无需重启后端（静态托管实时读取 dist）；桌面端需重开 App