-- Bootstrap schema for the distributed AI agent platform.
-- Runs once on first Postgres container start.
-- M2: added planned/succeeded to job_status, queued/succeeded to task_status,
--     and step_id/dependencies/priority columns to tasks.

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

-- Top-level user-facing job
CREATE TABLE jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt      TEXT NOT NULL,
    status      job_status NOT NULL DEFAULT 'pending',
    result      TEXT,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_job_id   ON tasks(job_id);
CREATE INDEX idx_tasks_status   ON tasks(status);
CREATE INDEX idx_tasks_step_id  ON tasks(job_id, step_id);
CREATE INDEX idx_jobs_status    ON jobs(status);

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
