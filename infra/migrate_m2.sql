-- M2 migration — run this against an existing M1 database.
-- If starting fresh, this is NOT needed; init.sql already includes these changes.
--
-- Usage:
--   docker compose exec postgres psql -U agent -d agentdb -f /docker-entrypoint-initdb.d/migrate_m2.sql
-- Or from host:
--   psql $DATABASE_URL -f infra/migrate_m2.sql

-- Extend enums (Postgres allows adding values but not removing them)
ALTER TYPE job_status  ADD VALUE IF NOT EXISTS 'planned';
ALTER TYPE job_status  ADD VALUE IF NOT EXISTS 'succeeded';
ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'queued';
ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'succeeded';

-- New task columns
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS step_id         TEXT,
    ADD COLUMN IF NOT EXISTS dependencies    JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS priority        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS expected_output TEXT;

CREATE INDEX IF NOT EXISTS idx_tasks_step_id ON tasks(job_id, step_id);
