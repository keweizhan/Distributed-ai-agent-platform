"""
Ingest / delete Celery tasks for RAG documents.

ingest_document  — chunk, embed, upsert to Qdrant; update DB status to ready/failed
delete_document  — delete all Qdrant vectors for a document_id + workspace_id
"""

from __future__ import annotations

import logging
import uuid

from shared.constants import TASK_DELETE_DOCUMENT, TASK_INGEST_DOCUMENT
from worker.celery_app import app
from worker.config import settings
from worker.db import get_sync_session
from worker.db.models import DocumentModel
from worker.memory.embeddings import embed
from worker.rag.chunker import chunk_text
from worker.rag.qdrant_rag import QdrantRagStore

logger = logging.getLogger(__name__)

_rag_store: QdrantRagStore | None = None


def _get_rag_store() -> QdrantRagStore:
    global _rag_store
    if _rag_store is None:
        _rag_store = QdrantRagStore(
            url=settings.qdrant_url,
            collection=settings.rag_collection,
        )
    return _rag_store


# ---------------------------------------------------------------------------
# ingest_document
# ---------------------------------------------------------------------------

@app.task(name=TASK_INGEST_DOCUMENT, bind=True, max_retries=2)
def ingest_document(
    self,
    document_id: str,
    workspace_id: str,
    title: str,
    content: str,
) -> dict:
    """
    Chunk, embed, and upsert a document into the RAG collection.
    Updates the documents table status to 'ready' on success, 'failed' on error.
    """
    logger.info(
        "ingest_document started",
        extra={"document_id": document_id, "workspace_id": workspace_id},
    )

    try:
        chunks = chunk_text(content)
        if not chunks:
            logger.warning("ingest_document: empty content", extra={"document_id": document_id})
            _set_document_status(document_id, "failed", chunk_count=0)
            return {"document_id": document_id, "chunk_count": 0}

        count = _get_rag_store().upsert_chunks(
            workspace_id=workspace_id,
            document_id=document_id,
            title=title,
            chunks=chunks,
            embedder=embed,
        )
        _set_document_status(document_id, "ready", chunk_count=count)
        logger.info(
            "ingest_document completed: %d chunks", count,
            extra={"document_id": document_id},
        )
        return {"document_id": document_id, "workspace_id": workspace_id, "chunk_count": count}

    except Exception as exc:
        logger.error("ingest_document failed: %s", exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10)
        _set_document_status(document_id, "failed")
        raise


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------

@app.task(name=TASK_DELETE_DOCUMENT, bind=True, max_retries=2)
def delete_document(self, document_id: str, workspace_id: str) -> dict:
    """
    Delete all Qdrant vectors for *document_id* within *workspace_id*.
    The DB row is already gone (deleted by the API); this cleans up Qdrant.
    """
    logger.info(
        "delete_document started",
        extra={"document_id": document_id, "workspace_id": workspace_id},
    )
    try:
        _get_rag_store().delete_by_document_id(
            workspace_id=workspace_id,
            document_id=document_id,
        )
        return {"document_id": document_id, "deleted": True}
    except Exception as exc:
        logger.error("delete_document failed: %s", exc, exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10)
        raise


# ---------------------------------------------------------------------------
# Helper — update document status in PostgreSQL
# ---------------------------------------------------------------------------

def _set_document_status(
    document_id: str,
    status: str,
    chunk_count: int | None = None,
) -> None:
    """Update the documents table row.  Silently logs on any DB error."""
    try:
        doc_uuid = uuid.UUID(document_id)
        with get_sync_session() as session:
            doc = session.get(DocumentModel, doc_uuid)
            if doc is None:
                logger.warning("_set_document_status: document %s not found in DB", document_id)
                return
            doc.status = status
            if chunk_count is not None:
                doc.chunk_count = chunk_count
            session.commit()
    except Exception:
        logger.warning("_set_document_status: DB update failed", exc_info=True)
