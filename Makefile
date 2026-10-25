# ---- Agentic RAG Makefile ----
# 解释器：优先 make bootstrap 生成的 .venv；CI 等未建 venv 的环境（pip 装到系统解释器）
# 回退到 PATH 上的 python3，可用 make test PYTHON=... 显式覆盖。
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
PIP    := .venv/bin/pip
VENV_PY := $(shell command -v python3.11 >/dev/null 2>&1 && echo python3.11 || echo python3)

# pip 镜像源：默认清华镜像（可用 make PIP_INDEX_URL=... 或环境变量覆盖）；失败时兜底官方 PyPI
PIP_INDEX_URL ?= https://pypi.tuna.tsinghua.edu.cn/simple
PIP_DEFAULT_TIMEOUT ?= 120

# HuggingFace 模型下载镜像（首次加载嵌入模型联网下载用；可用环境变量覆盖）
export HF_ENDPOINT ?= https://hf-mirror.com
# 无外网环境（模型已在本地缓存）务必加 HF_HUB_OFFLINE=1：
#   HF_HUB_OFFLINE=1 make eval / make perf / make run
# 否则 sentence-transformers 加载缓存模型时仍会联网查 PEFT adapter 配置，卡在 socket 连接上（表现为"评测无输出"）。
export HF_HUB_OFFLINE ?= 0

.PHONY: bootstrap run test eval eval-quality review perf coverage integration demo-ingest demo-query rebuild docker-up docker-down clean frontend-install frontend-test

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

## 启动 API（S2：默认 127.0.0.1 本机监听；暴露需显式 --host 0.0.0.0）
run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

## 跑全部单测（含覆盖率门禁 ≥80%，默认过滤 integration）
test:
	$(PYTHON) -m pytest tests -m "not integration" --cov=app --cov-report=term-missing -q

## 仅覆盖率报告（不设失败门禁）
coverage:
	$(PYTHON) -m pytest tests -m "not integration" --cov=app --cov-report=term-missing -q --cov-fail-under=0

## 评测（8.3/13.6）：全库（default+docs）进程内评测，输出 reports/eval-*.md（含按库汇总）
eval:
	$(PYTHON) -m app.cli.rageval --in-process --all-kbs

## 质量评测 smoke（P0-P2，设计稿 docs/specs/测试与评测体系设计.md）：IR 指标 + 可选 judge
## 用法：make eval-quality（不调 LLM）；JUDGE=1 加 judge；REAL=1 真实生成；LIMIT=N 试点条数
## 默认 --fresh-kb：每次清空 quality_smoke，保证 fixture/分块变更后可复现
eval-quality:
	$(PYTHON) -m app.cli.eval_quality --pairs tests/quality/datasets/smoke_quality.jsonl \
		--docs tests/quality/fixtures --kb quality_smoke --fresh-kb \
		--out reports/eval-quality-$$(date +%Y%m%d-%H%M%S).md \
		$(if $(filter 1,$(JUDGE)),--judge,) $(if $(filter 1,$(REAL)),--real-llm,) \
		$(if $(LIMIT),--limit $(LIMIT),)

## 外部基准测试集评测（8.4）：CMRC 2018 → 独立 kb cmrc，--fresh-kb 全新入库可复现。
## 与验证集（make eval，tests/eval/）物理分离；报告 reports/review-*.md。
## 基线对比：make review BASELINE=reports/review-<基线>.md
review:
	$(PYTHON) -m app.cli.rageval --in-process --fresh-kb --kb cmrc \
		--pairs tests/review/qa_pairs/cmrc/qa_pairs.jsonl \
		--docs tests/review/sample_data/cmrc \
		--out reports/review-$$(date +%Y%m%d-%H%M%S).md \
		$(if $(BASELINE),--baseline $(BASELINE),)

## 性能基准（8.5.2）：规模点 50/150/300 文档（进程内真实 BGE+Chroma，Stub LLM），测入库吞吐、
## 查询延迟分位与 Hit@5 规模曲线 → reports/perf-*.md。基线对比：make perf BASELINE=reports/perf-<基线>.md
perf:
	$(PYTHON) -m app.cli.perfbench --scales 50,150,300 --queries 30 \
		--out reports/perf-$$(date +%Y%m%d-%H%M%S).md \
		$(if $(BASELINE),--baseline $(BASELINE),)

## 集成测试（8.2）：需要本机 BGE 模型缓存（不联网下载）
integration:
	$(PYTHON) -m pytest tests/integration -m integration -q

## 入库 demo 文档并检索验证（W2 验收）
demo-ingest:
	$(PYTHON) -m app.cli.demo_ingest

## 索引全量重建（P1-3）：make rebuild KB=<kb_id>（需 P0-2 归档原件；重建期间暂停该库上传/查询）
rebuild:
	@test -n "$(KB)" || (echo "用法: make rebuild KB=<kb_id>"; exit 1)
	$(PYTHON) -m app.cli.rebuild --kb $(KB) --yes

## 一轮问答冒烟（W3 交付后可用）
demo-query:
	@echo "⚠️  demo-query 依赖 /api/query 全链路（W3 交付），当前周次暂不可用"

## Docker（可选）：构建并启动 api 容器（含 /ui；嵌入模型首次启动在容器内下载，需 HF 镜像兜底）
docker-up:
	docker compose up --build -d api
	@echo "ℹ️  健康检查：curl http://localhost:8000/api/health"

docker-down:
	docker compose down

## ---- 前端（W7，Vue3+Vite）----
## 安装前端依赖（首次）
frontend-install:
	cd frontend && npm install

## 前端单测（P1，零新增依赖：node --test）
frontend-test:
	cd frontend && npm run test:unit

## 启动 vite dev server（proxy /api → 127.0.0.1:8000，需先 make run）
frontend-dev:
	cd frontend && npm run dev

## 构建 SPA 产物 → frontend/dist（由 FastAPI 同源托管，生产无需 CORS）
frontend-build:
	cd frontend && npm run build

## 桌面端（W8，Electron）：内置拉起后端三步走（启动/就绪/退出）
## 依赖：.venv（make bootstrap）与 frontend 依赖（make frontend-install）；首次需 make frontend-build
desktop:
	cd frontend && npm run desktop

## 清理虚拟环境与运行时数据（谨慎）
clean:
	rm -rf .venv data reports
