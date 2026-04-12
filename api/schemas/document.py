"""Pydantic schemas for document ingestion and listing."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class IngestDocumentRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="Document title")
    content: str = Field(..., min_length=1, description="Full document text to ingest")


class IngestDocumentResponse(BaseModel):
    document_id: str
    status: str = "ingesting"
    title: str
    chunk_count: int


class DocumentRecord(BaseModel):
    """One row returned by GET /documents."""
    id: UUID
    title: str
    chunk_count: int
    status: str        # ingesting | ready | failed
    created_at: datetime

    model_config = {"from_attributes": True}
