"""
Planner abstraction.

All planners implement BasePlanner.plan(). The caller gets back a validated
ExecutionPlan or a PlannerError is raised — never a silent fallback.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from shared.models import ExecutionPlan


class PlannerError(Exception):
    """Raised when planning fails (LLM error, parse failure, validation error)."""


class BasePlanner(ABC):
    @abstractmethod
    def plan(self, job_id: uuid.UUID, prompt: str) -> ExecutionPlan:
        """
        Generate an ExecutionPlan for the given job.

        Args:
            job_id: The UUID of the job being planned.
            prompt: The user's natural language request.

        Returns:
            A validated ExecutionPlan.

        Raises:
            PlannerError: If planning fails for any reason.
        """
