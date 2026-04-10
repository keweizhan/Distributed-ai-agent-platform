"""
Jobs router — CRUD, cancellation, and per-task inspection.

All endpoints require a valid JWT bearer token.
Every query is scoped to the authenticated user's workspace so tenants
cannot observe or modify each other's jobs.
"""

import uuid

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.auth.dependencies import get_current_workspace
from api.config import settings
from api.db.models import JobModel, TaskModel, WorkspaceModel
from api.db.session import get_db
from api.metrics import jobs_cancelled_total, jobs_created_total
from api.schemas.job import CreateJobRequest, JobDetailResponse, JobResponse, TaskResponse
from shared.constants import QUEUE_PLANNER, TASK_PLAN_JOB

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Celery client — API only sends tasks, never runs them
_celery: Celery | None = None

# Job statuses that can still be cancelled
_CANCELLABLE = {"pending", "planning", "planned", "running"}
# Job statuses that are already terminal
_TERMINAL = {"succeeded", "failed", "cancelled"}


def get_celery() -> Celery:
    global _celery
    if _celery is None:
        _celery = Celery(broker=settings.celery_broker_url, backend=settings.celery_result_backend)
    return _celery


# ---------------------------------------------------------------------------
# POST /jobs  — submit a new job
# ---------------------------------------------------------------------------

@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    body: CreateJobRequest,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> JobModel:
    job = JobModel(prompt=body.prompt, status="pending", workspace_id=workspace.id)
    db.add(job)
    await db.commit()
    await db.refresh(job)

    get_celery().send_task(TASK_PLAN_JOB, args=[str(job.id)], queue=QUEUE_PLANNER)
    jobs_created_total.inc()
    return job


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}  — fetch job + tasks
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> JobModel:
    result = await db.execute(
        select(JobModel)
        .options(selectinload(JobModel.tasks))
        .where(JobModel.id == job_id, JobModel.workspace_id == workspace.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# GET /jobs  — list recent jobs (newest first, limit 50)
# ---------------------------------------------------------------------------

@router.get("", response_model=list[JobResponse])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> list[JobModel]:
    result = await db.execute(
        select(JobModel)
        .where(JobModel.workspace_id == workspace.id)
        .order_by(JobModel.created_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/cancel  — request cancellation
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    summary="Cancel a job",
    responses={
        409: {"description": "Job is already in a terminal state"},
        404: {"description": "Job not found"},
    },
)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> JobModel:
    """
    Mark a job as cancelled.

    Already-running tasks will be skipped via the executor's pre-flight check
    (which detects job.status == 'cancelled' and exits without invoking the tool).
    Pending tasks are skipped by the same guard when they are eventually picked up.

    Returns 409 if the job is already in a terminal state (succeeded/failed/cancelled).
    """
    result = await db.execute(
        select(JobModel).where(JobModel.id == job_id, JobModel.workspace_id == workspace.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in _TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {job.status} and cannot be cancelled",
        )
    job.status = "cancelled"
    await db.commit()
    await db.refresh(job)
    jobs_cancelled_total.inc()
    return job


# ---------------------------------------------------------------------------
# DELETE /jobs/{job_id}  — permanently remove a job and its tasks
# ---------------------------------------------------------------------------

@router.delete(
    "/{job_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a job",
    responses={404: {"description": "Job not found"}},
)
async def delete_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> dict:
    """
    Permanently delete a job and all its associated tasks.

    Tasks are removed via SQLAlchemy's cascade="all, delete-orphan" on the
    JobModel.tasks relationship (backed by ondelete="CASCADE" on the FK).
    Only jobs that belong to the caller's workspace can be deleted.
    """
    result = await db.execute(
        select(JobModel).where(JobModel.id == job_id, JobModel.workspace_id == workspace.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.delete(job)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/tasks/{task_id}  — inspect a single task
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Get task detail",
)
async def get_task(
    job_id: uuid.UUID,
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> TaskModel:
    """
    Return the full detail for a single task, including tool_input, tool_output,
    attempt_count, started_at, and finished_at.

    The workspace guard ensures the task's parent job belongs to the caller's workspace.
    """
    result = await db.execute(
        select(TaskModel)
        .join(JobModel, TaskModel.job_id == JobModel.id)
        .where(
            TaskModel.id == task_id,
            TaskModel.job_id == job_id,
            JobModel.workspace_id == workspace.id,
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
