# Distributed AI Agent Platform

A full-stack AI agent system that accepts natural-language prompts, decomposes them into a multi-step execution plan using an LLM, runs each step in parallel across Celery workers, and streams live progress back to a chat-style web UI. The platform is designed for production deployment — multi-tenant by default, provider-agnostic for LLMs, and observable via a pre-wired Prometheus + Grafana stack.

---

## Screenshots

> **Suggested placements:**
> 1. `docs/screenshots/chat-running.png` — chat UI mid-execution: assistant bubble showing "Running tools…" with animated step progress list
> 2. `docs/screenshots/chat-complete.png` — chat UI after completion: final answer bubble, green checkmarks on each step, "View technical details →" link
> 3. `docs/screenshots/sidebar-history.png` — collapsed/expanded sidebar showing job history with hover-delete
> 4. `docs/screenshots/grafana-dashboard.png` — Grafana: task throughput, duration histogram, queue depth

---

## Key Features

| Category | Feature |
|---|---|
| **Agent** | LLM-based task planning — prompt → validated `ExecutionPlan` with dependency graph |
| **Agent** | Parallel multi-step tool execution respecting declared dependencies |
| **Agent** | LLM synthesis step aggregates all tool outputs into a final answer |
| **Agent** | Provider-agnostic LLM (OpenAI, DeepSeek, Moonshot, OpenRouter, ZhipuAI) |
| **Tools** | Web search via Tavily (primary) with DuckDuckGo fallback |
| **Tools** | Sandboxed code execution (subprocess or Docker backend) |
| **Tools** | Pluggable tool registry — new tools added with a single decorator |
| **Backend** | Async job pipeline: plan → dispatch → execute → synthesize |
| **Backend** | Atomic task claiming — concurrent workers cannot double-execute a step |
| **Backend** | Automatic fast-fail: any task failure marks the job failed and skips dependents |
| **Backend** | At-least-once retry with configurable `max_retries` per task |
| **Auth** | JWT-based auth with per-user workspace isolation (all queries are tenant-scoped) |
| **Frontend** | Chat-style UI: live step progress, animated status indicators, friendly error copy |
| **Frontend** | Collapsible job history sidebar with per-row delete |
| **Frontend** | "View technical details" drill-down to full task tree and JSON I/O |
| **Observability** | Prometheus metrics on every task execution, tool call, and HTTP request |
| **Observability** | Pre-provisioned Grafana dashboard (no manual setup required) |
| **Memory** | Optional Qdrant-backed semantic memory — past results surfaced as planner context |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Browser  (Next.js 14 · TypeScript · Tailwind CSS)   │
│  Chat UI · Sidebar history · Job detail drill-down   │
└────────────────────┬─────────────────────────────────┘
                     │ HTTPS  (polling GET /jobs/{id})
                     ▼
┌──────────────────────────────────────────────────────┐
│  Nginx  (TLS termination · reverse proxy)            │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────┐
│  FastAPI  (REST · JWT auth · workspace scoping)      │
│  POST /jobs   GET /jobs/{id}   DELETE /jobs/{id}     │
└──────┬───────────────────────────────┬───────────────┘
       │ writes job row                │ Celery send_task
       ▼                               ▼
┌─────────────┐              ┌─────────────────────────┐
│  PostgreSQL │◄─────────────│  Redis  (broker/cache)  │
│  jobs       │              └─────────────────────────┘
│  tasks      │                         │
│  users      │              ┌──────────┴──────────┐
│  workspaces │              │                     │
└─────────────┘   ┌──────────▼──────┐  ┌──────────▼────────┐
                  │ Planner worker  │  │ Executor worker(s) │
                  │  calls LLM      │  │  invokes tools     │
                  │  writes plan    │  │  parallel tasks    │
                  └─────────────────┘  └───────────────────┘
                                                │
                               ┌────────────────┴───────────┐
                               │  Tool registry             │
                               │  web_search  (Tavily/DDG)  │
                               │  code_exec   (Docker/proc) │
                               └────────────────────────────┘
                                        │  optional
                                        ▼
                               ┌────────────────────────────┐
                               │  Qdrant  (semantic memory) │
                               └────────────────────────────┘
