"""
web_search tool — returns top results for a query via DuckDuckGo.

No API key required. Uses the duckduckgo_search package.
Falls back gracefully if the package is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)


@register_tool("web_search")
def web_search(query: str, max_results: int = 5, **_: Any) -> dict[str, Any]:
    """
    Search the web for *query* and return up to *max_results* results.

    Returns:
        {
            "query": str,
            "results": [{"title": str, "url": str, "snippet": str}, ...]
        }
    """
    if not query or not query.strip():
        raise ToolError("web_search requires a non-empty 'query' argument")

    try:
        from duckduckgo_search import DDGS  # type: ignore[import]
    except ImportError:
        logger.warning(
            "duckduckgo_search is not installed; returning empty results. "
            "Add duckduckgo-search to worker/requirements.txt to enable real search."
        )
        return {
            "query": query,
            "results": [],
            "note": "duckduckgo_search package not installed",
        }

    try:
        raw = DDGS().text(query, max_results=max_results)
        results = [
            {"title": r["title"], "url": r["href"], "snippet": r["body"]}
            for r in (raw or [])
        ]
        logger.debug("web_search(%r) → %d results", query, len(results))
        return {"query": query, "results": results}
    except Exception as exc:
        raise ToolError(f"web_search failed: {exc}") from exc
