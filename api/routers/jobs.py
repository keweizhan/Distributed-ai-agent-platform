"""
Jobs router — CRUD + job submission.
"""

import uuid

from celery import Celery
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.config import settings
from api.db.models import JobModel, TaskModel
from api.db.session import get_db
from api.schemas.job import CreateJobRequest, JobDetailResponse, JobResponse
from shared.constants import QUEUE_PLANNER, TASK_PLAN_JOB

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Celery client — API only sends tasks, never runs them
_celery: Celery | None = None


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
) -> JobModel:
    job = JobModel(prompt=body.prompt, status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue the planning task
    get_celery().send_task(
        TASK_PLAN_JOB,
        args=[str(job.id)],
        queue=QUEUE_PLANNER,
    )

    return job


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}  — fetch job + tasks
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JobModel:
    result = await db.execute(
        select(JobModel)
        .options(selectinload(JobModel.tasks))
        .where(JobModel.id == job_id)
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
) -> list[JobModel]:
    result = await db.execute(
        select(JobModel).order_by(JobModel.created_at.desc()).limit(50)
    )
    return list(result.scalars().all())
