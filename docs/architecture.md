# Architecture

## System Overview

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
│  users         │    │  ├─ [memory] Qdrant search (top_k=3)    │
│  workspaces    │    │  ├─ MockPlanner / OpenAIPlanner          │
└────────────────┘    │  └─ persist TaskModel rows              │
                      │                                          │
         ┌────────────│  execute_step task                       │
         │  Redis     │  ├─ atomic claim guard (UPDATE … rowcount)│
         │  broker    │  ├─ cancelled-job pre-flight check       │
         │  results   │  ├─ tool registry dispatch               │
         └────────────│  │   ├─ web_search (DuckDuckGo HTTP)    │
                      │  │   ├─ code_exec (subprocess/Docker)   │
                      │  │   └─ synthesis (LLM summarise)       │
                      │  ├─ dependency-chain re-enqueue          │
                      │  └─ [memory] store tool_output/job_result│
                      └──────────────────────────────────────────┘
                                          │
                             ┌────────────┴────────────┐
                             │                         │
                    ┌────────▼───────┐       ┌────────▼────────┐
                    │  Prometheus    │       │  Qdrant         │
                    │  worker :9090  │       │  vector DB      │
                    └────────┬───────┘       │  (optional)     │
                             │               └─────────────────┘
                    ┌────────▼───────┐
                    │  Grafana       │
                    │  :3000         │
                    └────────────────┘
```

---

## Request Lifecycle

```
Client
  │
  │  POST /jobs  { "prompt": "..." }
  │  Authorization: Bearer <jwt>
  ▼
FastAPI
  ├─ decode JWT → user_id
  ├─ resolve workspace (one user → one workspace)
  ├─ INSERT INTO jobs (prompt, status='pending', workspace_id)
  └─ send_task("plan_job", args=[job_id], queue="planner")
         │
         ▼
Celery Planner Worker
  ├─ UPDATE jobs SET status='planning'
  ├─ [optional] Qdrant search for past job_result entries
  ├─ LLM call → ExecutionPlan (N PlannedStep objects)
  ├─ INSERT INTO tasks (one row per step, status='pending')
  ├─ UPDATE jobs SET status='planned'
  ├─ for each step with no dependencies:
  │    UPDATE tasks SET status='queued'
  │    send_task("execute_step", args=[task_id], queue="executor")
  └─ UPDATE jobs SET status='running'
         │
         ▼ (one Celery task per ready step)
Celery Executor Worker
  ├─ atomic claim: UPDATE tasks SET status='running'
  │   WHERE id=? AND status IN ('pending','queued')  ← rowcount=0 → skip
  ├─ pre-flight: job.status == 'cancelled'? → mark skipped, return
  ├─ dispatch tool (web_search / code_exec / synthesis)
  ├─ UPDATE tasks SET status='succeeded', tool_output=...
  ├─ [optional] store tool_output to Qdrant
  ├─ find tasks whose only unsatisfied dependency was this step
  │    UPDATE tasks SET status='queued'
  │    send_task("execute_step", ...)   ← fan-out
  └─ if all tasks terminal:
       UPDATE jobs SET status='succeeded', result=...
       [optional] store job_result to Qdrant
```

---

## Component Map

```
api/
├── auth/
│   ├── utils.py          # hash_password, verify_password, create/decode_access_token
│   └── dependencies.py   # get_current_user, get_current_workspace (FastAPI Depends)
├── db/
│   ├── models.py         # UserModel, WorkspaceModel, JobModel (asyncpg)
│   └── session.py        # AsyncSession factory
├── routers/
│   ├── auth.py           # POST /auth/register, /auth/token; GET /auth/me
│   └── jobs.py           # POST /jobs, GET /jobs, cancel, task-detail
├── schemas/
│   ├── auth.py           # UserCreate, UserResponse, TokenResponse
│   └── job.py            # CreateJobRequest, JobResponse, TaskResponse, JobDetailResponse
├── metrics.py            # http_request_duration_seconds histogram
└── main.py               # FastAPI app init, middleware, /health, /ready, /metrics

worker/
├── memory/
│   ├── base.py           # MemoryEntry dataclass, MemoryStore ABC
│   ├── null_store.py     # NullMemoryStore — no-op when MEMORY_ENABLED=false
│   ├── embeddings.py     # embed() — OpenAI or deterministic mock (MD5-seeded gaussian)
│   ├── qdrant_store.py   # QdrantMemoryStore — upsert + workspace_id filter on search
│   └── factory.py        # get_memory_store() singleton; reset_memory_store() for tests
├── planner/
│   ├── base.py           # BasePlanner ABC: plan(job_id, prompt, context) → ExecutionPlan
│   ├── mock.py           # MockPlanner — deterministic plan, no LLM call
│   ├── openai_planner.py # OpenAIPlanner — json_object mode, parses into ExecutionPlan
│   └── prompt.py         # build_system_prompt, build_user_prompt (injects memory context)
├── sandbox/
│   ├── base.py           # BaseSandbox ABC
│   ├── subprocess_sandbox.py  # timeout-only execution
│   └── docker_sandbox.py      # resource-limited Docker container
├── tasks/
│   ├── planner.py        # plan_job Celery task
│   └── executor.py       # execute_step Celery task
├── tools/
│   ├── registry.py       # ToolRegistry — name → callable
│   ├── web_search.py     # DuckDuckGo search → list of result dicts
│   └── code_exec.py      # sandboxed Python execution via sandbox backend
├── db/
│   ├── models.py         # JobModel, TaskModel (psycopg2 — sync)
│   └── session.py        # get_sync_session context manager
├── metrics.py            # Worker Prometheus metrics (counters + histograms)
└── celery_app.py         # Celery app init, metrics HTTP server on WORKER_METRICS_PORT

