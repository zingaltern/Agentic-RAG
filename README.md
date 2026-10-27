<p align="center">
  <img src="./frontend/resources/icon-1024.png" width="240" alt="Agentic RAG application icon">
</p>

<h1 align="center">Agentic RAG</h1>

<p align="center">Self-hosted knowledge-base Q&amp;A that retrieves, self-corrects, optionally plans, and cites evidence instead of guessing.</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/zingaltern/Agentic-RAG/actions/workflows/ci.yml"><img src="https://github.com/zingaltern/Agentic-RAG/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <img src="https://img.shields.io/badge/Python-3.10--3.12-3776AB?style=flat-square" alt="Python 3.10 to 3.12">
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-4B5563?style=flat-square" alt="FastAPI 0.110 or newer">
  <img src="https://img.shields.io/badge/Chroma-0.5%2B-4B5563?style=flat-square" alt="Chroma 0.5 or newer">
  <img src="https://img.shields.io/badge/DeepSeek-LLM-4B5563?style=flat-square" alt="DeepSeek LLM">
  <img src="https://img.shields.io/badge/Vue-3.5-3776AB?style=flat-square" alt="Vue 3.5">
  <img src="https://img.shields.io/badge/Electron-43-4B5563?style=flat-square" alt="Electron 43">
  <img src="https://img.shields.io/badge/Docker-ready-0891B2?style=flat-square" alt="Docker support">
</p>

Most RAG samples stop at "embed, retrieve, answer". This project keeps going: it re-queries when the first pass is weak, ships an optional agentic mode that plans before it retrieves, verifies the answer against the evidence it actually used, and scores itself against a 197-pair development validation set plus a separate 120-question frozen benchmark. Documents, embeddings, and indexes stay on your machine. With web search disabled — the server default (`web.enabled=false`) — the answering path calls only the DeepSeek API, apart from the one-time model download; if you turn on the per-request **web** toggle (requires a Tavily key), the question text is also sent to Tavily.

## Highlights

| Highlight | Why it matters |
|---|---|
| Hybrid retrieval: BGE Chinese embeddings plus BM25, fused with RRF | Exact terms and paraphrases both surface; no single retriever decides the answer. |
| Two answering modes: iterative reflect loop and optional agentic plan-and-tools (off by default) | Start with the cheaper loop, escalate to planning when a question needs decomposition. |
| Evidence-bound answers | Each knowledge answer carries sources, citation markers, and a hallucination check; greeting and insufficient-evidence fallbacks bypass the detector and return a fallback code instead of a guess. |
| One Chroma collection per knowledge base | Uploads, queries, and deletes are scoped server-side, so libraries cannot leak into each other. |
| 46 recognized file extensions | PDF, Office, Markdown, HTML, email, CSV/TSV, JSON, plus code formats with AST symbol extraction where a tree-sitter grammar exists (config formats fall back to plain text). `.msg` is recognized only to return a clear `.eml` hint. |
| Evaluation is part of the repository | A 197-pair development validation set, a 160-pair external review set, and a throughput/latency benchmark all run from `make`; a 120-question frozen benchmark runs through `tests/eval_suite/runner.py` against a running service. |

## Architecture

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

Uploads run as background jobs: parse, optionally clean, chunk into parent/child blocks, embed with `BAAI/bge-small-zh-v1.5`, and write into the knowledge base's Chroma collection. Queries fan out to the vector index and an in-memory BM25 index, fuse the two rankings with RRF, and hand the context to either the reflect loop or the agentic planner. Every generated knowledge answer passes through the hallucination detector before it is returned; greeting and insufficient-evidence fallbacks bypass it.

## Usage Example

Start the service and ask a question against the built-in `default` knowledge base:

```bash
curl -s http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "What chunking strategy does the ingestion pipeline use?", "kb_id": "default"}'
```

The call returns HTTP 200 with the common envelope `{code, message, data}`. On success `code` is `0`, and `data` carries the answer plus the evidence trail:

| Field | Contents |
|---|---|
| `answer` | Generated answer text; citation markers such as `[1]` point into `sources`. |
| `sources` | The retrieved chunks used as context, each with its document metadata. |
| `citations` | Which citation markers appeared in the answer, and the share the detector confirmed. |
| `hallucination_check` | `passed` or `suspected`. |
| `iterations` / `elapsed_ms` | How many retrieval rounds ran, and the server-side duration. |
| `mode` / `agent_steps` / `termination_reason` | Which answering path ran and why it stopped. |

Business errors also return HTTP 200 with the cause in `code`: `30001` means the LLM is unavailable, either because no key is configured or because the call failed; `21001` means no supporting evidence was found; `40101` means the knowledge base is not readable by the caller.

## Quick Install

Requires Python 3.10 to 3.12, `make`, and Git. The first install pulls PyTorch and the embedding stack, so expect a large download.

```bash
git clone https://github.com/zingaltern/Agentic-RAG.git
cd Agentic-RAG
make bootstrap
```

