# Distributed AI Agent Platform

A production-oriented distributed system that accepts natural language requests, generates structured execution plans via LLM, and dispatches tasks asynchronously across worker nodes with tool invocation and sandboxed code execution.

## Architecture

```
User → FastAPI → PostgreSQL + Redis → Celery Workers → LLM Planner → Executor Workers → Tools
```

| Component | Technology | Role |
|-----------|-----------|------|
| API Server | FastAPI + asyncpg | Accept requests, persist jobs, enqueue work |
| Task Queue | Celery + Redis | Async task dispatch and routing |
| Planner Worker | Celery + OpenAI SDK | LLM → structured ExecutionPlan |
| Executor Worker | Celery | Run task steps via tool registry |
| Tool: web_search | HTTP (Milestone 3) | Retrieve web results |
| Tool: code_exec | Docker sandbox | Execute Python safely with timeout |
| State Store | PostgreSQL | Jobs, tasks, tool I/O, audit trail |
| Memory Layer | Qdrant (Milestone 6) | Semantic retrieval across past runs |

## Milestones

- [x] **M1** — Scaffold: Docker Compose, DB schema, API skeleton (`POST /jobs`, `GET /jobs/:id`)
- [x] **M2** — Planner: LLM integration, ExecutionPlan generation, task persistence, mock fallback
- [ ] **M3** — Executor: Tool registry wiring, web_search integration, dependency-chain dispatch
- [ ] **M4** — Code sandbox: Docker-in-Docker execution with timeout + stdout capture
- [ ] **M5** — Streaming: SSE status updates, `GET /jobs/:id/stream`
- [ ] **M6** — Memory: Qdrant embedding store for cross-run context retrieval

---

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

API at `http://localhost:8000` — interactive docs at `http://localhost:8000/docs`

### Run in mock mode (no API key required)

Leave `OPENAI_API_KEY=sk-not-set` in `.env`. The MockPlanner kicks in automatically
and returns a deterministic 3-step plan (search → analyse → synthesise).

```bash
# Submit a job
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Research the latest transformer architectures"}' \
  | jq .

# Poll status (copy id from above)
curl -s http://localhost:8000/jobs/<job-id> | jq .
```

The job will transition: `pending → planning → planned → running`
Each task row will appear with `status: queued` then `status: succeeded` (stub executor).

### Run with a real LLM

```bash
# In .env:
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-4o-mini          # or gpt-4o, or any OpenAI-compatible model
OPENAI_BASE_URL=https://api.openai.com/v1
```

The OpenAIPlanner uses `response_format: json_object` to guarantee valid JSON output,
then validates the response with Pydantic before persisting any rows.

To use a local model (Ollama, vLLM, etc.), point `OPENAI_BASE_URL` at your endpoint
and set `OPENAI_MODEL` to the model name — no other changes required.

---

## Running Tests

Tests are pure unit tests — no running Docker needed.

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Set PYTHONPATH so shared/ and worker/ are importable
export PYTHONPATH=$(pwd)

# Run all tests
pytest

# Run a specific file
pytest tests/test_mock_planner.py -v
```

---

## Job & Task Status Lifecycle

```
Job:   pending → planning → planned → running → succeeded / failed
Task:  pending → queued   → running → succeeded / failed
```

- `planned` — ExecutionPlan persisted to DB; executor dispatch imminent
- `queued` — TaskModel row exists and the Celery message has been sent
- `succeeded` — terminal success (distinct from failed for monitoring clarity)

---

## Project Structure

```
.
├── api/                    # FastAPI service
│   ├── db/                 # SQLAlchemy ORM models + async session
│   ├── routers/            # Route handlers
│   └── schemas/            # Pydantic request/response shapes
├── worker/                 # Celery worker service
│   ├── planner/            # BasePlanner, MockPlanner, OpenAIPlanner, factory
│   ├── tasks/              # plan_job and execute_step Celery tasks
│   ├── tools/              # Tool registry + stubs (web_search, code_exec)
│   └── db/                 # Sync SQLAlchemy for Celery tasks
├── shared/                 # Domain models + constants (used by both services)
├── tests/                  # Unit tests (no Docker required)
├── infra/
│   ├── init.sql            # Postgres schema bootstrap (fresh install)
│   └── migrate_m2.sql      # Incremental migration (existing M1 DB)
├── requirements-dev.txt    # Test/dev dependencies
└── docker-compose.yml
```

---

## Design Decisions

**Why MockPlanner instead of a hardcoded stub?**
The mock implements the same `BasePlanner` interface as `OpenAIPlanner`. This means
all downstream code (persistence, enqueue logic, tests) exercises the real path.
Switching from mock to real LLM is a single config change.

**Why `json_object` mode instead of OpenAI Structured Outputs?**
`json_object` mode is available on all current OpenAI models. Structured Outputs
(JSON Schema mode) is only available on `gpt-4o-2024-08-06+` and requires registering
a schema. We get the same guarantee (valid JSON) + Pydantic validation, with broader
model compatibility.

**Why keep `step_id` as a string rather than using the DB UUID?**
The LLM assigns `step_id` values like `"search_papers"` before rows exist in DB.
Dependencies reference these string keys at plan time. After persistence, executor
chain dispatch uses the DB UUID for task lookup. Both identifiers serve different
phases of the pipeline.

**Why two separate ORM model files (api/db and worker/db)?**
The API uses `asyncpg` (async driver) and the worker uses `psycopg2` (sync, required
by Celery). Keeping them separate avoids import coupling. `infra/init.sql` is the
single source of truth for schema.

**Why `task_acks_late=True`?**
Tasks are acknowledged after completion, not on receipt. If a worker crashes
mid-execution, the task re-queues. Combined with idempotent task design, this gives
at-least-once delivery semantics — appropriate for agentic workloads.

**Migrating an existing M1 database:**
```bash
docker compose exec postgres psql -U agent -d agentdb < infra/migrate_m2.sql
```
For a clean reset: `docker compose down -v && docker compose up --build`
