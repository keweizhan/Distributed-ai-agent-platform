"""
web_search tool — returns top results for a query.

Provider priority:
  1. Tavily   — if TAVILY_API_KEY is configured (reliable, structured results)
  2. DuckDuckGo — fallback when Tavily is absent or fails (no API key required)

The output shape is identical regardless of which provider is used.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from worker.config import settings
from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)

_DDG_MAX_ATTEMPTS = 3   # initial attempt + 2 retries for DuckDuckGo rate-limits
_DDG_RETRY_DELAY_S = 2


@register_tool("web_search")
def web_search(query: str, max_results: int = 5, **_: Any) -> dict[str, Any]:
    """
    Search the web for *query* and return up to *max_results* results.

    Returns:
        {
            "query":    str,
            "results":  [{"title": str, "url": str, "snippet": str}, ...],
            "provider": "tavily" | "duckduckgo",
            "note":     str   # present only on fallback / error paths
        }
    """
    if not query or not query.strip():
        raise ToolError("web_search requires a non-empty 'query' argument")

    n = max(1, min(max_results, settings.tavily_max_results if settings.tavily_api_key else max_results))

    # ── 1. Tavily (primary) ────────────────────────────────────────────────────
    if settings.tavily_api_key:
        try:
            from tavily import TavilyClient  # type: ignore[import]
            client = TavilyClient(api_key=settings.tavily_api_key)
            response = client.search(query=query, max_results=n)
            results = [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("content", ""),
                }
                for r in response.get("results", [])
            ]
            logger.debug("web_search(tavily, %r) → %d results", query, len(results))
            return {"query": query, "results": results, "provider": "tavily"}

        except ImportError:
            logger.warning(
                "tavily-python is not installed; falling back to DuckDuckGo. "
                "Add tavily-python to worker/requirements.txt."
            )
        except Exception as exc:
            logger.warning(
                "Tavily search failed (%s); falling back to DuckDuckGo.", exc
            )

    # ── 2. DuckDuckGo (fallback) ───────────────────────────────────────────────
    try:
        from duckduckgo_search import DDGS  # type: ignore[import]
    except ImportError:
        logger.warning("duckduckgo_search is not installed; returning empty results.")
        return {
            "query": query,
            "results": [],
            "provider": "none",
            "note": "No search provider available (tavily-python and duckduckgo-search both missing).",
        }

    try:
        from duckduckgo_search.exceptions import RatelimitException  # type: ignore[import]
    except ImportError:
        RatelimitException = None  # type: ignore[assignment,misc]

    last_exc: Exception | None = None
    for attempt in range(_DDG_MAX_ATTEMPTS):
        try:
            raw = DDGS().text(query, max_results=max_results)
            results = [
                {"title": r["title"], "url": r["href"], "snippet": r["body"]}
                for r in (raw or [])
            ]
            logger.debug("web_search(duckduckgo, %r) → %d results", query, len(results))
            return {"query": query, "results": results, "provider": "duckduckgo"}

        except Exception as exc:
            is_ratelimit = RatelimitException is not None and isinstance(exc, RatelimitException)
            if not is_ratelimit:
                raise ToolError(f"web_search failed: {exc}") from exc

            last_exc = exc
            if attempt < _DDG_MAX_ATTEMPTS - 1:
                logger.warning(
                    "web_search: DuckDuckGo rate limited (attempt %d/%d), "
                    "retrying in %ds — %s",
                    attempt + 1, _DDG_MAX_ATTEMPTS, _DDG_RETRY_DELAY_S, exc,
                )
                time.sleep(_DDG_RETRY_DELAY_S)
            else:
                logger.error(
                    "web_search: rate limit persists after %d attempts for query=%r: %s",
                    _DDG_MAX_ATTEMPTS, query, exc,
                )

    return {
        "query": query,
        "results": [],
        "provider": "duckduckgo",
        "note": (
            f"DuckDuckGo rate limit reached after {_DDG_MAX_ATTEMPTS} attempts. "
            "Search results are unavailable for this query."
        ),
    }
