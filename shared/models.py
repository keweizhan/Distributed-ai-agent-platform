"""
Shared Pydantic domain models used by both the API and worker services.
These are pure data shapes — no DB or framework dependencies.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations (mirror the Postgres ENUMs)
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING   = "pending"
    PLANNING  = "planning"
    PLANNED   = "planned"       # plan persisted; dispatching executor tasks
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING   = "pending"
    QUEUED    = "queued"        # sent to Celery, not yet picked up
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    SKIPPED   = "skipped"


class TaskType(str, Enum):
    PLAN      = "plan"
    TOOL_CALL = "tool_call"
    SYNTHESIS = "synthesis"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class Job(BaseModel):
    id:         uuid.UUID
    prompt:     str
    status:     JobStatus
    result:     str | None = None
    error:      str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class Task(BaseModel):
    id:              uuid.UUID
    job_id:          uuid.UUID
    parent_task_id:  uuid.UUID | None = None
    step_id:         str | None = None
    task_type:       TaskType
    name:            str
    description:     str | None = None
    tool_name:       str | None = None
    tool_input:      dict[str, Any] | None = None
    tool_output:     dict[str, Any] | None = None
    dependencies:    list[str] = Field(default_factory=list)
    priority:        int = 0
    status:          TaskStatus
    error:           str | None = None
    sequence:        int
    expected_output: str | None = None
    created_at:      datetime
    updated_at:      datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# LLM planner contract
# ---------------------------------------------------------------------------

class PlannedStep(BaseModel):
    """One step produced by the planner. The LLM fills all fields except sequence."""

    step_id: str = Field(
        ...,
        description="Unique snake_case key for this step, e.g. 'search_papers'",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    name: str = Field(..., description="Short human-readable title")
    description: str = Field(..., description="What this step should accomplish")
    task_type: TaskType = Field(default=TaskType.TOOL_CALL)
    tool_name: str | None = Field(
        default=None,
        description="Registered tool to invoke. Required when task_type=tool_call.",
    )
    tool_input: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool verbatim",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="step_ids this step depends on. Empty means no prerequisites.",
    )
    priority: int = Field(
        default=0,
        ge=0,
        description="Execution priority — lower value runs first among ready steps",
    )
    expected_output: str = Field(
        default="",
        description="Short description of what a successful result looks like",
    )
    # sequence is derived from list position — not provided by the LLM
    sequence: int = Field(default=0, exclude=True)


class ExecutionPlan(BaseModel):
    """Full plan returned by the planner for a single job."""

    job_id: uuid.UUID
    steps: list[PlannedStep] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_dependencies(self) -> ExecutionPlan:
        known = {s.step_id for s in self.steps}
        for step in self.steps:
            unknown = set(step.dependencies) - known
            if unknown:
                raise ValueError(
                    f"Step '{step.step_id}' references unknown dependencies: {unknown}"
                )
        # Inject sequence from list position
        for i, step in enumerate(self.steps):
            step.sequence = i
        return self

    def ready_steps(self, completed_step_ids: set[str]) -> list[PlannedStep]:
        """Return steps whose dependencies are all satisfied."""
        return [
            s for s in self.steps
            if set(s.dependencies).issubset(completed_step_ids)
        ]
