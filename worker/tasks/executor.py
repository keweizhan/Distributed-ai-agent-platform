"""
Executor Celery task — Milestones 4 + 5: safe sandboxed execution, hardening, metrics.

M7: Memory layer hooks added (all fire-and-forget, never block or fail a task):
  - After a tool_call task succeeds: store tool output to memory.
  - Before a synthesis task runs: retrieve relevant past results as context.
  - After a job succeeds: store the final job result to memory.

Flow per task:
    QUEUED → RUNNING → SUCCEEDED → dispatch newly-unblocked siblings
                     ↘ FAILED   → skip transitive dependents, fast-fail job

Key design decisions
--------------------
* Atomic claim (_claim_task): uses a WHERE-guarded UPDATE so two Celery
  workers racing on the same task_id will both succeed without data
  corruption — the second worker simply gets rowcount=0 and returns early.
* Duplicate enqueue guard (_enqueue_ready_task): same atomic UPDATE pattern
  prevents two workers completing independent branches from both enqueuing
  the same newly-ready task.
* Terminal state guards: pre-flight checks for both job and task ensure we
  never overwrite a terminal status set by another concurrent code-path.
* attempt_count / started_at / finished_at: tracked on every claim and
  completion so execution history is auditable without consulting Celery logs.
* Prometheus metrics: task_executions_total, task_duration_seconds,
  task_queue_delay_seconds, tool_calls_total, tool_duration_seconds.
* Pure dependency functions (_transitive_dependents, _newly_ready_step_ids)
  carry no I/O and are independently unit-testable.
* Fast-fail policy: any task failure immediately marks the job FAILED and
  skips all pending transitive dependents so the job reaches a clean terminal
  state without waiting for independent branches.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

import worker.metrics as metrics
from shared.constants import QUEUE_EXECUTOR, TASK_EXECUTE_STEP
from worker.celery_app import app
from worker.config import settings
from worker.db import get_sync_session
from worker.db.models import JobModel, TaskModel
from worker.memory import MemoryEntry, get_memory_store
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
    logger.info("execute_step started", extra={"task_id": task_id})

    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        logger.error("execute_step: invalid task_id=%s", task_id)
        return {"error": f"invalid task_id: {task_id}"}

    with get_sync_session() as session:
        task = session.get(TaskModel, tid)
        if task is None:
            logger.error("execute_step: task not found", extra={"task_id": task_id})
            return {"error": f"task {task_id} not found"}

        # ── Extract scalar fields while the session is active ─────────────────
        # SQLAlchemy's expire_on_commit=True expires all ORM attributes after
        # every session.commit().  Reading an expired attribute outside an open
        # session raises DetachedInstanceError.  Capturing plain Python locals
        # here means every subsequent read is a dict/str/uuid lookup — no ORM
        # lazy-load is ever needed after this point.
        task_step_id: str | None = task.step_id
        task_job_id: uuid.UUID = task.job_id
        task_type: str = task.task_type
        task_tool_name: str | None = task.tool_name
        task_status: str = task.status

        # ── Guard: task already in a terminal state ───────────────────────────
        # Handles at-least-once redelivery — Celery may redeliver a task after
        # a worker crash even though a previous worker already completed it.
        if task_status in ("succeeded", "failed", "skipped"):
            logger.info(
                "execute_step: task already terminal, skipping",
                extra={"task_id": task_id, "status": task_status},
            )
            return {
                "task_id": task_id,
                "status": task_status,
                "step_id": task_step_id,
                "newly_enqueued": [],
            }

        # ── Pre-flight: skip if job already terminal ──────────────────────────
        # Handles the race where a task was queued before a sibling failed.
        job = session.get(JobModel, task_job_id)
        if job and job.status in ("failed", "cancelled"):
            if task_status not in ("succeeded", "failed", "skipped"):
                task.status = "skipped"
                session.commit()
                metrics.task_executions_total.labels(
                    task_type=task_type, status="skipped"
                ).inc()
            logger.info(
                "execute_step: task skipped — job already terminal",
                extra={
                    "task_id": task_id,
                    "job_id": str(task_job_id),
                    "job_status": job.status,
                },
            )
            return {
                "task_id": task_id,
                "status": "skipped",
                "step_id": task_step_id,
                "newly_enqueued": [],
            }

        # Capture workspace_id while we have the job loaded — used for memory.
        workspace_id: str | None = str(job.workspace_id) if (job and job.workspace_id) else None

        # ── Atomic claim: PENDING/QUEUED → RUNNING ────────────────────────────
        # If two workers race on the same task_id (e.g. after a broker-level
        # redelivery), only one claim succeeds; the other returns early here.
        if not _claim_task(session, task):
            logger.info(
                "execute_step: task already claimed by another worker",
                extra={"task_id": task_id},
            )
            return {
                "task_id": task_id,
                "status": "skipped",
                "step_id": task_step_id,
                "newly_enqueued": [],
            }

        logger.info(
            "execute_step: task claimed",
            extra={
                "task_id": task_id,
                "step_id": task_step_id,
                "tool_name": task_tool_name,
                "attempt": task.attempt_count,  # updated by _claim_task
            },
        )

        # ── Record queue-wait delay (created_at → claimed now) ────────────────
        if (
            task.started_at and task.created_at
            and isinstance(task.started_at, datetime)
            and isinstance(task.created_at, datetime)
        ):
            delay = (task.started_at - task.created_at).total_seconds()
            metrics.task_queue_delay_seconds.observe(max(delay, 0))

        task_start = time.monotonic()

        # ── M7: retrieve memory context for synthesis tasks ───────────────────
        memory_context: list[str] = []
        if task_type == "synthesis" and workspace_id and job:
            memory_context = _retrieve_memory_context(workspace_id, task, job)

        # ── Invoke the tool ───────────────────────────────────────────────────
        tool_name = task_tool_name or "synthesis"
        tool_start = time.monotonic()
        try:
            output = _invoke_tool(
                task,
                memory_context=memory_context,
                session=session,
                workspace_id=workspace_id,
            )
        except ToolError as exc:
            metrics.tool_calls_total.labels(tool_name=tool_name, status="failed").inc()
            metrics.task_executions_total.labels(
                task_type=task_type, status="failed"
            ).inc()
            return _handle_task_failure(session, task, str(exc))
        except Exception as exc:
            # Transient errors: retry up to max_retries, then fail permanently.
            if self.request.retries < self.max_retries:
                metrics.task_retries_total.labels(task_type=task_type).inc()
                logger.warning(
                    "execute_step: transient error, retrying",
                    extra={
                        "task_id": task_id,
                        "error": str(exc),
                        "attempt": self.request.retries + 1,
                        "max_retries": self.max_retries,
                    },
                )
                raise self.retry(exc=exc, countdown=5)
            metrics.tool_calls_total.labels(tool_name=tool_name, status="failed").inc()
            metrics.task_executions_total.labels(
                task_type=task_type, status="failed"
            ).inc()
            return _handle_task_failure(session, task, f"unexpected error: {exc}")

        tool_elapsed = time.monotonic() - tool_start
        metrics.tool_calls_total.labels(tool_name=tool_name, status="succeeded").inc()
        metrics.tool_duration_seconds.labels(tool_name=tool_name).observe(tool_elapsed)

        # ── Persist output & mark SUCCEEDED ──────────────────────────────────
        task.tool_output = output
        task.status = "succeeded"
        task.finished_at = datetime.now(timezone.utc)
        session.commit()

        task_elapsed = time.monotonic() - task_start
        metrics.task_duration_seconds.labels(task_type=task_type).observe(task_elapsed)
        metrics.task_executions_total.labels(
            task_type=task_type, status="succeeded"
        ).inc()
        logger.info(
            "execute_step: task succeeded",
            extra={"task_id": task_id, "step_id": task_step_id},
        )

        # ── M7: store tool output to memory ───────────────────────────────────
        if task_type == "tool_call" and workspace_id:
            _try_store_task_memory(task, output, workspace_id)

        # ── Update job terminal state ─────────────────────────────────────────
        job_done = _check_job_completion(session, task_job_id)

        # ── M7: store job result to memory when job just succeeded ────────────
        if job_done and workspace_id:
            session.refresh(job)
            if job.status == "succeeded":
                _try_store_job_memory(job, workspace_id)

        # ── Dispatch newly unblocked dependent tasks ──────────────────────────
        newly_enqueued: list[str] = []
        if not job_done:
            newly_enqueued = _enqueue_newly_ready(session, task)

    # task_step_id is a plain str local — safe to read after session closes.
    return {
        "task_id": task_id,
        "status": "succeeded",
        "step_id": task_step_id,
        "newly_enqueued": newly_enqueued,
    }


# ---------------------------------------------------------------------------
# Atomic task state transitions
# ---------------------------------------------------------------------------

def _claim_task(session: Session, task: TaskModel) -> bool:
    """
    Atomically transition task from PENDING or QUEUED to RUNNING.

    Uses a WHERE-guarded UPDATE so concurrent workers racing on the same
    task_id are safe: only one will get rowcount=1, the rest get rowcount=0
    and must not proceed.

    Returns True if this worker successfully claimed the task.
    """
    now = datetime.now(timezone.utc)
    result = session.execute(
        sa_update(TaskModel)
        .where(
            TaskModel.id == task.id,
            TaskModel.status.in_(["pending", "queued"]),
        )
        .values(
            status="running",
            started_at=now,
            attempt_count=TaskModel.attempt_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        return False
    # Sync in-memory object to match the committed DB state.
    task.status = "running"
    task.started_at = now
    task.attempt_count = (task.attempt_count or 0) + 1
    session.commit()
    return True


def _enqueue_ready_task(session: Session, task: TaskModel) -> bool:
    """
    Atomically mark a PENDING task as QUEUED before sending to Celery.

    Prevents two workers completing sibling tasks from both enqueuing the
    same newly-ready downstream task.  Returns True if this worker won the
    race; False means another worker already claimed it.
    """
    result = session.execute(
        sa_update(TaskModel)
        .where(TaskModel.id == task.id, TaskModel.status == "pending")
        .values(status="queued")
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if result.rowcount == 0:
        return False
    task.status = "queued"
    return True


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

def _invoke_tool(
    task: TaskModel,
    memory_context: list[str] | None = None,
    session: Session | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Resolve and call the registered tool for this task.

    Synthesis steps aggregate the tool_output of every completed sibling
    tool_call task in the same job.  If *memory_context* is provided it is
    appended for downstream visibility.
    """
    if task.task_type == "synthesis":
        collected: list[dict[str, Any]] = []
        job_prompt: str = ""
        if session is not None:
            siblings = (
                session.query(TaskModel)
                .filter(
                    TaskModel.job_id == task.job_id,
                    TaskModel.task_type == "tool_call",
                    TaskModel.status == "succeeded",
                    TaskModel.tool_output.isnot(None),
                )
                .order_by(TaskModel.sequence)
                .all()
            )
            for s in siblings:
                collected.append({
                    "step_id":   s.step_id,
                    "name":      s.name,
                    "tool_name": s.tool_name,
                    "output":    s.tool_output,
                })
            job = session.get(JobModel, task.job_id)
            job_prompt = (job.prompt if job else "") or ""

        final_answer = _llm_synthesize(job_prompt, collected, memory_context or [])
        result: dict[str, Any] = {
            "final_answer":    final_answer,
            "collected_steps": collected,
        }
        if memory_context:
            result["memory_context"] = memory_context
        return result

    if not task.tool_name:
        raise ToolError(
            f"task {task.id} has task_type={task.task_type!r} but no tool_name"
        )

    tool_fn = get_tool(task.tool_name)  # raises ToolError for unknown names
    tool_input: dict[str, Any] = dict(task.tool_input or {})
    # Inject workspace context so tools like "retrieval" can scope their queries.
    # All existing tools accept **_ so this is safe for any registered tool.
    tool_input["_workspace_id"] = workspace_id or ""
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
            logger.debug("skipped downstream task", extra={"step_id": step_id})

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
    1. Mark task FAILED with finished_at timestamp.
    2. Skip all transitive PENDING dependents (clean terminal state).
    3. Fast-fail the job.
    """
    task.status = "failed"
    task.error = error_msg
    task.finished_at = datetime.now(timezone.utc)
    session.commit()
    logger.error(
        "execute_step: task failed",
        extra={"task_id": str(task.id), "step_id": task.step_id, "error": error_msg},
    )

    all_tasks: list[TaskModel] = (
        session.query(TaskModel)
        .filter(TaskModel.job_id == task.job_id)
        .all()
    )

    if task.step_id:
        skipped = _skip_downstream(session, all_tasks, task.step_id)
        if skipped:
            logger.info(
                "execute_step: skipped downstream tasks after failure",
                extra={"step_id": task.step_id, "skipped_count": skipped},
            )

    # Fast-fail: mark job failed without waiting for independent branches
    job = session.get(JobModel, task.job_id)
    if job and job.status not in ("succeeded", "failed", "cancelled"):
        job.status = "failed"
        job.error = error_msg
        session.commit()
        logger.info(
            "execute_step: job fast-failed",
            extra={"job_id": str(job.id), "failed_step": task.step_id},
        )

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
        logger.info("execute_step: job failed", extra={"job_id": str(job_id)})
    else:
        job.status = "succeeded"
        synthesis = next(
            (t for t in all_tasks if t.task_type == "synthesis"), None
        )
        if synthesis and synthesis.tool_output:
            raw = synthesis.tool_output
            job.result = raw.get("final_answer") or raw.get("note") or str(raw)
        else:
            job.result = "All steps completed successfully"
        session.commit()
        logger.info("execute_step: job succeeded", extra={"job_id": str(job_id)})

    return True


# ---------------------------------------------------------------------------
# Dependency dispatch
# ---------------------------------------------------------------------------

def _enqueue_newly_ready(session: Session, completed_task: TaskModel) -> list[str]:
    """
    After *completed_task* succeeds, find every PENDING task whose full
    dependency set is now satisfied and enqueue it to the executor queue.

    Uses _enqueue_ready_task for each candidate so concurrent workers
    completing sibling tasks cannot double-enqueue the same downstream task.
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
        if not _enqueue_ready_task(session, t):
            logger.debug(
                "enqueue skipped — task already claimed",
                extra={"task_id": str(t.id), "step_id": step_id},
            )
            continue
        app.send_task(TASK_EXECUTE_STEP, args=[str(t.id)], queue=QUEUE_EXECUTOR)
        enqueued.append(str(t.id))
        logger.debug(
            "enqueued newly-ready task",
            extra={"task_id": str(t.id), "step_id": step_id},
        )

    return enqueued


