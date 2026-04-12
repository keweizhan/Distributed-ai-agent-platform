"""
SQLAlchemy ORM models for the API service.
Schema is managed by Alembic migrations in api/migrations/versions/.
"""

import uuid

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Auth / multi-tenancy
# ---------------------------------------------------------------------------

class UserModel(Base):
    __tablename__ = "users"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email           = Column(String(255), nullable=False, unique=True)
    hashed_password = Column(Text, nullable=False)
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workspaces = relationship("WorkspaceModel", back_populates="owner")


class WorkspaceModel(Base):
    __tablename__ = "workspaces"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name       = Column(String(255), nullable=False)
    owner_id   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    owner = relationship("UserModel", back_populates="workspaces")
    jobs  = relationship("JobModel", back_populates="workspace")


# ---------------------------------------------------------------------------
# Jobs / tasks
# ---------------------------------------------------------------------------

class JobModel(Base):
    __tablename__ = "jobs"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    prompt       = Column(Text, nullable=False)
    status       = Column(
        Enum(
            "pending", "planning", "planned", "running", "succeeded", "failed", "cancelled",
            name="job_status",
        ),
        nullable=False,
        default="pending",
    )
    result     = Column(Text)
    error      = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workspace = relationship("WorkspaceModel", back_populates="jobs")
    tasks     = relationship("TaskModel", back_populates="job", cascade="all, delete-orphan")


class TaskModel(Base):
    __tablename__ = "tasks"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id          = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    parent_task_id  = Column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    step_id         = Column(Text, nullable=True)
    task_type       = Column(
        Enum("plan", "tool_call", "synthesis", name="task_type"),
        nullable=False,
    )
    name            = Column(String(255), nullable=False)
    description     = Column(Text)
    tool_name       = Column(String(128))
    tool_input      = Column(JSON)
    tool_output     = Column(JSON)
    dependencies    = Column(JSON, nullable=False, default=list)
    priority        = Column(Integer, nullable=False, default=0)
    status          = Column(
        Enum(
            "pending", "queued", "running", "succeeded", "failed", "skipped",
            name="task_status",
        ),
        nullable=False,
        default="pending",
    )
    error           = Column(Text)
    sequence        = Column(Integer, nullable=False, default=0)
    expected_output = Column(Text)
    attempt_count   = Column(Integer, nullable=False, default=0)
    started_at      = Column(DateTime(timezone=True), nullable=True)
    finished_at     = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job    = relationship("JobModel", back_populates="tasks")
    parent = relationship("TaskModel", remote_side="TaskModel.id", backref="children")


# ---------------------------------------------------------------------------
# RAG document metadata
# ---------------------------------------------------------------------------

class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('ingesting','ready','failed')", name="ck_documents_status"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title        = Column(String(500), nullable=False)
    chunk_count  = Column(Integer, nullable=False, default=0)
    status       = Column(String(20), nullable=False, default="ingesting")
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workspace = relationship("WorkspaceModel")
