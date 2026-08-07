# ---- Agentic RAG Makefile ----
PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
VENV_PY := $(shell command -v python3.11 >/dev/null 2>&1 && echo python3.11 || echo python3)

# pip 镜像源：默认清华镜像（可用 make PIP_INDEX_URL=... 或环境变量覆盖）；失败时兜底官方 PyPI
PIP_INDEX_URL ?= https://pypi.tuna.tsinghua.edu.cn/simple
PIP_DEFAULT_TIMEOUT ?= 120

# HuggingFace 模型下载镜像（首次加载嵌入模型联网下载用；可用环境变量覆盖）
export HF_ENDPOINT ?= https://hf-mirror.com

.PHONY: bootstrap run test demo-query clean

## 首次：创建虚拟环境（python3.11 优先）并安装依赖（含 dev 依赖）
bootstrap:
	@echo "✅ 使用 Python: $$($(VENV_PY) --version 2>&1)（$(VENV_PY)）"
	$(VENV_PY) -m venv .venv
	$(PIP) install --upgrade pip
	@echo "ℹ️  pip 源: $(PIP_INDEX_URL)"
	$(PIP) install -e ".[dev]" || \
		(echo "⚠️  镜像安装失败，改用官方 PyPI 兜底重试…" && \
		 $(PIP) install --index-url https://pypi.org/simple -e ".[dev]")
	@echo "✅ 完成：source .venv/bin/activate 或直接用 make run"

## 启动 API（含 /ui 测试页）
run:
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

## 跑全部单测
test:
	$(PYTHON) -m pytest tests -q

## 入库 demo 文档并检索验证（W2 验收）
demo-ingest:
	$(PYTHON) -m app.cli.demo_ingest

## 一轮问答冒烟（W3 交付后可用）
demo-query:
	@echo "⚠️  demo-query 依赖 /api/query 全链路（W3 交付），当前周次暂不可用"

## 清理虚拟环境与运行时数据（谨慎）
clean:
	rm -rf .venv data reports
