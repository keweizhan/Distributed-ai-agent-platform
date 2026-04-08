# Distributed AI Agent Platform

A production-oriented distributed system that accepts natural language requests, generates structured execution plans via LLM, and dispatches tasks asynchronously across worker nodes with tool invocation, sandboxed code execution, and full Prometheus observability.

---

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
|---------|-----|
| API + Swagger UI | http://localhost:8000/docs |
| Prometheus | http://localhost:9091 |
| Grafana | http://localhost:3000 (no login required) |
| Qdrant UI | http://localhost:6333/dashboard (when `MEMORY_ENABLED=true`) |

The **Agent Platform** Grafana dashboard is pre-loaded under the Agent Platform folder.

No OpenAI key required — the built-in `MockPlanner` exercises the full execution path without any API calls.

---

## Minimal Server Deploy (HTTP, no domain required)

For a first Tencent Cloud / any Ubuntu VM demo without a domain name or TLS certificate.
Open **port 8000** inbound in your cloud security group, then:

```bash
# 1. Install Docker (one-liner, Ubuntu 22.04)
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER && newgrp docker

# 2. Clone and configure
git clone <your-repo-url> ~/app && cd ~/app
cp .env.example .env

# Edit two lines in .env (minimum required for any public server):
#   JWT_SECRET_KEY=<run: python3 -c "import secrets; print(secrets.token_hex(32))">
#   POSTGRES_PASSWORD=<any strong password>
# Then update DATABASE_URL to match the new POSTGRES_PASSWORD:
#   DATABASE_URL=postgresql+asyncpg://agent:<password>@postgres:5432/agentdb
nano .env   # or vim / sed

# 3. Start only the four core services (skip prometheus/grafana/qdrant for now)
docker compose up -d --build postgres redis api worker

# 4. Validate
curl http://<server-ip>:8000/health          # → {"status":"ok"}
curl http://<server-ip>:8000/ready           # → {"status":"ready","db":"ok"}

# 5. Register + get token + submit a job
curl -s -X POST http://<server-ip>:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"demopassword"}' | jq .

TOKEN=$(curl -s -X POST http://<server-ip>:8000/auth/token \
  -d "username=demo@example.com&password=demopassword" | jq -r .access_token)

JOB=$(curl -s -X POST http://<server-ip>:8000/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"search for the latest Python release"}' | jq -r .id)

# 6. Poll until succeeded
watch -n 3 "curl -s -H 'Authorization: Bearer $TOKEN' \
  http://<server-ip>:8000/jobs/$JOB | jq '{status,result}'"
```

Swagger UI is available at `http://<server-ip>:8000/docs`.

When you have a domain name, follow [docs/deployment.md](docs/deployment.md) for the full nginx + TLS + production hardening path.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Client                                                         │
│  POST /auth/register → POST /auth/token → Bearer JWT           │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTPS
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI  (api/)                                                │
│  ├─ /auth  — register, token, me                               │
│  ├─ /jobs  — submit, list, detail, cancel, task-detail         │
│  ├─ /health, /ready, /metrics                                  │
│  └─ workspace-scoped: every query includes WHERE workspace_id  │
└────────┬──────────────────────┬──────────────────────────────────┘
         │ write job row        │ enqueue plan_job
         ▼                      ▼
┌────────────────┐    ┌──────────────────────────────────────────┐
│  PostgreSQL    │    │  Celery Worker  (worker/)                │
│  jobs          │◄───│                                          │
│  tasks         │    │  plan_job task                           │
│  users         │    │  ├─ [M7] Qdrant memory search           │
│  workspaces    │    │  ├─ MockPlanner / OpenAIPlanner          │
└────────────────┘    │  └─ persist TaskModel rows              │
                      │                                          │
         ┌────────────│  execute_step task                       │
         │  Redis     │  ├─ atomic claim guard                   │
         │  broker    │  ├─ tool registry dispatch               │
         │  results   │  │   ├─ web_search (DuckDuckGo)         │
         └────────────│  │   ├─ code_exec (subprocess/Docker)   │
                      │  │   └─ synthesis (LLM summarise)       │
                      │  └─ [M7] store tool_output / job_result │
                      └──────────────────────────────────────────┘
                                          │
                             ┌────────────┴────────────┐
                             │                         │
                    ┌────────▼───────┐       ┌────────▼────────┐
                    │  Prometheus    │       │  Qdrant         │
                    │  :9090 scrape  │       │  vector memory  │
                    └────────┬───────┘       │  (optional)     │
                             │               └─────────────────┘
                    ┌────────▼───────┐
                    │  Grafana       │
                    │  dashboards    │
                    └────────────────┘
