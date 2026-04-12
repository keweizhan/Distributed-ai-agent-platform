# Distributed AI Agent Platform

A production-minded, distributed AI agent system with a chat-style frontend, retrieval-augmented generation (RAG), sandboxed code execution, and full-stack observability. Built to demonstrate how LLM-powered agents can be decomposed into durable, observable, horizontally-scalable microservices without reaching for a heavyweight framework.

---

## Screenshots

> Add screenshots here after first run. Suggested captures:

| Placeholder | What to capture |
|---|---|
| `docs/chat-running.png` | Chat UI mid-execution — "Running tools…" with animated step list |
| `docs/chat-complete.png` | Completed job — final answer bubble + green step checkmarks + Sources panel |
| `docs/knowledge-library.png` | Library modal — document list with "Ready / Ingesting / Failed" status badges |
| `docs/file-upload.png` | "+ Add knowledge" modal with a PDF loaded, ready to ingest |
| `docs/grafana.png` | Grafana — task duration histogram, queue-wait latency, tool call rates |

---

## Overview

The platform accepts a natural-language prompt, decomposes it into a dependency-aware execution plan, and runs each step across Celery workers — with full persistence, retries, and workspace isolation. A chat-style Next.js frontend polls job state in real time and renders each agent step as it completes.

A built-in RAG pipeline lets users upload `.txt`, `.md`, and `.pdf` files and immediately query them alongside live web search. Retrieved document chunks appear as collapsible source cards under the assistant reply. The planner automatically selects the retrieval tool when the prompt refers to uploaded documents (heuristic in mock mode; LLM-guided in OpenAI mode).

The whole stack ships with `docker compose up`. No API key is required to run the end-to-end demo — a mock planner produces a deterministic task graph when no LLM credentials are provided.

---

## Key Features

**Agent orchestration**
- Dependency-aware task graph: tasks unlock and dispatch downstream siblings the moment their dependencies succeed
- Atomic task claiming via `WHERE`-guarded `UPDATE` — two concurrent workers racing on the same task both execute safely; the second gets `rowcount=0` and exits early
- Fast-fail policy: one task failure immediately skips all transitive dependents via BFS and marks the job failed — no orphaned running steps
- Celery retry with countdown back-off for transient errors (`max_retries=2`)
- Attempt count, `started_at`, and `finished_at` tracked on every task for full auditability

**Retrieval-Augmented Generation**
- Document ingestion pipeline: chunk (500 char / 100 char overlap) → embed (`text-embedding-3-small` or mock) → upsert to Qdrant
- Deterministic `uuid5` point IDs — re-ingesting the same document is always idempotent
- Workspace-scoped vector search: every query filters by `workspace_id`; tenants never see each other's data
- Planner selects `retrieval` tool via keyword heuristic (20-phrase frozenset, mock mode) or LLM guidance (OpenAI mode)
- Source cards rendered under each assistant reply: document title, chunk reference (`§N`), 2-line text preview

**Tools**
- `web_search` — DuckDuckGo (zero-config) or Tavily with configurable result count
- `code_exec` — sandboxed Python via subprocess or Docker (`python:3.11-slim`), 30 s hard timeout
- `retrieval` — Qdrant semantic search scoped to the current workspace; all heavy imports are lazy so the tool registers without `qdrant_client` installed locally

**Knowledge base UI**
- "+ Add knowledge" modal: paste plain text or upload `.txt`, `.md`, `.pdf`
- Text/Markdown: client-side `FileReader` → populates textarea; PDF: sent to server, extracted with `pypdf`
- Library modal: lists all documents with chunk count, creation date, and status badge; delete button on hover dispatches a background Celery task that removes vectors from Qdrant

**Frontend**
- Next.js 14 App Router chat interface with 2-second job polling
- Collapsible conversation history sidebar with per-job delete
- Per-job task step expansion: tool name, status, timing, raw JSON I/O

**Auth and multi-tenancy**
- JWT (HS256) issued at login, verified on every request
- All DB queries are workspace-scoped; cross-tenant data leakage is structurally impossible

**Schema management**
- Alembic migrations are the sole schema authority; `alembic upgrade head` runs automatically on API container start
- Adding a column never requires `docker compose down -v`

