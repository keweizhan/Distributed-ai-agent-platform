-- Migration: Milestone 4 — execution hardening
-- Adds attempt_count, started_at, finished_at to tasks table.
--
-- Run against an existing M3 database:
--   docker compose exec postgres psql -U agent -d agentdb < infra/migrate_m4.sql

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS started_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS finished_at   TIMESTAMPTZ;

-- Useful index for monitoring slow / stuck tasks
CREATE INDEX IF NOT EXISTS idx_tasks_started_at ON tasks (started_at)
    WHERE started_at IS NOT NULL;