```

| Component | Technology | Role |
|-----------|-----------|------|
| API Server | FastAPI + asyncpg | Accept requests, persist jobs, enqueue work |
| Task Queue | Celery + Redis | Async task dispatch and routing |
| Planner | Celery + OpenAI SDK | LLM → structured ExecutionPlan (MockPlanner fallback) |
| Executor | Celery | Run task steps via tool registry |
| Tool: web_search | DuckDuckGo HTTP | Retrieve web results |
| Tool: code_exec | Subprocess / Docker | Execute Python safely with resource limits |
| State Store | PostgreSQL | Jobs, tasks, tool I/O, audit trail |
| Auth | JWT (HS256) + bcrypt | User/Workspace models, workspace-scoped isolation |
| Memory | Qdrant + embeddings | Semantic retrieval of past results (optional) |
| Metrics | prometheus_client | Counters + histograms for tasks, tools, HTTP |
| Dashboards | Grafana + Prometheus | Pre-configured agent-platform dashboard |

---

## Authentication

Every `/jobs` endpoint requires a Bearer JWT.

```
POST /auth/register  →  creates User + Workspace, returns user info
POST /auth/token     →  email + password → JWT (HS256, configurable TTL)
GET  /auth/me        →  current user (token required)
```

Token claims: `sub=user_id`. On each request the API resolves the user's workspace and scopes every DB query by `workspace_id`. A request for a job in another workspace returns **404** — indistinguishable from a missing resource (no information leakage).

```bash
# 1. Register (creates a workspace automatically)
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@example.com", "password": "devpassword"}' | jq .

# 2. Get a token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d "username=dev@example.com&password=devpassword" | jq -r .access_token)

# 3. All job requests use the token
curl -s http://localhost:8000/auth/me \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Swagger UI: click **Authorize 🔒** at http://localhost:8000/docs → paste the token value into the **Value** field (no `Bearer` prefix — Swagger adds it automatically) → click **Authorize** → **Close**.

---

## End-to-End Demo

### 1. Register and get a token

```bash
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@example.com", "password": "devpassword"}' | jq .

TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -d "username=dev@example.com&password=devpassword" | jq -r .access_token)
```

### 2. Submit a job

```bash
JOB=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt": "Research the latest advances in transformer architectures and summarise the key findings"}' \
  | jq -r .id)
echo "Job ID: $JOB"
```

### 3. Watch it execute

```bash
watch -n 2 "curl -s -H 'Authorization: Bearer $TOKEN' http://localhost:8000/jobs/$JOB \
  | jq '{status, result}'"
```

Typical progression (MockPlanner — no API key required):

```
pending → planning → planned → running
  task web_search:   queued → running → succeeded
  task analyse:      queued → running → succeeded
  task synthesise:   queued → running → succeeded
→ succeeded
```

### 4. Inspect a task's tool output

```bash
TASK_ID=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/jobs/$JOB \
  | jq -r '.tasks[] | select(.tool_name=="web_search") | .id')

curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/jobs/$JOB/tasks/$TASK_ID \
  | jq '{step_id, status, tool_name, tool_input, tool_output, attempt_count, started_at, finished_at}'
```

### 5. Cancel a running job

```bash
NEW_JOB=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt": "A long-running task"}' | jq -r .id)

curl -s -X POST http://localhost:8000/jobs/$NEW_JOB/cancel \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 6. View metrics in Grafana

Open http://localhost:3000 → **Agent Platform** dashboard.

Key panels: Tasks Succeeded/Failed · Task Execution Rate · Tool Duration p50/p95 · Queue-Wait Delay p95 · HTTP Request Duration p95.

---

## Memory Layer

Optional Qdrant-backed semantic memory, disabled by default.

```
Job succeeds         → store job_result  → Qdrant (scoped by workspace_id)
Task tool_call ends  → store tool_output → Qdrant

