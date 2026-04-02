"""
NullMemoryStore — used when MEMORY_ENABLED=false.

All operations are no-ops so the rest of the codebase can call memory methods
unconditionally without checking the feature flag at every call site.
"""

from __future__ import annotations

from worker.memory.base import MemoryEntry, MemoryStore


class NullMemoryStore(MemoryStore):
    def store(self, entry: MemoryEntry) -> None:
        pass

    def search(self, workspace_id: str, query: str, top_k: int = 5) -> list[MemoryEntry]:
        return []
