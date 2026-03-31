"""
SQLAlchemy ORM models for the worker service.
Kept separate from api/db/models.py so the worker has zero dependency on the api package.
infra/init.sql is the single source of truth for the DB schema.
"""

import uuid

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class JobModel(Base):
    __tablename__ = "jobs"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt     = Column(Text, nullable=False)
    status     = Column(
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

    tasks = relationship("TaskModel", back_populates="job", cascade="all, delete-orphan")


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
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job    = relationship("JobModel", back_populates="tasks")
    parent = relationship("TaskModel", remote_side="TaskModel.id", backref="children")