**Observability**
- Prometheus metrics on every Celery task: `task_executions_total`, `task_duration_seconds`, `task_queue_delay_seconds`, `tool_calls_total`, `tool_duration_seconds`, `task_retries_total`
- Grafana dashboards provisioned from `infra/grafana/provisioning/` — no manual setup

**Optional semantic memory**
- Enable with `MEMORY_ENABLED=true`: tool outputs and job results stored to Qdrant `agent_memory` collection
- Past results retrieved and injected as context into synthesis tasks for follow-up coherence

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Browser                                 │
│                Next.js 14  ·  TypeScript  ·  Tailwind           │
│       Chat UI · History sidebar · Knowledge base modal          │
└──────────────────────────┬──────────────────────────────────────┘
                           │  REST / JSON  (Bearer JWT)
┌──────────────────────────▼──────────────────────────────────────┐
│                    FastAPI  (async, Uvicorn)                     │
│   /auth   /jobs   /documents   /documents/upload                │
│   SQLAlchemy 2 async  ·  asyncpg  ·  Pydantic v2                │
└────────┬──────────────────────────────┬─────────────────────────┘
         │  Celery send_task             │  async SQLAlchemy
         ▼                              ▼
┌────────────────┐             ┌────────────────────────────────┐
│  Redis 7       │             │      PostgreSQL 16              │
│  broker +      │             │  users · workspaces · jobs     │
│  result backend│             │  tasks · documents             │
└────────┬───────┘             │  (schema owned by Alembic)     │
         │                     └────────────────────────────────┘
         │  3 named queues
   ┌─────┴─────────────────────────────────────┐
   │              Celery Workers                │
   │                                            │
   │  planner queue                             │
   │  └─ plan_job → MockPlanner / OpenAIPlanner │
   │                                            │
   │  executor queue                            │
   │  └─ execute_step (parallel, atomic claim)  │
   │       ├─ web_search  (DDG / Tavily)        │
   │       ├─ code_exec   (subprocess / Docker) │
   │       └─ retrieval   (Qdrant vector search)│
   │                                            │
   │  celery queue (default)                    │
   │  └─ ingest_document                        │
   │  └─ delete_document                        │
   └─────────┬──────────────────────────────────┘
             │
   ┌─────────▼──────────────────────────────────┐
   │                  Qdrant                    │
   │  rag_documents   (workspace RAG vectors)   │
   │  agent_memory    (optional episodic memory)│
   └────────────────────────────────────────────┘

   Prometheus ← scrapes /metrics from API + worker
   Grafana    ← reads Prometheus, dashboards provisioned at startup
```

---

## Request Lifecycle

```
User types prompt → POST /jobs
  │
  ├─ JobModel saved  (status=pending)
  └─ plan_job dispatched to planner queue
         │
         ▼
  Planner worker
  ├─ MockPlanner  (no LLM key)  →  deterministic 2- or 3-step plan
  └─ OpenAIPlanner              →  GPT-4o-mini / any compatible model
         │
         ▼
  TaskModel rows written (status=pending)
  Root tasks (no deps) → status=queued → sent to executor queue
         │
  ┌──────▼───────────────────────────────────────┐
  │  execute_step  (executor queue, runs in ┐    │
  │  parallel across workers)              │    │
  │                                        │    │
  │  1. Atomic claim (WHERE status=queued) │    │
  │  2. Invoke tool with injected          │    │
  │       _workspace_id                    │    │
  │  3. Persist tool_output, mark SUCCEEDED│    │
  │  4. BFS: find newly-unblocked tasks    │    │
  │  5. Atomic enqueue each (WHERE pending)│    │
  └──────────────────────────────┬─────────┘    │
                                 └──────────────┘
         │  all tasks terminal
         ▼
  Synthesis task
  └─ LLM aggregates all tool_output rows + optional memory context
         │
         ▼
  job.status=succeeded · job.result=<final answer>
         │
  Frontend polls GET /jobs/:id every 2s → live step list updates
```

---

## RAG Workflow

```
User uploads file  (.txt / .md / .pdf)
  │
  ▼
POST /documents/upload  (multipart/form-data)
  ├─ PDF  → pypdf.PdfReader  server-side text extraction
  ├─ txt/md → UTF-8 decode (latin-1 fallback)
  └─ DocumentModel saved  (status=ingesting, chunk_count=0)
  │
  ▼
