"""
MockPlanner — returns a deterministic plan without calling any external service.
Used when OPENAI_API_KEY is not set, or in tests.

Default plan (web search query):
  1. web_search   — look up information
  2. code_exec    — process / analyse the results
  3. synthesis    — summarise into a final answer

Retrieval plan (knowledge-base query detected by heuristic):
  1. retrieval    — search the user's ingested documents
  2. synthesis    — summarise the retrieved chunks

This is generic enough to be meaningful for most prompts while keeping
the end-to-end pipeline exercisable without a live LLM.
"""

from __future__ import annotations

import uuid

from shared.models import ExecutionPlan, PlannedStep, TaskType
from worker.planner.base import BasePlanner

# ---------------------------------------------------------------------------
# Keyword heuristic: phrases that strongly suggest the user is asking about
# documents they have uploaded rather than the public internet.
# ---------------------------------------------------------------------------
_RETRIEVAL_SIGNALS = frozenset({
    "according to my",
    "according to the document",
    "based on my document",
    "based on the document",
    "based on the file",
    "based on what i uploaded",
    "from my document",
    "from my file",
    "from my knowledge base",
    "from my notes",
    "from the document",
    "from the file i",
    "from the uploaded",
    "i uploaded",
    "in my document",
    "in my knowledge base",
    "in my notes",
    "in the document",
    "in the file",
    "in the uploaded",
    "my document says",
    "my uploaded",
    "search my document",
    "search my knowledge",
    "what did i upload",
    "what does my document",
    "what does the document",
    "what does the file",
})


def _is_retrieval_query(prompt: str) -> bool:
    """
    Return True when the prompt contains at least one phrase that implies the
    user is asking about their own uploaded documents rather than the internet.
    """
    lower = prompt.lower()
    return any(signal in lower for signal in _RETRIEVAL_SIGNALS)


class MockPlanner(BasePlanner):
    def plan(
        self,
        job_id: uuid.UUID,
        prompt: str,
        context: list[str] | None = None,
    ) -> ExecutionPlan:
        # context is intentionally ignored — mock always returns the same shape.
        if _is_retrieval_query(prompt):
            return self._retrieval_plan(job_id, prompt)
        return self._web_search_plan(job_id, prompt)

    # ------------------------------------------------------------------
    # Plan templates
    # ------------------------------------------------------------------

    def _retrieval_plan(self, job_id: uuid.UUID, prompt: str) -> ExecutionPlan:
        """Two-step plan: search ingested documents → synthesise."""
        steps = [
            PlannedStep(
                step_id="retrieve_docs",
                name="Search knowledge base",
                description=f"Search ingested documents for content relevant to: {prompt[:120]}",
                task_type=TaskType.TOOL_CALL,
                tool_name="retrieval",
                tool_input={"query": prompt[:200], "top_k": 5},
                dependencies=[],
                priority=0,
                expected_output="Relevant document chunks from the knowledge base",
            ),
            PlannedStep(
                step_id="synthesise",
                name="Synthesise answer",
                description="Combine retrieved document chunks into a coherent final answer",
                task_type=TaskType.SYNTHESIS,
                tool_name=None,
                tool_input={},
                dependencies=["retrieve_docs"],
                priority=0,
                expected_output="A complete answer drawn from the user's uploaded documents",
            ),
        ]
        return ExecutionPlan(job_id=job_id, steps=steps)

    def _web_search_plan(self, job_id: uuid.UUID, prompt: str) -> ExecutionPlan:
        """Three-step plan: web search → analyse → synthesise."""
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
