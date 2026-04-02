"""
Memory store factory — returns a process-level singleton.

When MEMORY_ENABLED=false (default) returns NullMemoryStore — zero overhead.
When MEMORY_ENABLED=true returns QdrantMemoryStore; connection is attempted
on first call and any Qdrant error is allowed to propagate so the operator
notices a misconfigured QDRANT_URL early.
"""

from __future__ import annotations

import logging

from worker.memory.base import MemoryStore

logger = logging.getLogger(__name__)

_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """Return the process-level MemoryStore singleton (lazy init)."""
    global _store
    if _store is None:
        _store = _build_store()
    return _store


def _build_store() -> MemoryStore:
    # Deferred import so we can mock settings in tests without side effects
    from worker.config import settings

    if not settings.memory_enabled:
        from worker.memory.null_store import NullMemoryStore
        logger.debug("Memory disabled — using NullMemoryStore")
        return NullMemoryStore()

    from worker.memory.embeddings import embed
    from worker.memory.qdrant_store import QdrantMemoryStore

    logger.info(
        "Memory enabled — connecting to Qdrant at %s (collection=%s)",
        settings.qdrant_url,
        settings.qdrant_collection,
    )
    return QdrantMemoryStore(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedder=embed,
    )


def reset_memory_store() -> None:
    """
    Reset the singleton — used in tests to inject a fresh / mocked store.
    Not intended for production use.
    """
    global _store
    _store = None