```

### Services

| Service | Role |
|---|---|
| `api` | FastAPI — accepts requests, stores jobs, dispatches to Celery |
| `worker` | Celery — runs planner and executor task types on separate queues |
| `postgres` | Primary datastore — jobs, tasks, users, workspaces |
| `redis` | Celery broker and result backend |
| `nginx` | TLS termination + reverse proxy (production only) |
| `prometheus` | Scrapes `/metrics` from API and worker |
| `grafana` | Pre-provisioned dashboards — no manual setup required |
| `qdrant` | Vector store for semantic memory (optional, off by default) |

---

## End-to-End Request Flow

```
1.  User types a prompt in the chat UI and presses Enter.

2.  POST /jobs → API creates a JobModel (status: pending) and
    enqueues TASK_PLAN_JOB on the planner Celery queue.

3.  Planner worker picks up the job, calls the configured LLM with a
    structured system prompt, and parses the response into a validated
    ExecutionPlan (PlannedSteps with declared dependencies).
    Tasks are written to PostgreSQL; job moves to "planned".

4.  Ready tasks (those with no unmet dependencies) are dispatched to
    the executor queue. Job moves to "running".

5.  Executor workers claim tasks atomically (WHERE-guarded UPDATE to
    prevent double-execution across concurrent workers). Each task
    invokes its registered tool, stores tool_output, marks "succeeded".

6.  After each completion, the executor re-evaluates the dependency
    graph and dispatches any newly-unblocked tasks.

7.  When all tasks are terminal, a synthesis task runs: the LLM reads
    every tool_output and writes a final coherent answer into job.result.

8.  The frontend polls GET /jobs/{id} every 2 seconds and updates the
    assistant bubble in real time:
      pending   →  "Thinking…"
      planning  →  "Planning…"
      running   →  "Running tools…" + live step progress list
      succeeded →  final answer (multi-line, whitespace-preserved)
      failed    →  friendly error message + "View technical details"
```

---

## Tech Stack

**Backend**
- Python 3.11, FastAPI + Uvicorn (async API)
- Celery 5 + Redis 7 (distributed task queue, two named queues: `planner`, `executor`)
- SQLAlchemy 2 (async ORM) + PostgreSQL 16
- Pydantic v2 (request/response validation, `ExecutionPlan` schema enforcement)
- `openai` SDK — used for any OpenAI-compatible provider
- `zhipuai` SDK — optional ZhipuAI/GLM synthesis path
- `tavily-python` + `duckduckgo-search` — layered web search (Tavily → DDG fallback)
- `prometheus_client` — per-task, per-tool, and per-endpoint metrics

**Frontend**
- Next.js 14 (App Router), TypeScript, React 18
- Tailwind CSS 3
- Polling-based live updates (no WebSocket dependency)

**Infrastructure**
- Docker Compose (dev) / Docker Compose + Nginx (prod)
- Let's Encrypt TLS via Certbot
- Grafana 10 + Prometheus 2.51 — dashboard JSON provisioned at startup
- Qdrant 1.9 — optional, activated with `MEMORY_ENABLED=true`

---

## Local Development Setup

### Prerequisites

- Docker and Docker Compose v2
- Node.js 18+ and npm (for the frontend dev server)

### 1. Clone and configure

```bash
git clone <repo-url>
cd distributed-ai-agent-platform
cp .env.example .env
```

Open `.env` and set at minimum:

```dotenv
# LLM — leave as sk-not-set to use the built-in MockPlanner (no API cost)
OPENAI_API_KEY=sk-...

# Web search — leave blank to fall back to DuckDuckGo (no key required)
TAVILY_API_KEY=tvly-...
```

The `MockPlanner` returns a deterministic test plan when no LLM key is configured — useful for exercising the execution pipeline without API costs.

### 2. Start backend services

```bash
docker compose up -d
```

PostgreSQL, Redis, the API, a Celery worker, Prometheus, Grafana, and Qdrant all start together. The database schema is applied automatically on first boot via `infra/init.sql`.

| Service | URL |
|---|---|
| API + interactive docs | http://localhost:8000/docs |
| Grafana dashboards | http://localhost:3000 |
| Prometheus | http://localhost:9091 |

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev          # → http://localhost:3001
```