# ---------------------------------------------------------------------------
# LLM-based synthesis helper
# ---------------------------------------------------------------------------

def _llm_synthesize(
    job_prompt: str,
    collected: list[dict[str, Any]],
    memory_context: list[str],
) -> str:
    """
    Generate a final answer by calling the LLM with the original user prompt
    and the collected tool outputs as context.

    Falls back to a plain-text summary if OpenAI is not configured or the
    API call fails — synthesis is never blocked by LLM availability.
    """
    # Build the context block from collected tool outputs
    context_parts: list[str] = []
    for i, step in enumerate(collected, 1):
        name = step.get("name") or step.get("step_id") or f"Step {i}"
        tool = step.get("tool_name") or "tool"
        output = step.get("output") or {}
        # Truncate very large outputs so we stay within token limits
        output_str = str(output)[:1500]
        context_parts.append(f"Step {i} — {name} ({tool}):\n{output_str}")

    if memory_context:
        context_parts.append(
            "Relevant past context:\n" + "\n".join(memory_context)
        )

    context_text = "\n\n".join(context_parts) if context_parts else "(no tool outputs available)"

    synthesis_prompt = (
        f"You are a helpful AI assistant synthesising the results of an agent pipeline.\n\n"
        f"ORIGINAL USER REQUEST:\n{job_prompt}\n\n"
        f"COMPLETED TOOL OUTPUTS:\n{context_text}\n\n"
        f"Using the above information, write a clear, complete, and concise final answer "
        f"to the user's original request. Do not repeat the tool outputs verbatim — "
        f"synthesise them into a coherent response."
    )

    # Try ZhipuAI if configured (takes priority over OpenAI)
    if settings.zhipu_api_key:
        try:
            from zhipuai import ZhipuAI
            client = ZhipuAI(api_key=settings.zhipu_api_key)
            resp = client.chat.completions.create(
                model=settings.zhipu_model,
                messages=[{"role": "user", "content": synthesis_prompt}],
            )
            answer = (resp.choices[0].message.content or "").strip()
            if answer:
                logger.info("ZhipuAI synthesis succeeded (%d chars)", len(answer))
                return answer
        except Exception as exc:
            logger.warning("ZhipuAI synthesis failed, using fallback: %s", exc)

    # Try OpenAI if configured (same check as planner factory)
    elif settings.openai_api_key and settings.openai_api_key not in ("sk-not-set", ""):
        try:
            from openai import OpenAI, OpenAIError
            client = OpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "user", "content": synthesis_prompt}],
                temperature=0.3,
                max_tokens=1024,
            )
            answer = (response.choices[0].message.content or "").strip()
            if answer:
                logger.info("LLM synthesis succeeded (%d chars)", len(answer))
                return answer
        except Exception as exc:
            logger.warning("LLM synthesis failed, using fallback: %s", exc)

    # Fallback: structured plain-text summary
    if not collected:
        return f"No tool steps completed successfully for: {job_prompt}"

    lines = [f"Summary for: {job_prompt}\n"]
    for i, step in enumerate(collected, 1):
        name = step.get("name") or step.get("step_id") or f"Step {i}"
        output = step.get("output") or {}
        lines.append(f"{i}. {name}: {str(output)[:300]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# M7: Memory integration helpers (fire-and-forget — never fail the task)
# ---------------------------------------------------------------------------

def _retrieve_memory_context(
    workspace_id: str,
    task: TaskModel,
    job: JobModel,
) -> list[str]:
    """
    Search memory for past results relevant to the synthesis task.
    Returns an empty list on any error so synthesis is never blocked.
    """
    try:
        store = get_memory_store()
        query = task.description or (job.prompt if job else "") or ""
        entries = store.search(workspace_id, query=query, top_k=3)
        return [e.content for e in entries]
    except Exception:
        logger.warning("Failed to retrieve memory context for synthesis", exc_info=True)
        return []


def _try_store_task_memory(
    task: TaskModel,
    output: dict[str, Any],
    workspace_id: str,
) -> None:
    """Store a tool_call task's output to memory.  Swallows all exceptions."""
    try:
        content = (
            f"Tool: {task.tool_name}\n"
            f"Task: {task.name}\n"
            f"Description: {task.description or ''}\n"
            f"Output: {str(output)[:800]}"
        )
        entry = MemoryEntry(
            workspace_id=workspace_id,
            job_id=str(task.job_id),
            entry_type="tool_output",
            content=content,
            metadata={
                "task_id":   str(task.id),
                "step_id":   task.step_id,
                "tool_name": task.tool_name,
            },
        )
        get_memory_store().store(entry)
    except Exception:
        logger.warning(
            "Failed to store task memory",
            extra={"task_id": str(task.id)},
            exc_info=True,
        )


def _try_store_job_memory(job: JobModel, workspace_id: str) -> None:
    """Store a completed job's result to memory.  Swallows all exceptions."""
    try:
        content = f"Prompt: {job.prompt}\nResult: {job.result or ''}"
        entry = MemoryEntry(
            workspace_id=workspace_id,
            job_id=str(job.id),
            entry_type="job_result",
            content=content,
            metadata={"job_id": str(job.id)},
        )
        get_memory_store().store(entry)
    except Exception:
        logger.warning(
            "Failed to store job memory",
            extra={"job_id": str(job.id)},
            exc_info=True,
        )
