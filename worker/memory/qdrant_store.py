"""
QdrantMemoryStore — vector store backed by Qdrant.

Design notes:
- One collection shared across all workspaces; workspace_id is stored in the
  point payload and injected as a must-match filter on every search so tenants
  never see each other's memories.
- The collection is created on first use if it doesn't exist yet.
- Vector size is fixed at 1536 (OpenAI text-embedding-3-small / mock fallback).
- All Qdrant I/O errors are allowed to propagate so the caller (factory or
  executor hook) can decide whether to swallow or re-raise.
"""

from __future__ import annotations

import logging
from typing import Callable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from worker.memory.base import MemoryEntry, MemoryStore

logger = logging.getLogger(__name__)

_VECTOR_SIZE = 1536


class QdrantMemoryStore(MemoryStore):
    def __init__(
        self,
        url: str,
        collection: str,
        embedder: Callable[[str], list[float]],
    ) -> None:
        self._client    = QdrantClient(url=url, timeout=5)
        self._collection = collection
        self._embedder   = embedder
        self._ensure_collection()

    # ------------------------------------------------------------------
    # MemoryStore interface
    # ------------------------------------------------------------------

    def store(self, entry: MemoryEntry) -> None:
        vector = self._embedder(entry.content)
        self._client.upsert(
            collection_name=self._collection,
            points=[
                PointStruct(
                    id=entry.id,
                    vector=vector,
                    payload={
                        "workspace_id": entry.workspace_id,
                        "job_id":       entry.job_id,
                        "entry_type":   entry.entry_type,
                        "content":      entry.content,
                        "metadata":     entry.metadata,
                        "created_at":   entry.created_at,
                    },
                )
            ],
        )
        logger.debug(
            "memory stored",
            extra={
                "entry_id":   entry.id,
                "type":       entry.entry_type,
                "workspace":  entry.workspace_id,
            },
        )

    def search(
        self,
        workspace_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """
        Semantic search restricted to *workspace_id*.

        The workspace filter is applied server-side in Qdrant so no cross-tenant
        data ever travels over the wire to the application.
        """
        vector = self._embedder(query)
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="workspace_id",
                        match=MatchValue(value=workspace_id),
                    )
                ]
            ),
            limit=top_k,
        )
        entries = []
        for hit in hits:
            p = hit.payload or {}
            entries.append(
                MemoryEntry(
                    id=str(hit.id),
                    workspace_id=p.get("workspace_id", ""),
                    job_id=p.get("job_id", ""),
                    entry_type=p.get("entry_type", ""),
                    content=p.get("content", ""),
                    metadata=p.get("metadata", {}),
                    created_at=p.get("created_at", ""),
                )
            )
        logger.debug(
            "memory search returned %d hits",
            len(entries),
            extra={"workspace": workspace_id, "query": query[:80]},
        )
        return entries

    # ------------------------------------------------------------------
    # Collection bootstrap
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s'", self._collection)
