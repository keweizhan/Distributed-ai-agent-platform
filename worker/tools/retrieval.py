"""
Retrieval tool — semantic search over ingested RAG documents.

Registered as "retrieval" in the tool registry.

The executor injects _workspace_id as a kwarg so each workspace only sees
its own documents.  The ** catch-all signature matches all other tools.
"""

from __future__ import annotations

import logging
from typing import Any

from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)

_rag_store = None  # QdrantRagStore, lazily initialised


def _get_rag_store():
    # Deferred imports so the tool registers even without qdrant_client installed.
    from worker.config import settings
    from worker.rag.qdrant_rag import QdrantRagStore

    global _rag_store
    if _rag_store is None:
        _rag_store = QdrantRagStore(
            url=settings.qdrant_url,
            collection=settings.rag_collection,
        )
    return _rag_store


@register_tool("retrieval")
def retrieval(
    query: str,
    top_k: int = 5,
    _workspace_id: str = "",
    **_: Any,
) -> dict[str, Any]:
    """
    Search ingested documents for chunks relevant to *query*.

    Args:
        query:          Natural-language search query.
        top_k:          Maximum number of chunks to return (default 5).
        _workspace_id:  Injected by the executor — tenant scope.

    Returns:
        {
            "query": str,
            "workspace_id": str,
            "chunks": [
                {
                    "document_id": str,
                    "title": str,
                    "chunk_index": int,
                    "text": str,
                    "score": float,
                }
            ]
        }
    """
    if not query or not query.strip():
        raise ToolError("retrieval: 'query' must be a non-empty string")

    if not _workspace_id:
        logger.warning("retrieval called without _workspace_id — results may be empty")

    from worker.memory.embeddings import embed
    query_vector = embed(query)
    store = _get_rag_store()
    chunks = store.search(
        workspace_id=_workspace_id,
        query_vector=query_vector,
        top_k=max(1, int(top_k)),
    )

    logger.info(
        "retrieval: found %d chunks for query=%r workspace=%s",
        len(chunks), query[:60], _workspace_id,
    )

    return {
        "query":        query,
        "workspace_id": _workspace_id,
        "chunks":       chunks,
    }
