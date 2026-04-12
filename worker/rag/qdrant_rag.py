"""
QdrantRagStore — vector store for RAG documents.

Separate from the agent_memory collection so document chunks and job memories
never mix payloads or pollute each other's search results.

Collection: rag_documents (configurable via settings.rag_collection)
Payload per point:
    workspace_id  — tenant isolation, injected as must-match filter on search
    document_id   — UUID string identifying the source document
    title         — document title for display in retrieval results
    chunk_index   — 0-based position of this chunk in the source document
    text          — the raw chunk text

Point IDs are deterministic: uuid5(NAMESPACE_URL, "{document_id}:{chunk_index}")
so re-ingesting the same document is idempotent (upsert overwrites).
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

logger = logging.getLogger(__name__)

_VECTOR_SIZE = 1536


class QdrantRagStore:
    def __init__(self, url: str, collection: str) -> None:
        self._client = QdrantClient(url=url, timeout=10)
        self._collection = collection
        self._ensure_collection()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        workspace_id: str,
        document_id: str,
        title: str,
        chunks: list[str],
        embedder,  # Callable[[str], list[float]]
    ) -> int:
        """
        Embed and upsert all *chunks* for a document.
        Returns the number of chunks upserted.
        """
        if not chunks:
            return 0

        points: list[PointStruct] = []
        for i, text in enumerate(chunks):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{i}"))
            vector = embedder(text)
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "workspace_id": workspace_id,
                        "document_id":  document_id,
                        "title":        title,
                        "chunk_index":  i,
                        "text":         text,
                    },
                )
            )

        self._client.upsert(collection_name=self._collection, points=points)
        logger.info(
            "upserted %d chunks for document %s (workspace=%s)",
            len(points), document_id, workspace_id,
        )
        return len(points)

    def search(
        self,
        workspace_id: str,
        query_vector: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        """
        Return the top-k most similar chunks for *workspace_id*.

        Each result dict:
            document_id, title, chunk_index, text, score
        """
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=query_vector,
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

        results = []
        for hit in hits:
            p = hit.payload or {}
            results.append({
                "document_id": p.get("document_id", ""),
                "title":       p.get("title", ""),
                "chunk_index": p.get("chunk_index", 0),
                "text":        p.get("text", ""),
                "score":       round(hit.score, 4),
            })
        return results

    def delete_by_document_id(self, workspace_id: str, document_id: str) -> None:
        """
        Delete all vector points belonging to *document_id* in *workspace_id*.

        Both fields are required so a compromised document_id cannot reach
        vectors from another workspace.
        """
        self._client.delete(
            collection_name=self._collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="document_id",  match=MatchValue(value=document_id)),
                        FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id)),
                    ]
                )
            ),
        )
        logger.info(
            "deleted Qdrant vectors for document %s (workspace=%s)",
            document_id, workspace_id,
        )

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
            logger.info("Created Qdrant RAG collection '%s'", self._collection)