ingest_document Celery task  (celery queue)
  ├─ chunk_text(text, chunk_size=500, overlap=100)
  ├─ embed_texts()  →  text-embedding-3-small  or mock embeddings
  ├─ QdrantRagStore.upsert_chunks()
  │    point IDs = uuid5(NAMESPACE_URL, "{doc_id}:{i}")  ← idempotent
  │    payload   = {workspace_id, document_id, title, chunk_index, text}
  └─ DocumentModel  status=ready, chunk_count=N
  │
User prompt: "Based on my document, what does it say about X?"
  │
  ▼
Planner detects retrieval signal → generates:
  step 1: tool_name="retrieval",  tool_input={query, top_k=5}
  step 2: task_type="synthesis"   (depends on step 1)
  │
  ▼
execute_step injects _workspace_id → calls retrieval()
  └─ QdrantRagStore.search(workspace_id, query, top_k)
       filter: must=[workspace_id=current]
       returns: [{document_id, title, chunk_index, text, score}, ...]
  │
  ▼
Synthesis: LLM receives chunks + original prompt → coherent answer
  │
  ▼
Chat UI: answer + collapsible Sources panel
  (title · §chunk_index · 2-line text preview)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS |
| API | FastAPI 0.115, Uvicorn, Pydantic v2, python-jose JWT, passlib bcrypt |
| Task queue | Celery 5.4, Redis 7 (3 named queues: `planner`, `executor`, `celery`) |
| Database | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg, Alembic 1.14 |
| Vector store | Qdrant 1.9 (RAG + optional agent memory) |
| Embeddings | OpenAI `text-embedding-3-small` (or mock) |
| LLM planning | OpenAI SDK (GPT-4o-mini default, any compatible endpoint), ZhipuAI, mock |
| Web search | DuckDuckGo Search (zero-config) + Tavily (optional, higher quality) |
| PDF parsing | pypdf 4 |
| Code sandbox | subprocess (default) or Docker `python:3.11-slim`, 30 s timeout |
| Observability | Prometheus, Grafana (auto-provisioned dashboards) |
| Containers | Docker Compose (7 services) |

---

## Local Setup

### Prerequisites

- Docker + Docker Compose v2
- Node.js 18+ (for the frontend dev server)
- An OpenAI API key — **optional**; the mock planner works without one

### 1. Clone and configure

```bash
git clone <repo-url>
cd distributed-ai-agent-platform
cp .env.example .env
```

Minimal `.env` for a working zero-cost demo (all services, no API keys):

```dotenv
# Infrastructure — pre-filled for local Docker Compose
DATABASE_URL=postgresql+asyncpg://agent:agentpass@postgres:5432/agentdb
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

# LLM — leave as-is to use MockPlanner (no API cost)
OPENAI_API_KEY=sk-not-set

# Optional upgrades
# OPENAI_API_KEY=sk-...           # enables real GPT-4o-mini planner + synthesis
# TAVILY_API_KEY=tvly-...         # better web search (falls back to DuckDuckGo)
# MEMORY_ENABLED=true             # enables Qdrant episodic memory
```

### 2. Start all backend services

```bash
docker compose up --build
```

On first start this:
1. Pulls Postgres 16, Redis 7, Qdrant 1.9, Prometheus, Grafana
2. Builds the `api` and `worker` images
3. Runs `alembic upgrade head` inside the API container — creates all tables
4. Starts the Celery worker consuming `planner`, `executor`, and `celery` queues

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:3001
```

### 4. Try the demo

1. Open [http://localhost:3001](http://localhost:3001) — register with any email/password
2. **Web search job:** type `What are the top 5 Python web frameworks?` — watch the 3-step plan execute and synthesise
3. **Knowledge base:** click **+ Add knowledge**, paste some text (or upload a `.pdf`), wait for "Ready"
4. **RAG job:** type `Based on my document, what does it say about X?` — see retrieved sources appear under the answer
5. **Library:** click **Knowledge base** to list, inspect, or delete ingested documents

### Service URLs

| Service | URL |
|---|---|
| Frontend (dev) | http://localhost:3001 |
| API + Swagger UI | http://localhost:8000/docs |
| Prometheus | http://localhost:9091 |
| Grafana | http://localhost:3000 (user: `admin`, pass: `admin`) |
| Qdrant dashboard | http://localhost:6333/dashboard |

### Switching LLM providers

Set these env vars in `.env` — no code changes needed:

```dotenv
# DeepSeek / Moonshot / OpenRouter (OpenAI-compatible)
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# ZhipuAI (synthesis step only; planner uses the OpenAI-compatible key)
ZHIPU_API_KEY=...
ZHIPU_MODEL=glm-4-flash
```

---

## Database Migrations

Alembic is the sole schema authority. `infra/init.sql` is intentionally empty — it exists only as a placeholder so the Postgres container mount succeeds.

```bash
# Apply all pending migrations (also runs automatically on container start)
alembic upgrade head

