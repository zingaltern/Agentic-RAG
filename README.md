# Agentic RAG 智能知识库系统

基于 FastAPI + BGE(中文嵌入) + Chroma + BM25/RRF 混合检索 + DeepSeek(LLM) 的企业知识库问答系统，带 Agentic 反思循环与幻觉检测。

## 快速开始

```bash
make bootstrap      # 创建 .venv 并安装依赖（首次，含 torch/嵌入模型依赖，较慢）
make run            # 启动服务 → http://localhost:8000/ui
make test           # 跑全部单测（契约测试 + Parser/Chunker + UI 冒烟）
make demo-query     # 一轮问答冒烟（W3 起可用）
```

- 环境变量 `RAG_DEEPSEEK_API_KEY`（DeepSeek 平台获取）只走环境变量；真实 `.env` 不入库。
- 首次启动向量化相关功能会自动下载嵌入模型（约 100~400MB，仅一次联网）。
- 配置见 `configs/settings.yaml`（覆盖优先级：内置默认 < yaml < `RAG_` 环境变量），完整规格见 `DEVELOPMENT.md` 第 4 章。

## 目录速览

```
app/
├── main.py            # FastAPI 入口（装配 DI → 启动，托管 /ui）
├── api/               # health / documents / query / plugins / jobs 路由
├── core/              # domain 数据模型 / interfaces 接口契约 / config / errors / logging / registry
└── adapters/          # parser（pdf/docx/xlsx/pptx/md/txt）、chunker（父子块）等实现
web/                   # /ui 内置测试页（无前端工程）
configs/settings.yaml  # 系统配置
tests/unit/            # 单测（契约/配置/解析/分块/UI）
```

## 里程碑（DEVELOPMENT.md 第 9 章）

| 周 | 交付 |
|---|---|
| W1 | 工程骨架、接口契约、Config、Parser + Chunker、UI 骨架（当前） |
| W2 | VectorEngine + VectorStore + Retriever(RRF) + 上传 Job 流水线 |
| W3 | Orchestration 反思循环 + Detector + `/api/query` + DeepSeek |
| W4 | 测试全覆盖 + 集成 + 评测 + 可选 Docker |

## 测试

```bash
make test
# 评测（W3 后可用）：
python -m app.cli.rageval --pairs tests/eval/qa_pairs.json
```
