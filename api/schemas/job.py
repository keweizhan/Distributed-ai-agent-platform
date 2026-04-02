"""Request/response schemas for the jobs API."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from shared.models import JobStatus, TaskStatus, TaskType


class CreateJobRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4096, description="Natural language task description")


class JobResponse(BaseModel):
    id:           uuid.UUID
    workspace_id: uuid.UUID | None = None
    prompt:       str
    status:       JobStatus
    result:       str | None = None
    error:        str | None = None
    created_at:   datetime
    updated_at:   datetime

    model_config = {"from_attributes": True}


class TaskResponse(BaseModel):
    id:              uuid.UUID
    job_id:          uuid.UUID
    step_id:         str | None = None
    task_type:       TaskType
    name:            str
    description:     str | None = None
    tool_name:       str | None = None
    tool_input:      dict[str, Any] | None = None
    tool_output:     dict[str, Any] | None = None
    dependencies:    list[str] = []
    priority:        int = 0
    status:          TaskStatus
    error:           str | None = None
    sequence:        int
    expected_output: str | None = None
    attempt_count:   int = 0
    started_at:      datetime | None = None
    finished_at:     datetime | None = None

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    tasks: list[TaskResponse] = []