# Generate a migration after editing api/db/models.py
alembic revision --autogenerate -m "add retries column to tasks"

# Preview what a migration will emit as SQL without running it
alembic upgrade head --sql

# Roll back one migration
alembic downgrade -1
```

**Existing databases created from the old `init.sql`:**
Stamp the database as already at the baseline without re-running the initial DDL:

```bash
alembic stamp 0001
```

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

| Test module | Covers |
|---|---|
| `test_executor.py` | BFS dependency graph, atomic claim logic, fast-fail propagation |
| `test_mock_planner.py` | Retrieval heuristic, plan shapes for both branches |
| `test_auth.py` | JWT issue/verify, workspace creation, duplicate email rejection |
| `test_sandbox.py` | Subprocess sandbox stdout, stderr capture, timeout enforcement |
| `test_memory.py` | Qdrant memory store store/search contract |
| `test_metrics.py` | Prometheus counter/histogram registration and increment |

---

## Deployment Notes

`api/Dockerfile.prod` builds a fully self-contained image (`--workers 2`, no source bind-mounts). For a minimal cloud deployment:

- Point `DATABASE_URL` and `REDIS_URL` at managed services (e.g. RDS, ElastiCache)
- Replace the local Qdrant container with [Qdrant Cloud](https://cloud.qdrant.io) — set `QDRANT_URL` to the cluster endpoint
- Set `SANDBOX_BACKEND=docker` and mount `/var/run/docker.sock` only if code execution is enabled; leave `subprocess` for simpler deployments
- Run at least two Celery worker replicas, each consuming all three queues — or split queues across dedicated deployments for independent scaling
- Prometheus and Grafana can be replaced with a managed observability stack; the worker exposes a standard `/metrics` endpoint on port 9090

---

## Project Structure

```
.
├── api/                      FastAPI application
│   ├── auth/                 JWT helpers, request dependencies
│   ├── db/                   SQLAlchemy models, async session factory
│   ├── migrations/           Alembic env.py + version files
│   │   └── versions/
│   │       └── 0001_initial_schema.py
│   ├── routers/              auth · jobs · documents
│   └── schemas/              Pydantic request/response models
│
├── worker/                   Celery worker
│   ├── planner/              MockPlanner · OpenAIPlanner · system prompt
│   ├── tasks/                plan_job · execute_step · ingest_document
│   ├── tools/                web_search · code_exec · retrieval · registry
│   ├── rag/                  chunker · QdrantRagStore
│   ├── memory/               QdrantMemoryStore · NullStore · factory
│   └── sandbox/              SubprocessSandbox · DockerSandbox · factory
│
├── shared/                   ExecutionPlan, PlannedStep, task name constants
│
├── frontend/
│   └── src/app/
│       ├── chat/             Main chat page — jobs, tasks, knowledge base
│       ├── login/
│       └── register/
│
├── infra/
│   ├── init.sql              Empty placeholder (Alembic owns the schema)
│   ├── prometheus.yml        Scrape config
│   └── grafana/              Auto-provisioned datasource + dashboard JSON
│
├── tests/                    pytest suite
├── alembic.ini
└── docker-compose.yml
```

---

## Observability

Prometheus scrapes `/metrics` from the API and worker. Grafana loads a pre-provisioned dashboard on first start:

- Task executions per second, by type (`tool_call`, `synthesis`, `plan`)
- Task duration histogram (p50 / p95 / p99)
- Per-tool call counts and latency
- Queue wait time (task `created_at` → worker claim)
- Task retry and failure rates

No manual dashboard setup — JSON is provisioned via `infra/grafana/provisioning/`.

---

## Roadmap

| Priority | Item |
|---|---|
| High | **SSE / WebSocket streaming** — push job status instead of 2-second polling; stream LLM tokens into the answer bubble as they arrive |
| High | **Persistent conversation threads** — link jobs into a thread so the planner sees prior turns; eliminates the need to re-explain context |
| Medium | **Plan inspection UI** — show the generated task graph before execution; let users edit or reject it |
| Medium | **Document metadata + filters** — tag ingested documents; filter retrieval by tag, date range, or source |
| Medium | **Re-ranking** — add a cross-encoder pass after initial vector retrieval for higher-precision RAG |
| Low | **Custom tool registration UI** — define new tools (API calls, DB queries) from the frontend without code changes |
| Low | **Worker autoscaling** — KEDA HPA targeting Redis queue depth |
| Low | **Evaluation harness** — automated scoring of plan quality and end-to-end task success rate |

---

## License

MIT

---

---

## Supplementary: GitHub Description, Resume Bullets, Demo Script

### GitHub repo description (one line)

> Distributed AI agent platform — LLM task planning, parallel Celery execution, RAG over uploaded documents, JWT multi-tenancy, Alembic migrations, Prometheus/Grafana — runs with `docker compose up`.

---

### Resume bullets

> These are written for a software engineering role. Adjust the leading verb and metrics to match your actual measurements if you have them.

**1. Systems / backend focus**
> Built a distributed AI agent platform in Python (FastAPI, Celery 5, PostgreSQL, Redis) where natural-language prompts are decomposed into dependency-aware task graphs and executed in parallel across workers; implemented atomic task claiming via WHERE-guarded UPDATE statements to prevent double-execution under concurrent consumers, with fast-fail propagation using BFS over the dependency graph.

**2. AI / ML engineering focus**
> Designed and shipped an end-to-end RAG pipeline — document chunking, OpenAI embedding, Qdrant vector upsert with deterministic UUIDs for idempotent re-ingestion, workspace-scoped semantic search — integrated with a multi-step agent planner that selects retrieval vs. web search based on prompt signals; results rendered as sourced citations in the chat UI.

**3. Full-stack / product focus**
> Delivered a full-stack AI chat product (Next.js 14, FastAPI, Celery) featuring real-time task step progress, a knowledge base UI supporting txt/md/pdf upload, JWT multi-tenancy, Alembic database migrations, and a pre-wired Prometheus + Grafana observability stack — all runnable locally with `docker compose up` and no API key required.

---

### 1-minute demo script

> Use this when screen-sharing with an interviewer or recording a walkthrough video.

---

**[0:00 – 0:10] — Open and orient**

*"This is a distributed AI agent platform I built from scratch. The frontend is Next.js, the backend is FastAPI, and the agents run on Celery workers backed by Postgres and Redis. Let me show you the end-to-end flow."*

**[0:10 – 0:25] — Web search job**

Open the chat page. Type: `What are the three most popular Python web frameworks and a one-line description of each?`

*"I'll submit this prompt. The backend creates a job, a planner worker generates a task graph — in this case three steps: web search, code analysis, synthesis — and the executor picks them up in parallel."*

Watch the step list animate. Point to each step as it turns green.

*"Each step runs in a separate Celery task. The dependency graph ensures the synthesis step only fires once both upstream steps succeed."*

**[0:25 – 0:40] — Knowledge base + RAG**

Click **+ Add knowledge**. Paste two paragraphs of text (or upload a PDF). Click **Ingest**.

*"I've just uploaded a document into the workspace's knowledge base. It gets chunked, embedded with OpenAI, and stored in Qdrant — scoped to my workspace so no other tenant can query it."*

Wait for the "Ready" badge to appear in the library. Then type: `Based on my document, what does it say about X?`

*"The planner detects this is a retrieval query and routes it to the retrieval tool. The agent searches the vector store, retrieves the relevant chunks, and synthesises an answer."*

Point to the **Sources** panel under the reply.

*"These source cards show exactly which chunks were retrieved — title, chunk index, and a preview. Full traceability from answer back to source text."*

**[0:40 – 0:55] — Observability**

Switch to the Grafana tab.

*"Every task execution emits Prometheus metrics — duration, queue wait time, per-tool call counts. This dashboard is provisioned automatically; there's nothing to configure."*

**[0:55 – 1:00] — Close**

*"The whole stack runs with `docker compose up`, including migrations via Alembic so schema changes never require wiping the database. Happy to go deeper on any part — the dependency graph logic, the RAG pipeline, or the multi-tenancy model."*
