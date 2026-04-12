"""
Retrieval tool — semantic search over ingested RAG documents.

Registered as "retrieval" in the tool registry.

The executor injects _workspace_id as a kwarg so each workspace only sees
its own documents.  The ** catch-all signature matches all other tools.

Reranking
---------
Qdrant returns candidates ranked purely by cosine similarity.  A lightweight
second-pass reranker re-scores each candidate using:

    combined = 0.7 * vector_score + 0.3 * keyword_overlap

where keyword_overlap is the fraction of meaningful query tokens that appear
in the chunk text (overlap coefficient, stopwords removed).  The vector score
dominates; keyword overlap breaks ties and penalises chunks that happen to be
close in embedding space but share no lexical content with the query.

We over-fetch (top_k * 3, capped at 20) so the reranker has a larger pool
without changing what callers receive.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)

_rag_store = None  # QdrantRagStore, lazily initialised

# How many extra candidates to fetch before reranking.
_RERANK_MULTIPLIER = 3
_RERANK_POOL_CAP   = 20

# Combined-score threshold below which a chunk is considered irrelevant.
_DROP_THRESHOLD = 0.10

# Common English stopwords that add no signal for keyword matching.
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can", "shall",
    "not", "no", "nor", "so", "yet", "both", "either", "just", "than",
    "too", "very", "it", "its", "this", "that", "these", "those",
    "he", "she", "they", "we", "you", "i", "me", "my", "our", "their",
    "what", "which", "who", "how", "when", "where", "why", "all", "each",
    "any", "more", "most", "also", "about", "into", "up", "out", "over",
})


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase alphabetic tokens of length >= 2, stopwords removed."""
    return frozenset(
        t for t in re.findall(r"\b[a-z]{2,}\b", text.lower())
        if t not in _STOPWORDS
    )


def _rerank(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """
    Re-score *chunks* with a combined vector + keyword signal and return
    the best *top_k*, dropping anything below _DROP_THRESHOLD.

    Always returns at least one chunk (the highest scorer) so the caller
    never receives an empty list when Qdrant returned results.
    """
    if len(chunks) <= top_k:
        # Pool is already small enough — still score so we can drop weak hits.
        pass

    query_tokens = _tokenize(query)

    scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        vector_score = float(chunk.get("score", 0.0))

        if query_tokens:
            chunk_tokens = _tokenize(chunk.get("text", ""))
            # Overlap coefficient: |Q ∩ C| / |Q|
            # Measures how much of the query is covered, regardless of chunk length.
            keyword_score = (
                len(query_tokens & chunk_tokens) / len(query_tokens)
                if chunk_tokens else 0.0
            )
        else:
            keyword_score = 0.0

        combined = 0.7 * vector_score + 0.3 * keyword_score
        scored.append((combined, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top_k, then drop anything clearly irrelevant.
    candidates = scored[:top_k]
    result = [c for score, c in candidates if score >= _DROP_THRESHOLD]

    # Guarantee at least one result so synthesis always has something to judge.
    if not result:
        result = [candidates[0][1]]

    return result


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

    # Over-fetch to give the reranker a larger candidate pool.
    pool_size = min(max(1, int(top_k)) * _RERANK_MULTIPLIER, _RERANK_POOL_CAP)
    candidates = store.search(
        workspace_id=_workspace_id,
        query_vector=query_vector,
        top_k=pool_size,
    )

    chunks = _rerank(query=query, chunks=candidates, top_k=max(1, int(top_k)))

    logger.info(
        "retrieval: pool=%d reranked=%d top_k=%d query=%r workspace=%s",
        len(candidates), len(chunks), top_k, query[:60], _workspace_id,
    )

    return {
        "query":        query,
        "workspace_id": _workspace_id,
        "chunks":       chunks,
    }