### 4. Try it

1. Open http://localhost:3001, register an account, and you land on the Chat page.
2. Submit a prompt: *"Find the top 5 Python web frameworks and summarise each one."*
3. Watch the planner create tasks, then see each step complete in the live step list.
4. Click **View technical details** to inspect raw tool inputs and outputs.

### Switching LLM providers

The worker reads a single set of env vars — swap them to change provider with no code changes:

```dotenv
# DeepSeek
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# Moonshot
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.moonshot.cn/v1
OPENAI_MODEL=moonshot-v1-8k

# ZhipuAI (synthesis only; planner still uses OpenAI-compatible key above)
ZHIPU_API_KEY=...
ZHIPU_MODEL=glm-4-flash
```

---

## Deployment

A production-hardened Compose file is provided at `docker-compose.prod.yml`.

```bash
# 1. Generate strong secrets and write .env.prod
bash scripts/gen-env.sh

# 2. Provision TLS certificate (requires SERVER_NAME in .env.prod)
bash scripts/setup-server.sh

# 3. Build images and start all services
docker compose -f docker-compose.prod.yml up -d --build
```

**Key production differences:**

- Images are fully self-contained (no source bind-mounts)
- Internal services (Postgres, Redis, Qdrant, Prometheus) have no public ports
- Nginx terminates TLS on 80/443 and proxies to the API container on the internal Docker network
- Redis requires password authentication
- Grafana requires admin login; accessible via SSH tunnel only (`ssh -L 3000:localhost:3000 user@server`)
- Worker concurrency raised to 2 (tune with `--concurrency` to match CPU count)
- Log rotation configured on all containers (50 MB per file, 5 files max)

**Frontend:** build the Next.js app and deploy the output to any static host or Node server:

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=https://your-api-domain.com npm run build
npm start
```

---

## Project Structure

```
.
├── api/                    FastAPI application
│   ├── routers/            jobs.py  auth.py
│   ├── db/                 SQLAlchemy models, async session
│   └── schemas/            Pydantic request / response schemas
├── worker/                 Celery workers
│   ├── tasks/              planner.py  executor.py
│   ├── planner/            LLM planner (OpenAI-compatible) + MockPlanner
│   ├── tools/              web_search.py  code_exec.py  registry.py
│   └── memory/             Qdrant-backed semantic memory (optional)
├── shared/                 Constants and models shared by api + worker
├── frontend/               Next.js 14 application
│   └── src/app/
│       ├── chat/           Primary chat UI (page + Suspense wrapper)
│       ├── jobs/           Job detail drill-down and task tree
│       └── components/     Navbar  Sidebar
├── infra/
│   ├── init.sql            Canonical DB schema (source of truth)
│   ├── nginx/              Production reverse proxy config
│   ├── prometheus.yml      Scrape configuration
│   └── grafana/            Pre-provisioned datasource + dashboard JSON
├── docker-compose.yml      Local development
└── docker-compose.prod.yml Production (TLS, no public internal ports)
```

---

## Observability

Prometheus scrapes `/metrics` from the API and the worker. Grafana loads a pre-provisioned dashboard on first start that shows:

- Task executions per second by type (`tool_call`, `synthesis`, `plan`)
- Task duration histogram (p50 / p95 / p99)
- Per-tool call counts and latency
- Task queue wait time (time from `created_at` → worker claim)
- HTTP request latency by endpoint
- Task retry and failure rates

No manual dashboard setup is required — the JSON is provisioned via `infra/grafana/provisioning/`.

---

## Roadmap

- **WebSocket / SSE streaming** — push job status updates instead of polling; eliminate the 2-second polling lag
- **Streaming synthesis** — stream LLM synthesis tokens into the chat bubble as they arrive
- **More tools** — browser automation, file I/O, structured data queries, HTTP fetch
- **Multi-turn memory** — inject previous conversation turns into the planner prompt for coherent follow-up prompts
- **Plan visualisation** — render the dependency DAG interactively before and during execution
- **Worker auto-scaling** — KEDA or similar to scale Celery workers based on Redis queue depth
- **Evaluation harness** — automated scoring of planner output quality and end-to-end task success rate

---

## License

MIT
