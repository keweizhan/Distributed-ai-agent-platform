-- Bootstrap schema for the distributed AI agent platform.
-- Runs once on first Postgres container start.
-- M2: added planned/succeeded to job_status, queued/succeeded to task_status,
--     and step_id/dependencies/priority columns to tasks.
-- M4: added attempt_count, started_at, finished_at to tasks.
-- M6: added users, workspaces tables; workspace_id FK on jobs.

CREATE TYPE job_status AS ENUM (
    'pending',
    'planning',
    'planned',      -- plan persisted; executor dispatch imminent
    'running',
    'succeeded',
    'failed',
    'cancelled'
);

CREATE TYPE task_status AS ENUM (
    'pending',
    'queued',       -- persisted and sent to Celery, not yet picked up
    'running',
    'succeeded',
    'failed',
    'skipped'
);

CREATE TYPE task_type AS ENUM (
    'plan',         -- root planning step
    'tool_call',    -- invoke a registered tool
    'synthesis'     -- aggregate results into final answer
);

-- Auth / multi-tenancy
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(255) NOT NULL,
    owner_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_workspaces_owner_id ON workspaces(owner_id);

-- Top-level user-facing job
CREATE TABLE jobs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    prompt       TEXT NOT NULL,
    status       job_status NOT NULL DEFAULT 'pending',
    result       TEXT,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Individual steps that make up a job's execution plan
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    parent_task_id  UUID REFERENCES tasks(id),
    step_id         TEXT,                              -- LLM-assigned key, e.g. "search_papers"
    task_type       task_type NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    tool_name       TEXT,
    tool_input      JSONB,
    tool_output     JSONB,
    dependencies    JSONB NOT NULL DEFAULT '[]',       -- list of step_id strings
    priority        INTEGER NOT NULL DEFAULT 0,        -- lower = higher priority
    status          task_status NOT NULL DEFAULT 'pending',
    error           TEXT,
    sequence        INTEGER NOT NULL DEFAULT 0,        -- position in plan (0-indexed)
    expected_output TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,        -- incremented on each claim
    started_at      TIMESTAMPTZ,                       -- set when claimed by a worker
    finished_at     TIMESTAMPTZ,                       -- set on succeeded or failed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_job_id      ON tasks(job_id);
CREATE INDEX idx_tasks_status      ON tasks(status);
CREATE INDEX idx_tasks_step_id     ON tasks(job_id, step_id);
CREATE INDEX idx_jobs_status       ON jobs(status);
CREATE INDEX idx_jobs_workspace_id ON jobs(workspace_id);
CREATE INDEX idx_tasks_started_at ON tasks(started_at) WHERE started_at IS NOT NULL;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TRIGGER tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
