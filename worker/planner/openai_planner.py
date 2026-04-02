"""
OpenAIPlanner — calls an OpenAI-compatible LLM and parses the response into
a validated ExecutionPlan.

Tradeoffs:
- We use json_object response_format (available on gpt-4o-mini and later) so
  the model is guaranteed to emit valid JSON. We then validate with Pydantic.
- We do NOT use json_schema mode (structured outputs) because it requires
  registering a JSON Schema and is only available on specific model versions.
  json_object + Pydantic validation is simpler and broadly compatible.
- On parse or validation error we raise PlannerError immediately — we never
  silently return a partial plan.
"""

from __future__ import annotations

import json
import logging
import uuid

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from shared.models import ExecutionPlan, PlannedStep
from worker.config import settings
from worker.planner.base import BasePlanner, PlannerError
from worker.planner.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

# How many steps the LLM is allowed to return (sanity guard)
_MAX_STEPS = 12


class OpenAIPlanner(BasePlanner):
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._model = settings.openai_model

    def plan(
        self,
        job_id: uuid.UUID,
        prompt: str,
        context: list[str] | None = None,
    ) -> ExecutionPlan:
        user_msg = build_user_prompt(prompt, context=context)

        logger.info("Calling LLM planner (model=%s, job=%s)", self._model, job_id)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.2,   # low temperature → more deterministic structure
                max_tokens=2048,
            )
        except OpenAIError as exc:
            raise PlannerError(f"LLM API call failed: {exc}") from exc

        raw = response.choices[0].message.content or ""
        logger.debug("LLM raw response: %s", raw[:500])

        return self._parse(job_id, raw)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse(self, job_id: uuid.UUID, raw: str) -> ExecutionPlan:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlannerError(f"LLM returned non-JSON: {exc}\nRaw: {raw[:300]}") from exc

        if "steps" not in data or not isinstance(data["steps"], list):
            raise PlannerError(f"LLM response missing 'steps' list. Got keys: {list(data)}")

        if len(data["steps"]) > _MAX_STEPS:
            raise PlannerError(
                f"LLM returned {len(data['steps'])} steps; maximum allowed is {_MAX_STEPS}"
            )

        try:
            steps = [PlannedStep.model_validate(s) for s in data["steps"]]
        except ValidationError as exc:
            raise PlannerError(f"Step validation failed:\n{exc}") from exc

        try:
            plan = ExecutionPlan(job_id=job_id, steps=steps)
        except ValidationError as exc:
            raise PlannerError(f"ExecutionPlan validation failed:\n{exc}") from exc

        logger.info("Plan validated: %d steps for job %s", len(steps), job_id)
        return plan
