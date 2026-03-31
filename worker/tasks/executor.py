"""
Executor Celery task — Milestone 3B: dependency chaining and job completion aggregation.

Flow per task:
    QUEUED → RUNNING → SUCCEEDED → dispatch newly-unblocked siblings
                     ↘ FAILED   → skip transitive dependents, fast-fail job

Key design decisions
--------------------
* Pure dependency functions (_transitive_dependents, _newly_ready_step_ids) carry
  no I/O and are independently unit-testable.
* Fast-fail policy: any task failure immediately marks the job FAILED and skips
  all pending transitive dependents so the job reaches a clean terminal state.
* Pre-flight skip: if a queued task is picked up after its job was already
  fast-failed (race condition), it skips without invoking the tool.
* _check_job_completion guards against overriding a terminal job status that was
  set by another code-path (fast-fail or a concurrent worker).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from shared.constants import QUEUE_EXECUTOR, TASK_EXECUTE_STEP
from worker.celery_app import app
from worker.db import get_sync_session
from worker.db.models import JobModel, TaskModel
from worker.tools.registry import ToolError, get_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery task entry-point
# ---------------------------------------------------------------------------

@app.task(name=TASK_EXECUTE_STEP, bind=True, max_retries=2)
def execute_step(self, task_id: str) -> dict[str, Any]:  # type: ignore[override]
    """
    Execute a single task step identified by *task_id*.

    Returns:
        {
            "task_id":        str,
            "status":         "succeeded" | "failed" | "skipped",
            "step_id":        str | None,
            "newly_enqueued": [task_id, ...]
        }
    """
    logger.info("execute_step: task_id=%s", task_id)

    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        logger.error("execute_step: invalid task_id: %s", task_id)
        return {"error": f"invalid task_id: {task_id}"}

    with get_sync_session() as session:
        task = session.get(TaskModel, tid)
        if task is None:
            logger.error("execute_step: task %s not found", task_id)
            return {"error": f"task {task_id} not found"}

        # ── Pre-flight: skip if job already terminal ──────────────────────────
        # Handles the race where a task was queued before a sibling failed.
        job = session.get(JobModel, task.job_id)
        if job and job.status in ("failed", "cancelled"):
            if task.status not in ("succeeded", "failed", "skipped"):
                task.status = "skipped"
                session.commit()
            logger.info(
                "task %s skipped — job %s is already %s", task_id, job.id, job.status
            )
            return {
                "task_id": task_id,
                "status": "skipped",
                "step_id": task.step_id,
                "newly_enqueued": [],
            }

        # ── QUEUED → RUNNING ──────────────────────────────────────────────────
        task.status = "running"
        session.commit()
        logger.debug("task %s → running (tool=%s)", task_id, task.tool_name)

        # ── Invoke the tool ───────────────────────────────────────────────────
        try:
            output = _invoke_tool(task)
        except ToolError as exc:
            return _handle_task_failure(session, task, str(exc))
        except Exception as exc:
            # Transient errors: retry up to max_retries, then fail permanently.
            if self.request.retries < self.max_retries:
                logger.warning(
                    "task %s unexpected error (%s), retrying %d/%d",
                    task_id, exc, self.request.retries + 1, self.max_retries,
                )
                raise self.retry(exc=exc, countdown=5)
            return _handle_task_failure(session, task, f"unexpected error: {exc}")

        # ── Persist output & mark SUCCEEDED ──────────────────────────────────
        task.tool_output = output
        task.status = "succeeded"
        session.commit()
        logger.info("task %s → succeeded (step_id=%s)", task_id, task.step_id)

        # ── Update job terminal state ─────────────────────────────────────────
        job_done = _check_job_completion(session, task.job_id)

        # ── Dispatch newly unblocked dependent tasks ──────────────────────────
        newly_enqueued: list[str] = []
        if not job_done:
            newly_enqueued = _enqueue_newly_ready(session, task)

    return {
        "task_id": task_id,
        "status": "succeeded",
        "step_id": task.step_id,
        "newly_enqueued": newly_enqueued,
    }


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

def _invoke_tool(task: TaskModel) -> dict[str, Any]:
    """
    Resolve and call the registered tool for this task.
    Synthesis steps do not call an external tool.
    """
    if task.task_type == "synthesis":
        return {"note": "synthesis step — aggregation not yet implemented"}

    if not task.tool_name:
        raise ToolError(
            f"task {task.id} has task_type={task.task_type!r} but no tool_name"
        )

    tool_fn = get_tool(task.tool_name)  # raises ToolError for unknown names
    tool_input: dict[str, Any] = task.tool_input or {}
    return tool_fn(**tool_input)


# ---------------------------------------------------------------------------
# Pure dependency functions  (no I/O — easily unit-testable)
# ---------------------------------------------------------------------------

def _transitive_dependents(
    all_tasks: list[TaskModel], seed_step_ids: set[str]
) -> set[str]:
    """
    BFS over the dependency graph (reversed edges).

    Returns all step_ids that transitively depend on any step in *seed_step_ids*.
    The seeds themselves are NOT included in the result.

    Example — linear chain A → B → C, seed={"A"}:
        reverse_adj = {A: {B}, B: {C}, C: {}}
        BFS from A  →  visits B, then C  →  returns {"B", "C"}

    Example — fan-in [A, B] → C, seed={"A"}:
        C depends on both A and B. We only return {"C"} — C is a dependent of A,
        but whether C can actually run depends on B too (handled by _newly_ready_step_ids).
    """
    # Build reverse adjacency: step_id → set of step_ids that list it as a dep
    reverse: dict[str, set[str]] = {t.step_id: set() for t in all_tasks if t.step_id}
    for task in all_tasks:
        if not task.step_id:
            continue
        for dep in task.dependencies or []:
            if dep in reverse:
                reverse[dep].add(task.step_id)

    visited: set[str] = set()
    queue: list[str] = [s for s in seed_step_ids if s]
    while queue:
        current = queue.pop()
        for child in reverse.get(current, set()):
            if child not in visited and child not in seed_step_ids:
                visited.add(child)
                queue.append(child)

    return visited


def _newly_ready_step_ids(
    all_tasks: list[TaskModel], succeeded_step_ids: set[str]
) -> set[str]:
    """
    Return step_ids of tasks that are PENDING and have ALL dependencies satisfied.

    Only "pending" tasks are eligible — queued/running tasks are already in flight
    and must not be double-enqueued.

    Example — A(succeeded), B(pending, deps=[A]), C(pending, deps=[A,B]):
        succeeded={"A"}  →  {"B"}   (C still waiting for B)
        succeeded={"A","B"}  →  {"C"}
    """
    return {
        t.step_id
        for t in all_tasks
        if t.status == "pending"
        and t.step_id is not None
        and set(t.dependencies or []).issubset(succeeded_step_ids)
    }


# ---------------------------------------------------------------------------
# Session-level dependency helpers
# ---------------------------------------------------------------------------

def _skip_downstream(
    session: Session, all_tasks: list[TaskModel], failed_step_id: str
) -> int:
    """
    Mark every PENDING transitive dependent of *failed_step_id* as SKIPPED.

    Queued tasks that are already in Celery are handled by the pre-flight check
    at the top of execute_step — we deliberately leave them as "queued" here
    to avoid a second DB write racing with the worker that picked them up.

    Returns the number of tasks skipped.
    """
    to_skip = _transitive_dependents(all_tasks, {failed_step_id})
    if not to_skip:
        return 0

    step_to_task = {t.step_id: t for t in all_tasks if t.step_id}
    count = 0
    for step_id in to_skip:
        task = step_to_task.get(step_id)
        if task and task.status == "pending":
            task.status = "skipped"
            count += 1
            logger.debug("skipped downstream task step_id=%s", step_id)

    if count:
        session.commit()
    return count


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------

def _handle_task_failure(
    session: Session, task: TaskModel, error_msg: str
) -> dict[str, Any]:
    """
    On task failure:
    1. Mark task FAILED.
    2. Skip all transitive PENDING dependents (clean terminal state).
    3. Fast-fail the job.
    """
    task.status = "failed"
    task.error = error_msg
    session.commit()
    logger.error("task %s → failed: %s", task.id, error_msg)

    all_tasks: list[TaskModel] = (
        session.query(TaskModel)
        .filter(TaskModel.job_id == task.job_id)
        .all()
    )

    if task.step_id:
        skipped = _skip_downstream(session, all_tasks, task.step_id)
        if skipped:
            logger.info(
                "skipped %d downstream task(s) after step '%s' failed",
                skipped, task.step_id,
            )

    # Fast-fail: mark job failed without waiting for independent branches
    job = session.get(JobModel, task.job_id)
    if job and job.status not in ("succeeded", "failed", "cancelled"):
        job.status = "failed"
        job.error = error_msg
        session.commit()
        logger.info("job %s → failed (fast-fail after '%s')", job.id, task.step_id)

    return {
        "task_id": str(task.id),
        "status": "failed",
        "step_id": task.step_id,
        "error": error_msg,
        "newly_enqueued": [],
    }


# ---------------------------------------------------------------------------
# Job completion tracking
# ---------------------------------------------------------------------------

def _check_job_completion(session: Session, job_id: uuid.UUID) -> bool:
    """
    If every task for *job_id* is in a terminal state, transition the job to
    SUCCEEDED or FAILED accordingly.

    Terminal task states: succeeded | failed | skipped.

    Returns True when the job is (or becomes) terminal — including if it was
    already terminal before this call (guards against status override).
    """
    job = session.get(JobModel, job_id)
    if job is None:
        return True

    # Respect terminal status set by another code-path (fast-fail or concurrent worker)
    if job.status in ("succeeded", "failed", "cancelled"):
        return True

    all_tasks: list[TaskModel] = (
        session.query(TaskModel).filter(TaskModel.job_id == job_id).all()
    )

    terminal = {"succeeded", "failed", "skipped"}
    statuses = {t.status for t in all_tasks}

    if not statuses.issubset(terminal):
        return False  # some tasks still running / queued / pending

    if "failed" in statuses:
        job.status = "failed"
        job.error = job.error or "One or more tasks failed"
        session.commit()
        logger.info("job %s → failed (all tasks terminal)", job_id)
    else:
        job.status = "succeeded"
        synthesis = next(
            (t for t in all_tasks if t.task_type == "synthesis"), None
        )
        if synthesis and synthesis.tool_output:
            raw = synthesis.tool_output
            job.result = raw.get("note") or str(raw)
        else:
            job.result = "All steps completed successfully"
        session.commit()
        logger.info("job %s → succeeded", job_id)

    return True


# ---------------------------------------------------------------------------
# Dependency dispatch
# ---------------------------------------------------------------------------

def _enqueue_newly_ready(session: Session, completed_task: TaskModel) -> list[str]:
    """
    After *completed_task* succeeds, find every PENDING task whose full
    dependency set is now satisfied and enqueue it to the executor queue.

    Delegates the "which tasks are ready?" decision to _newly_ready_step_ids so
    the logic is pure and independently testable.
    """
    if not completed_task.step_id:
        return []

    all_tasks: list[TaskModel] = (
        session.query(TaskModel)
        .filter(TaskModel.job_id == completed_task.job_id)
        .all()
    )

    succeeded_ids: set[str] = {
        t.step_id for t in all_tasks if t.status == "succeeded" and t.step_id
    }

    ready_step_ids = _newly_ready_step_ids(all_tasks, succeeded_ids)
    step_to_task = {t.step_id: t for t in all_tasks if t.step_id}

    enqueued: list[str] = []
    for step_id in sorted(ready_step_ids):  # sorted for deterministic ordering
        t = step_to_task[step_id]
        t.status = "queued"
        session.commit()
        app.send_task(TASK_EXECUTE_STEP, args=[str(t.id)], queue=QUEUE_EXECUTOR)
        enqueued.append(str(t.id))
        logger.debug("enqueued task %s (step_id=%s)", t.id, step_id)

    return enqueued
