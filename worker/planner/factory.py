"""
Planner factory — selects the right planner implementation based on config.

Switching to a different LLM provider (Anthropic, Mistral, local Ollama) only
requires adding a new BasePlanner subclass and updating this function.
"""

from __future__ import annotations

from worker.config import settings
from worker.planner.base import BasePlanner


def get_planner() -> BasePlanner:
    """
    Return the appropriate planner:
    - OpenAIPlanner  if OPENAI_API_KEY is configured
    - MockPlanner    otherwise (safe default for local dev / CI)
    """
    if settings.openai_api_key and settings.openai_api_key not in ("sk-not-set", ""):
        from worker.planner.openai_planner import OpenAIPlanner
        return OpenAIPlanner()

    from worker.planner.mock import MockPlanner
    return MockPlanner()
