"""
Documents router — knowledge base ingestion and management.

POST   /documents          — ingest a new document (records metadata in DB, vectors in Qdrant)
GET    /documents          — list all documents for the current workspace
DELETE /documents/{doc_id} — delete a document (removes DB row, schedules Qdrant cleanup)

All endpoints require a JWT bearer token and are workspace-scoped.
"""

from __future__ import annotations

import io
import uuid

from celery import Celery
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import get_current_workspace
from api.config import settings
from api.db.models import DocumentModel, WorkspaceModel
from api.db.session import get_db
from api.schemas.document import DocumentRecord, IngestDocumentRequest, IngestDocumentResponse
from shared.constants import QUEUE_INGEST, TASK_DELETE_DOCUMENT, TASK_INGEST_DOCUMENT

router = APIRouter(prefix="/documents", tags=["documents"])

_celery: Celery | None = None


def _get_celery() -> Celery:
    global _celery
    if _celery is None:
        _celery = Celery(
            broker=settings.celery_broker_url,
            backend=settings.celery_result_backend,
        )
    return _celery


# ---------------------------------------------------------------------------
# POST /documents  — ingest a new document
# ---------------------------------------------------------------------------

@router.post("", response_model=IngestDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_document(
    body: IngestDocumentRequest,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> IngestDocumentResponse:
    """
    Queue a document for chunking, embedding, and Qdrant upsert.

    A metadata row is written to PostgreSQL immediately so the document
    appears in GET /documents right away with status='ingesting'.
    The worker updates status to 'ready' (or 'failed') when processing completes.
    """
    chunk_count_estimate = max(1, (len(body.content) + 399) // 400)

    doc = DocumentModel(
        workspace_id=workspace.id,
        title=body.title,
        chunk_count=chunk_count_estimate,
        status="ingesting",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    _get_celery().send_task(
        TASK_INGEST_DOCUMENT,
        kwargs={
            "document_id":  str(doc.id),
            "workspace_id": str(workspace.id),
            "title":        body.title,
            "content":      body.content,
        },
        queue=QUEUE_INGEST,
    )

    return IngestDocumentResponse(
        document_id=str(doc.id),
        status="ingesting",
        title=body.title,
        chunk_count=chunk_count_estimate,
    )


# ---------------------------------------------------------------------------
# POST /documents/upload  — ingest a file (.txt / .md / .pdf)
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_EXTS = {"txt", "md", "pdf"}


def _extract_text(raw: bytes, ext: str, filename: str) -> str:
    """Return plain text from the uploaded bytes.  Raises HTTPException on failure."""
    if ext in ("txt", "md"):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    # PDF
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages).strip()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"PDF parsing failed for '{filename}': {exc}",
        ) from exc

    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No text could be extracted from '{filename}'. "
                "The file may be a scanned image — OCR is not supported yet."
            ),
        )
    return text


@router.post(
    "/upload",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a file (.txt, .md, .pdf) into the knowledge base",
)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> IngestDocumentResponse:
    """
    Upload a file for RAG ingestion.  Text is extracted server-side, then the
    content is chunked, embedded, and upserted through the same Celery pipeline
    used by POST /documents.

    Supported types: .txt, .md, .pdf (text-layer only; no OCR).
    Max file size: 10 MB.
    """
    raw = await file.read()

    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large. Maximum size is 10 MB.",
        )

    filename = file.filename or "untitled"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type '.{ext}'. Allowed: .txt, .md, .pdf",
        )

    content = _extract_text(raw, ext, filename)
    if not content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{filename}' appears to be empty.",
        )

    doc_title = title.strip() or filename.rsplit(".", 1)[0]
    chunk_count_estimate = max(1, (len(content) + 399) // 400)

    doc = DocumentModel(
        workspace_id=workspace.id,
        title=doc_title,
        chunk_count=chunk_count_estimate,
        status="ingesting",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    _get_celery().send_task(
        TASK_INGEST_DOCUMENT,
        kwargs={
            "document_id":  str(doc.id),
            "workspace_id": str(workspace.id),
            "title":        doc_title,
            "content":      content,
        },
        queue=QUEUE_INGEST,
    )

    return IngestDocumentResponse(
        document_id=str(doc.id),
        status="ingesting",
        title=doc_title,
        chunk_count=chunk_count_estimate,
    )


# ---------------------------------------------------------------------------
# GET /documents  — list documents for the current workspace
# ---------------------------------------------------------------------------

@router.get("", response_model=list[DocumentRecord])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> list[DocumentModel]:
    result = await db.execute(
        select(DocumentModel)
        .where(DocumentModel.workspace_id == workspace.id)
        .order_by(DocumentModel.created_at.desc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# DELETE /documents/{doc_id}  — remove a document
# ---------------------------------------------------------------------------

@router.delete("/{doc_id}", status_code=status.HTTP_200_OK)
async def delete_document(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace: WorkspaceModel = Depends(get_current_workspace),
) -> dict:
    """
    Delete a document from the knowledge base.

    The metadata row is removed from PostgreSQL immediately.
    A Celery task is dispatched to delete the corresponding vectors from Qdrant.
    """
    result = await db.execute(
        select(DocumentModel).where(
            DocumentModel.id == doc_id,
            DocumentModel.workspace_id == workspace.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    await db.delete(doc)
    await db.commit()

    _get_celery().send_task(
        TASK_DELETE_DOCUMENT,
        kwargs={
            "document_id":  str(doc_id),
            "workspace_id": str(workspace.id),
        },
        queue=QUEUE_INGEST,
    )

    return {"ok": True}
