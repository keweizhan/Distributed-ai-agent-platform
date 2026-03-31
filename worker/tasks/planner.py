"""
Planner Celery task.

Flow:
  PENDING → PLANNING  (job picked up)
          → PLANNED   (ExecutionPlan persisted as TaskModel rows)
          → RUNNING   (ready tasks enqueued to executor)
          → FAILED    (any unhandled error)
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from shared.models import ExecutionPlan, PlannedStep
from worker.celery_app import app
from worker.db import get_sync_session
from worker.db.models import JobModel, TaskModel
from worker.planner import PlannerError, get_planner
from shared.constants import QUEUE_EXECUTOR, TASK_EXECUTE_STEP

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task entry point
# ---------------------------------------------------------------------------

@app.task(name="worker.tasks.planner.plan_job", bind=True, max_retries=2)
def plan_job(self, job_id: str) -> dict:
    """
    1. Load job, transition PENDING → PLANNING
    2. Call planner (mock or OpenAI)
    3. Persist TaskModel rows, transition → PLANNED
    4. Enqueue dependency-free tasks, transition → RUNNING
    """
    logger.info("Planning job %s", job_id)
    jid = uuid.UUID(job_id)

    with get_sync_session() as session:
        job = _load_job(session, jid)
        if job is None:
            return {"error": "job not found"}

        _set_job_status(session, job, "planning")

    # Run planning outside the DB session — LLM call can be slow
    try:
        planner = get_planner()
        plan = planner.plan(jid, job.prompt)
    except PlannerError as exc:
        logger.error("Planning failed for job %s: %s", job_id, exc)
        with get_sync_session() as session:
            job = _load_job(session, jid)
            if job:
                job.status = "failed"
                job.error = str(exc)
                session.commit()
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error planning job %s", job_id)
        with get_sync_session() as session:
            job = _load_job(session, jid)
            if job:
                job.status = "failed"
                job.error = f"Unexpected planner error: {exc}"
                session.commit()
        raise self.retry(exc=exc, countdown=10)

    with get_sync_session() as session:
        job = _load_job(session, jid)
        if job is None:
            return {"error": "job not found after planning"}

        task_rows = _persist_plan(session, plan)
        _set_job_status(session, job, "planned")

        # Enqueue tasks whose dependencies are already satisfied (empty deps = ready now)
        ready = plan.ready_steps(completed_step_ids=set())
        enqueued_ids = _enqueue_ready_tasks(session, task_rows, ready)

        _set_job_status(session, job, "running")

    logger.info(
        "Job %s: plan has %d steps, %d enqueued immediately",
        job_id, len(plan.steps), len(enqueued_ids),
    )
    return {
        "job_id": job_id,
        "total_steps": len(plan.steps),
        "enqueued": enqueued_ids,
    }


# ---------------------------------------------------------------------------
# Helpers — kept as named functions so they're unit-testable
# ---------------------------------------------------------------------------

def _load_job(session: Session, job_id: uuid.UUID) -> JobModel | None:
    job = session.get(JobModel, job_id)
    if job is None:
        logger.error("Job %s not found in DB", job_id)
    return job


def _set_job_status(session: Session, job: JobModel, status: str) -> None:
    job.status = status
    session.commit()
    logger.debug("Job %s → %s", job.id, status)


def persist_plan(session: Session, plan: ExecutionPlan) -> list[TaskModel]:
    """
    Public helper: write TaskModel rows for every step in the plan.
    Exposed for unit testing without running a Celery task.
    """
    return _persist_plan(session, plan)


def _persist_plan(session: Session, plan: ExecutionPlan) -> list[TaskModel]:
    """Create one TaskModel per PlannedStep, all with status=pending."""
    rows: list[TaskModel] = []
    for step in plan.steps:
        row = TaskModel(
            job_id=plan.job_id,
            step_id=step.step_id,
            task_type=step.task_type.value,
            name=step.name,
            description=step.description,
            tool_name=step.tool_name,
            tool_input=step.tool_input or {},
            dependencies=step.dependencies,
            priority=step.priority,
            sequence=step.sequence,
            expected_output=step.expected_output or None,
            status="pending",
        )
        session.add(row)
        rows.append(row)

    session.flush()   # assign DB ids without committing
    session.commit()
    return rows


def _enqueue_ready_tasks(
    session: Session,
    task_rows: list[TaskModel],
    ready_steps: list[PlannedStep],
) -> list[str]:
    """
    Mark ready tasks as queued and send them to the executor queue.
    Returns list of task UUIDs that were enqueued.
    """
    ready_step_ids = {s.step_id for s in ready_steps}
    step_id_to_row = {row.step_id: row for row in task_rows if row.step_id}

    enqueued: list[str] = []
    for step_id in ready_step_ids:
        row = step_id_to_row.get(step_id)
        if row is None:
            logger.warning("Ready step '%s' has no matching TaskModel row", step_id)
            continue

        row.status = "queued"
        session.commit()

        app.send_task(
            TASK_EXECUTE_STEP,
            args=[str(row.id)],
            queue=QUEUE_EXECUTOR,
        )
        enqueued.append(str(row.id))
        logger.debug("Enqueued task %s (step_id=%s)", row.id, step_id)

    return enqueued