Planning:   search(workspace_id, job.prompt, top_k=3)       → inject job_result context into LLM prompt
Synthesis:  search(workspace_id, task.description, top_k=3) → inject into synthesis output
```

When `MEMORY_ENABLED=false` (default), all calls go to `NullMemoryStore` — pure no-op, zero Qdrant dependency.

### What gets stored

| Entry type | When | Content |
|-----------|------|---------|
| `tool_output` | After any `tool_call` task succeeds | `Tool: <name>\nTask: <name>\nOutput: <truncated>` |
| `job_result` | After job transitions to `succeeded` | `Prompt: <prompt>\nResult: <result>` |

### Enabling memory

```bash
# In .env:
MEMORY_ENABLED=true
QDRANT_URL=http://qdrant:6333
# Optional — leave as sk-not-set for the deterministic mock embedder (no API cost):
OPENAI_API_KEY=sk-not-set
```

Workspace isolation: every stored entry carries `workspace_id` in the Qdrant payload. Every search applies a server-side `must` filter — a tenant can never read another tenant's entries.

---

## API Reference

### Auth (no token required)

```
POST   /auth/register    Create user + workspace → { id, email, created_at }
POST   /auth/token       email+password → { access_token, token_type }
GET    /auth/me          Current user info (token required)
```

### Jobs (Bearer token required — workspace-scoped)

```
POST   /jobs                         Submit a new job
GET    /jobs                         List 50 most recent jobs (this workspace)
GET    /jobs/{id}                    Job detail + all tasks
POST   /jobs/{id}/cancel             Cancel a running job
GET    /jobs/{id}/tasks/{task_id}    Full task detail (inputs, outputs, timestamps)
```

### System

```
GET    /health    Liveness probe
GET    /ready     Readiness probe (checks DB connection)
GET    /metrics   Prometheus scrape endpoint
```

### Status transitions

```
Job:   pending → planning → planned → running → succeeded / failed / cancelled
Task:  pending → queued   → running → succeeded / failed / skipped
```

A cancelled job causes all in-flight and pending tasks to be skipped via the executor's pre-flight check.

---

## Observability

| Metric | Type | Labels |
|--------|------|--------|
| `agent_jobs_created_total` | Counter | — |
| `agent_jobs_cancelled_total` | Counter | — |
| `agent_job_plans_total` | Counter | status |
| `agent_task_executions_total` | Counter | task_type, status |
| `agent_task_duration_seconds` | Histogram | task_type |
| `agent_task_queue_delay_seconds` | Histogram | — |
| `agent_task_retries_total` | Counter | task_type |
| `agent_tool_calls_total` | Counter | tool_name, status |
| `agent_tool_duration_seconds` | Histogram | tool_name |
| `agent_http_request_duration_seconds` | Histogram | method, path |

**Single-process note:** The worker runs `--concurrency=1` so all metrics are captured in one process and visible at `:9090`. For higher concurrency in production, set `PROMETHEUS_MULTIPROC_DIR` (prometheus_client multiprocess mode) and increase concurrency.

---

## Sandbox Execution

`code_exec` runs user-supplied Python through a pluggable sandbox backend.

| Backend | Isolation | Use case |
|---------|-----------|---------|
| `subprocess` (default) | Timeout only | Dev / CI |
| `docker` | CPU + memory + network + read-only fs | Production |

```bash
# .env
SANDBOX_BACKEND=docker
SANDBOX_IMAGE=python:3.11-slim
SANDBOX_TIMEOUT_SECONDS=30
```

Docker safety boundaries: network disabled, 128 MiB memory limit, 50% CPU quota, read-only workspace volume, container force-removed after every run.

---

## Execution Hardening

| Guard | Mechanism |
|-------|-----------|
| Duplicate execution | `UPDATE tasks SET status='running' WHERE id=? AND status IN ('pending','queued')` — rowcount=0 → skip |
| Duplicate enqueue | Same atomic UPDATE `pending` → `queued` before `send_task()` |
| Terminal task guard | Pre-check: task already in succeeded/failed/skipped → return immediately |
| Terminal job guard | `_check_job_completion` never overwrites a terminal status |
| Audit trail | `attempt_count`, `started_at`, `finished_at` on every task row |

---

## Running Tests

```bash
pip install -r requirements-dev.txt
export PYTHONPATH=$(pwd)
pytest                              # all 186 tests (no running Docker required)
pytest tests/test_memory.py -v      # memory layer: store, search, embeddings, isolation
pytest tests/test_auth.py -v        # JWT auth + workspace isolation
pytest tests/test_api.py -v         # API endpoint tests
pytest tests/test_metrics.py -v     # Prometheus metrics
pytest tests/test_executor.py -v    # executor + hardening
pytest tests/test_sandbox.py -v     # sandbox abstraction
```

---

## Project Structure

```
.
├── api/
│   ├── auth/               # JWT utils, FastAPI dependencies (get_current_workspace)
│   ├── db/                 # SQLAlchemy ORM models + async session
│   ├── routers/
│   │   ├── auth.py         # POST /auth/register, /auth/token, GET /auth/me
│   │   └── jobs.py         # POST /jobs, GET /jobs, cancel, task detail (workspace-scoped)
│   ├── schemas/
│   │   ├── auth.py         # UserCreate, UserResponse, TokenResponse
│   │   └── job.py          # CreateJobRequest, JobResponse, TaskResponse
│   ├── metrics.py          # API Prometheus metrics
│   └── main.py             # App setup, /health, /ready, /metrics
├── worker/
│   ├── memory/             # Qdrant-backed memory layer (NullMemoryStore when disabled)
│   ├── planner/            # BasePlanner, MockPlanner, OpenAIPlanner
│   ├── sandbox/            # BaseSandbox, DockerSandbox, SubprocessSandbox
│   ├── tasks/              # plan_job and execute_step Celery tasks
│   ├── tools/              # web_search, code_exec, registry
│   ├── db/                 # Sync SQLAlchemy for Celery tasks
│   ├── metrics.py          # Worker Prometheus metrics
│   └── celery_app.py       # Celery config + metrics server startup
├── shared/                 # Domain models + constants
├── tests/                  # 186 unit tests (no running Docker required)
├── infra/
│   ├── init.sql            # PostgreSQL schema (fresh install)
│   ├── migrate_m4.sql      # M3 → M4 migration (execution audit fields)
│   ├── migrate_m6.sql      # M5 → M6 migration (users, workspaces, workspace_id)
│   ├── prometheus.yml      # Prometheus scrape config
│   └── grafana/            # Grafana provisioning (datasource + dashboard)
├── requirements-dev.txt
└── docker-compose.yml
```

---

## Design Decisions

**Why MockPlanner?** Same interface as OpenAIPlanner — all downstream code exercises the real path. Switching to a live LLM is a single env-var change.

**Why `json_object` mode over OpenAI Structured Outputs?** Available on all current OpenAI models; broader compatibility than JSON Schema mode.

**Why `step_id` as a string?** LLM assigns step IDs before DB rows exist. Dependencies use string keys at plan time; executor uses DB UUIDs for task lookup.

**Why two ORM files?** API uses asyncpg; worker uses psycopg2. `infra/init.sql` is the single schema source of truth.

**Why `task_acks_late=True`?** At-least-once delivery — tasks re-queue on worker crash. The atomic claim guard makes re-delivery safe.

**Why `--concurrency=1` in docker-compose?** Keeps all metrics in one process for the demo setup. Increase + set `PROMETHEUS_MULTIPROC_DIR` for production scale.

---

## Migrating an Existing Database

```bash
# M3 → M4 (attempt_count, started_at, finished_at)
docker compose exec postgres psql -U agent -d agentdb < infra/migrate_m4.sql

# M5 → M6 (users, workspaces, workspace_id on jobs)
docker compose exec postgres psql -U agent -d agentdb < infra/migrate_m6.sql

# Clean reset (drops all data):
docker compose down -v && docker compose up --build
```
