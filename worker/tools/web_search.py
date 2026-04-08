"""
web_search tool — returns top results for a query via DuckDuckGo.

No API key required. Uses the duckduckgo_search package.
Falls back gracefully if the package is not installed or if DuckDuckGo
rate-limits the request (HTTP 202 → RatelimitException in duckduckgo-search 6.x).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3   # initial attempt + 2 retries
_RETRY_DELAY_S = 2  # fixed wait between attempts (seconds)


@register_tool("web_search")
def web_search(query: str, max_results: int = 5, **_: Any) -> dict[str, Any]:
    """
    Search the web for *query* and return up to *max_results* results.

    On DuckDuckGo rate-limit (HTTP 202 / RatelimitException) the call is
    retried up to _MAX_ATTEMPTS times with a fixed delay.  If all attempts
    are exhausted the tool returns an empty-results dict (task succeeds)
    rather than raising ToolError and killing the job.

    Returns:
        {
            "query": str,
            "results": [{"title": str, "url": str, "snippet": str}, ...],
            "note": str   # present only on fallback / import-missing paths
        }
    """
    if not query or not query.strip():
        raise ToolError("web_search requires a non-empty 'query' argument")

    # ── Dependency check ────────────────────────────────────────────────────
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

    # ── Rate-limit exception class (duckduckgo-search >= 6.x) ───────────────
    # Import defensively so the tool doesn't break on unexpected package layouts.
    try:
        from duckduckgo_search.exceptions import RatelimitException  # type: ignore[import]
    except ImportError:
        RatelimitException = None  # type: ignore[assignment,misc]

    # ── Search with retry on rate limit ─────────────────────────────────────
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            raw = DDGS().text(query, max_results=max_results)
            results = [
                {"title": r["title"], "url": r["href"], "snippet": r["body"]}
                for r in (raw or [])
            ]
            logger.debug("web_search(%r) → %d results", query, len(results))
            return {"query": query, "results": results}

        except Exception as exc:
            # Only treat RatelimitException as retryable; all other errors are hard failures.
            is_ratelimit = (
                RatelimitException is not None and isinstance(exc, RatelimitException)
            )
            if not is_ratelimit:
                raise ToolError(f"web_search failed: {exc}") from exc

            last_exc = exc
            if attempt < _MAX_ATTEMPTS - 1:
                logger.warning(
                    "web_search: DuckDuckGo rate limited (attempt %d/%d), "
                    "retrying in %ds — %s",
                    attempt + 1, _MAX_ATTEMPTS, _RETRY_DELAY_S, exc,
                )
                time.sleep(_RETRY_DELAY_S)
            else:
                logger.error(
                    "web_search: rate limit persists after %d attempts for query=%r: %s",
                    _MAX_ATTEMPTS, query, exc,
                )

    # ── Graceful fallback — return empty results, do not fail the task ───────
    # Returning a result dict (rather than raising ToolError) marks the task as
    # "succeeded" with empty search output.  The synthesis step can still run
    # and the job reaches a terminal state cleanly.
    return {
        "query": query,
        "results": [],
        "note": (
            f"DuckDuckGo rate limit reached after {_MAX_ATTEMPTS} attempts. "
            "Search results are unavailable for this query."
        ),
    }
