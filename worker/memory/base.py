"""
Memory abstraction — MemoryEntry dataclass and MemoryStore ABC.

Implementations:
  NullMemoryStore  — all methods are no-ops (MEMORY_ENABLED=false)
  QdrantMemoryStore — persists embeddings in Qdrant (MEMORY_ENABLED=true)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MemoryEntry:
    """A single piece of stored memory, associated with a workspace and job."""

    workspace_id: str               # tenant isolation key
    job_id:       str               # which job produced this entry
    entry_type:   str               # "tool_output" | "job_result"
    content:      str               # text that was embedded (truncated if long)
    metadata:     dict              # supplementary data (tool_name, step_id, …)

    # Auto-generated fields
    id:         str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class MemoryStore(ABC):
    """Minimal interface — store an entry, search by semantic similarity."""

    @abstractmethod
    def store(self, entry: MemoryEntry) -> None:
        """Persist *entry* and its embedding."""

    @abstractmethod
    def search(
        self,
        workspace_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """
        Return at most *top_k* entries most relevant to *query*, restricted to
        entries whose workspace_id matches the caller's workspace (tenant isolation).
        """