`make bootstrap` creates `.venv` and installs the project with development dependencies. It uses the Tsinghua PyPI mirror by default and falls back to the official index if that fails; override with `make bootstrap PIP_INDEX_URL=<url>`.

## Quick Start

```bash
cp .env.example .env                 # then set RAG_DEEPSEEK_API_KEY in .env
make run                             # serves the API and SPA on http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` for the web console; the SPA is served from the committed `frontend/dist`. Then confirm the components are alive:

```bash
curl -s http://127.0.0.1:8000/api/health
```

The response reports `status` plus a `checks` map for `embedding`, `vector_store`, and `llm`. By default this is an assembly probe: `llm: ok` only means the provider is wired, not that the API key works. Use `GET /api/health?probe=1` for a minimal real LLM call (cached for 60s) before treating `llm` as actually reachable in external monitoring. The embedding model (`BAAI/bge-small-zh-v1.5`, downloaded to the Hugging Face cache on first use) may briefly read `not_loaded` while it warms up in the background. Without `RAG_DEEPSEEK_API_KEY` the service still starts, `llm` stays `not_loaded`, and questions return code `30001`.

Upload a document and wait for the ingestion job:

```bash
curl -s -F "file=@./README.md" -F "kb_id=default" http://127.0.0.1:8000/api/documents/upload
curl -s http://127.0.0.1:8000/api/jobs/<job_id>
```

The upload responds with a `job_id`; poll it until `status` is no longer `pending` or `running`. Once it settles, the Usage Example above returns an answer over that document.

If the machine has no outbound network and the embedding model is already cached, prefix commands with `HF_HUB_OFFLINE=1`; otherwise the loader may stall while checking for adapter configuration.

## Common commands

| Command | What it does |
|---|---|
| `make bootstrap` | Create `.venv` and install runtime plus development dependencies. |
| `make run` | Start the API and mount the built SPA on `http://127.0.0.1:8000`. |
| `make test` | Run the unit suite with the 80 percent coverage gate. |
| `make eval` | Run the 197-pair validation set and write `reports/eval-*.md`. |
| `make review` | Run the 160-pair CMRC 2018 external set against an isolated `cmrc` knowledge base. |
| `make perf` | Benchmark ingestion throughput, query latency percentiles, and Hit@5 at 50, 150, and 300 documents. |
| `make integration` | Run the end-to-end tests against the local model cache. |
| `make demo-ingest` | Ingest the demo documents and verify retrieval. |
| `make rebuild KB=<id>` | Rebuild one knowledge base from archived originals. |
| `make frontend-install` / `make frontend-dev` | Install frontend dependencies and serve the SPA on `http://localhost:5173`. |
| `make frontend-build` | Build the SPA into `frontend/dist`. |
| `make desktop` | Launch the Electron shell, which starts and supervises the backend. |
| `make docker-up` / `make docker-down` | Run the API container via Docker Compose. |

## API

All responses use `{code, message, data}`. The SPA is mounted at `/` when `frontend/dist` exists; `/ui` remains as a legacy smoke-test page.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Component health for embedding, vector store, and LLM. |
| POST | `/api/query` | Ask a question; accepts `query`, `kb_id`, `mode`, `top_k`, `filters`, and `web_search`. |
| POST | `/api/documents/upload` | Upload and ingest; accepts `file`, `kb_id`, `mode` (`add` or `upsert`), and `force`. |
| GET | `/api/documents` | List documents in a knowledge base. |
| GET / DELETE | `/api/documents/{document_id}` | Read metadata or delete a document and its vectors. |
| POST | `/api/documents/batch-delete` | Delete up to 200 documents in one call. |
| GET | `/api/jobs/{job_id}` | Poll an ingestion job. |
| GET / POST | `/api/kbs` | List knowledge bases or create one. |
| DELETE | `/api/kbs/{kb_id}` | Delete a knowledge base and its vectors. |
| GET / POST | `/api/plugins/status`, `/api/plugins/switch` | Inspect or hot-switch providers. |
| POST | `/api/auth/login` | Sign in and receive a bearer token. |
| GET | `/api/auth/me`, POST `/api/auth/change-password` | Read the current user or change a password. |
| GET / POST | `/api/users` | List or create users; admin only. |
| POST | `/api/users/import` | Bulk-create users from CSV. |
| POST | `/api/users/{user_id}/reset-password`, `/role` | Reset a password or change a role. |
| DELETE | `/api/users/{user_id}` | Delete a user. |
| GET | `/api/metrics` | Expose in-process counters. |

`mode` accepts `reflect`, `agentic`, or `auto`. `auto` routes simple questions to the reflect loop and escalates complex ones to the agent, and it only runs when `orchestration.agent.enabled` is on.

## Configuration

Configuration resolves in order: built-in defaults, then `configs/settings.yaml`, then `RAG_`-prefixed environment variables. Business code reads settings through `app/core/config.py`; keep secrets in the environment.

