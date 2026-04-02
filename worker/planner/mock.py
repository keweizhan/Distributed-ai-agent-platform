"""
MockPlanner — returns a deterministic plan without calling any external service.
Used when OPENAI_API_KEY is not set, or in tests.

The plan always has three steps:
  1. web_search   — look up information
  2. code_exec    — process / analyse the results
  3. synthesis    — summarise into a final answer

This is generic enough to be meaningful for most prompts while keeping
the end-to-end pipeline exercisable without a live LLM.
"""

from __future__ import annotations

import uuid

from shared.models import ExecutionPlan, PlannedStep, TaskType
from worker.planner.base import BasePlanner


class MockPlanner(BasePlanner):
    def plan(
        self,
        job_id: uuid.UUID,
        prompt: str,
        context: list[str] | None = None,
    ) -> ExecutionPlan:
        # context is intentionally ignored — mock always returns the same shape.
        steps = [
            PlannedStep(
                step_id="web_search",
                name="Search for information",
                description=f"Search the web for information relevant to: {prompt[:120]}",
                task_type=TaskType.TOOL_CALL,
                tool_name="web_search",
                tool_input={"query": prompt[:200]},
                dependencies=[],
                priority=0,
                expected_output="A list of relevant search results",
            ),
            PlannedStep(
                step_id="analyse_results",
                name="Analyse results",
                description="Process and extract key findings from the search results",
                task_type=TaskType.TOOL_CALL,
                tool_name="code_exec",
                tool_input={
                    "code": (
                        "# Placeholder — real analysis will be injected by the executor\n"
                        "print('analysis complete')"
                    )
                },
                dependencies=["web_search"],
                priority=0,
                expected_output="Structured findings extracted from search results",
            ),
            PlannedStep(
                step_id="synthesise",
                name="Synthesise final answer",
                description="Aggregate findings into a clear, concise final answer",
                task_type=TaskType.SYNTHESIS,
                tool_name=None,
                tool_input={},
                dependencies=["web_search", "analyse_results"],
                priority=0,
                expected_output="A complete answer to the user's original request",
            ),
        ]
        return ExecutionPlan(job_id=job_id, steps=steps)