shared/
├── models.py             # ExecutionPlan, PlannedStep, JobStatus, TaskStatus, TaskType
└── constants.py          # QUEUE_PLANNER, QUEUE_EXECUTOR, TASK_PLAN_JOB, TASK_EXECUTE_STEP

infra/
├── init.sql              # Full schema for fresh installs
├── migrate_m4.sql        # Add attempt_count, started_at, finished_at to tasks
├── migrate_m6.sql        # Add users, workspaces; add workspace_id to jobs
├── prometheus.yml        # Scrape config: api :8000/metrics, worker :9090/metrics
└── grafana/
    └── provisioning/
        ├── datasources/  # Prometheus datasource auto-provisioned
        └── dashboards/   # Agent Platform dashboard JSON
```

---

## Data Model

```
users
  id            UUID PK
  email         VARCHAR UNIQUE NOT NULL
  hashed_password VARCHAR NOT NULL
  is_active     BOOLEAN DEFAULT true
  created_at    TIMESTAMPTZ DEFAULT now()

workspaces
  id            UUID PK
  name          VARCHAR NOT NULL
  owner_id      UUID FK → users(id) ON DELETE CASCADE
  created_at    TIMESTAMPTZ DEFAULT now()

jobs
  id            UUID PK
  workspace_id  UUID FK → workspaces(id) ON DELETE CASCADE   ← all queries filter on this
  prompt        TEXT NOT NULL
  status        VARCHAR NOT NULL   -- pending|planning|planned|running|succeeded|failed|cancelled
  result        TEXT
  error         TEXT
  created_at    TIMESTAMPTZ DEFAULT now()
  updated_at    TIMESTAMPTZ DEFAULT now()

tasks
  id            UUID PK
  job_id        UUID FK → jobs(id) ON DELETE CASCADE
  step_id       VARCHAR                                       ← LLM-assigned string key
  task_type     VARCHAR NOT NULL   -- tool_call|synthesis|analysis|decision
  name          VARCHAR NOT NULL
  description   TEXT
  tool_name     VARCHAR
  tool_input    JSONB DEFAULT '{}'
  tool_output   JSONB
  dependencies  JSONB DEFAULT '[]'  -- list of step_id strings
  priority      INTEGER DEFAULT 0
  sequence      INTEGER NOT NULL
  expected_output TEXT
  status        VARCHAR NOT NULL   -- pending|queued|running|succeeded|failed|skipped
  error         TEXT
  attempt_count INTEGER DEFAULT 0
  started_at    TIMESTAMPTZ
  finished_at   TIMESTAMPTZ
  created_at    TIMESTAMPTZ DEFAULT now()
  updated_at    TIMESTAMPTZ DEFAULT now()
```

---

## Auth Flow

```
POST /auth/register
  body: { email, password, workspace_name? }
  ├─ hash_password(password)  →  bcrypt hash
  ├─ INSERT INTO users
  ├─ INSERT INTO workspaces (owner_id = user.id)
  └─ return UserResponse

POST /auth/token
  body: OAuth2 form (username=email, password)
  ├─ SELECT user WHERE email = username
  ├─ verify_password(plain, hashed)
  ├─ create_access_token(sub=user.id)  →  HS256 JWT, exp=JWT_EXPIRE_MINUTES
  └─ return { access_token, token_type: "bearer" }

Protected endpoint dependency chain:
  get_current_workspace(token, db)
    └─ get_current_user(token, db)
         ├─ decode_access_token(token)  →  user_id
         ├─ SELECT user WHERE id = user_id
         └─ SELECT workspace WHERE owner_id = user.id
```

---

## Memory Architecture

```
MEMORY_ENABLED=false (default)
  get_memory_store() → NullMemoryStore
    store()  →  pass
    search() →  []

MEMORY_ENABLED=true
  get_memory_store() → QdrantMemoryStore (lazy singleton)
    store(entry):
      vector = embed(entry.content)   ← OpenAI or mock embedder
      qdrant.upsert(collection, PointStruct(
        id=entry.id,
        vector=vector,
        payload={workspace_id, job_id, entry_type, content, metadata, created_at}
      ))

    search(workspace_id, query, top_k):
      vector = embed(query)
      qdrant.search(collection, query_vector=vector, limit=top_k,
        query_filter=Filter(must=[
          FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
        ])
      )
      → [MemoryEntry, ...]

Embedder fallback (no OPENAI_API_KEY):
  _mock_embed(text):
    seed = int(md5(text.encode()).hexdigest(), 16) % 2^32
    rng  = np.random.default_rng(seed)
    v    = rng.standard_normal(1536)
    return (v / ‖v‖).tolist()   ← deterministic, unit-norm
```

---

## Execution Hardening

The executor uses two atomic database guards to make task execution safe under at-least-once Celery delivery:

**Claim guard** (prevents duplicate execution):
```sql
UPDATE tasks
SET    status = 'running', attempt_count = attempt_count + 1, started_at = now()
WHERE  id = :task_id
AND    status IN ('pending', 'queued')
RETURNING id
```
If rowcount = 0, another worker already claimed the task. The current invocation returns immediately.

**Enqueue guard** (prevents duplicate queue entries):
```sql
UPDATE tasks
SET    status = 'queued'
WHERE  id = :task_id
AND    status = 'pending'
RETURNING id
```
Only the row that wins this update calls `send_task()`.

**Cancellation**: The executor checks `job.status == 'cancelled'` immediately after claiming the task. If cancelled, it marks the task `skipped` and returns without calling any tool. No inter-worker signalling is required.