| Variable | Purpose |
|---|---|
| `RAG_DEEPSEEK_API_KEY` | DeepSeek API key. Required for generated answers. |
| `RAG_ADMIN_USER` / `RAG_ADMIN_PASSWORD` | Bootstrap the first admin when team mode is enabled. |
| `HF_ENDPOINT` | Hugging Face mirror for model downloads; defaults to `https://hf-mirror.com` in the Makefile. |
| `HF_HUB_OFFLINE` | Set to `1` to keep model loading fully offline. |

The API listens on `127.0.0.1:8000` by default. To expose it, run `uvicorn app.main:app --host 0.0.0.0` or change `api.host` in the settings file.

## Security and access control

- Personal mode is the default: `api.auth_enabled` is `false`, there is no login, and every operation is available.
- Team mode adds password login, scrypt-hashed credentials, and knowledge-base-scoped permissions. Shared libraries are readable by everyone and writable by admins; private libraries are visible to their owner and to admins. The built-in `default` library cannot be deleted.
- Repeated login failures lock the source IP for five minutes. Query and upload endpoints are rate-limited per IP, and concurrent queries are capped by a semaphore.
- `trace_id` runs through JSON logs from request to answer. Audit records hold the user, knowledge base, and answer summary, never passwords or tokens.
- Parsing validates magic numbers and guards against archive bombs. Prompt-injection scanning, content moderation, and PII masking are all implemented but off by default; when injection scanning is enabled, its default mode audits and flags rather than blocks.

## Evaluation and benchmarks

Reports are written to `reports/`, which is excluded from version control.

- `make eval` runs 197 question-answer pairs across the `default`, `docs`, and `complex` sets and reports Hit@1, Hit@5, context precision and recall, answer relevancy, faithfulness by category, and fallback rate.
- `make review` runs the CMRC 2018 sample as an external set against its own `cmrc` library, which keeps it physically separate from the validation set.
- `make perf` measures ingestion throughput, query latency percentiles, and the Hit@5 curve at 50, 150, and 300 documents.
- `make eval-quality` runs a retrieval smoke test over a scripted fixture set.

## Known limitations

- The default answering path is a retrieval-side self-correction loop. The agentic mode has planner, tool-call, and evidence-ledger basics in place but is off by default, and its dedicated evaluation is not finished.
- Faithfulness is near `0` in in-process runs (0–3% in the latest full run) because the stub LLM does not match the retrieved context; real DeepSeek runs score far higher. Treat in-process numbers as retrieval signals, not answer quality.
- Colloquial phrasings can still fall back with `21001` when query rewriting does not cover them. The synonym table lives in `configs/synonyms.csv`.
- `.doc` and `.ppt` require LibreOffice on the host; without it the upload fails with an explicit installation hint. `.msg` is not parsed and the API asks for `.eml` instead. Image OCR is an interface without an engine.
- The cleaner layer (`cleaner.enabled`) is off by default. Turning it on changes ingestion output and requires a rebuild of affected libraries.

## Project layout

```text
app/
├── main.py            # FastAPI entry point
├── application.py     # Middleware, dependency injection, SPA mounting
├── api/               # HTTP routes: auth, users, kbs, documents, jobs, query, health, metrics, plugins
├── core/              # Domain models, interface contracts, config, errors, logging, rate limiting
├── adapters/          # Embedding, vector store, retriever, reranker, parser, chunker, cleaner, LLM, agent
├── services/          # Orchestration, agent runtime, ingestion, job and document stores, RBAC
└── cli/               # Demo ingestion, evaluation, review set builder, perf benchmark
web/                   # Legacy /ui smoke-test page
frontend/              # Vue 3 SPA and Electron shell
configs/settings.yaml  # System configuration
docs/                  # Documentation index, specs, and task briefs
tests/                 # unit, integration, evaluation, review, and frozen benchmark sets
```

## Documentation

| Topic | What it covers | Link |
|---|---|---|
| Using the product | Screens, upload flow, and what each setting does | [PRODUCT_MANUAL.md](./PRODUCT_MANUAL.md) |
| Specification | Interface contracts, data models, algorithms, and acceptance criteria | [DEVELOPMENT.md](./DEVELOPMENT.md) |
| Documentation index | How `docs/` is organized and where new documents go | [docs/README.md](./docs/README.md) |
| Agentic design | Planner, tool calls, evidence ledger, and the roadmap | [docs/specs/真Agentic_RAG设计.md](./docs/specs/真Agentic_RAG设计.md) |
| Validation set | How `make eval` scores answers | [tests/eval/README.md](./tests/eval/README.md) |
| External set | How the CMRC review set is built and run | [tests/review/README.md](./tests/review/README.md) |
| Frozen benchmark | The 120-question frozen set and its rules | [tests/eval_suite/README.md](./tests/eval_suite/README.md) |
| Contributor rules | Engineering conventions for development agents | [AGENTS.md](./AGENTS.md) |

## Status

Phases 1 and 2 are delivered: the retrieval loop, multi-knowledge-base isolation, the Vue 3 SPA, the Electron shell, and team-mode authentication. Current work is hardening — desktop packaging decisions and the agentic evaluation track.
