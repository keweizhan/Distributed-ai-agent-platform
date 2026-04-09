"""
Tests for M7 — memory and retrieval layer.

Covers:
  1. MemoryEntry construction
  2. NullMemoryStore — no-ops
  3. Mock embedder — deterministic, unit-norm
  4. QdrantMemoryStore — store and search (mocked Qdrant client)
  5. Workspace isolation — search filter includes workspace_id
  6. get_memory_store factory — returns NullMemoryStore when disabled
  7. Planner memory context injection
  8. Executor memory hooks — store called on tool_call success
  9. Executor synthesis enrichment — context injected into output
 10. Fire-and-forget safety — memory errors never fail the task
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from unittest.mock import ANY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. MemoryEntry
# ---------------------------------------------------------------------------

class TestMemoryEntry:
    def test_auto_generates_id_and_created_at(self) -> None:
        from worker.memory.base import MemoryEntry
        e = MemoryEntry(
            workspace_id="ws-1",
            job_id="job-1",
            entry_type="job_result",
            content="some content",
            metadata={},
        )
        assert uuid.UUID(e.id)  # valid UUID
        assert e.created_at    # non-empty ISO string

    def test_explicit_id_preserved(self) -> None:
        from worker.memory.base import MemoryEntry
        fixed_id = str(uuid.uuid4())
        e = MemoryEntry(
            workspace_id="ws-1",
            job_id="job-1",
            entry_type="tool_output",
            content="output",
            metadata={"tool": "web_search"},
            id=fixed_id,
        )
        assert e.id == fixed_id


# ---------------------------------------------------------------------------
# 2. NullMemoryStore
# ---------------------------------------------------------------------------

class TestNullMemoryStore:
    def test_store_is_noop(self) -> None:
        from worker.memory.base import MemoryEntry
        from worker.memory.null_store import NullMemoryStore
        store = NullMemoryStore()
        entry = MemoryEntry(
            workspace_id="ws", job_id="j", entry_type="job_result",
            content="x", metadata={},
        )
        store.store(entry)  # must not raise

    def test_search_returns_empty_list(self) -> None:
        from worker.memory.null_store import NullMemoryStore
        store = NullMemoryStore()
        results = store.search("ws-1", "some query")
        assert results == []


# ---------------------------------------------------------------------------
# 3. Mock embedder
# ---------------------------------------------------------------------------

class TestMockEmbedder:
    def test_returns_correct_dimension(self) -> None:
        from worker.memory.embeddings import _mock_embed
        vec = _mock_embed("hello world")
        assert len(vec) == 1536

    def test_is_unit_norm(self) -> None:
        import math
        from worker.memory.embeddings import _mock_embed
        vec = _mock_embed("hello world")
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_is_deterministic(self) -> None:
        from worker.memory.embeddings import _mock_embed
        assert _mock_embed("same text") == _mock_embed("same text")

    def test_different_texts_differ(self) -> None:
        from worker.memory.embeddings import _mock_embed
        assert _mock_embed("text A") != _mock_embed("text B")

    def test_embed_uses_mock_when_no_api_key(self) -> None:
        from worker.memory.embeddings import _mock_embed, embed
        with patch("worker.config.settings") as mock_settings:
            mock_settings.openai_api_key = "sk-not-set"
            result = embed("test")
        assert len(result) == 1536


# ---------------------------------------------------------------------------
# 4 & 5. QdrantMemoryStore with mocked client
# ---------------------------------------------------------------------------

def _make_qdrant_store(mock_client):
    """Helper: build a QdrantMemoryStore backed by a mock Qdrant client."""
    from worker.memory.qdrant_store import QdrantMemoryStore
    from worker.memory.embeddings import _mock_embed

    # Patch QdrantClient constructor so no real connection is made
    with patch("worker.memory.qdrant_store.QdrantClient", return_value=mock_client):
        store = QdrantMemoryStore(
            url="http://qdrant:6333",
            collection="test_memory",
            embedder=_mock_embed,
        )
    return store


class TestQdrantMemoryStore:
    def _build_client(self) -> MagicMock:
        client = MagicMock()
        # get_collections returns empty — triggers create_collection
        client.get_collections.return_value.collections = []
        return client

    def test_creates_collection_on_init(self) -> None:
        client = self._build_client()
        _make_qdrant_store(client)
        client.create_collection.assert_called_once_with(
            collection_name="test_memory",
            vectors_config=ANY,
        )

    def test_skips_create_if_collection_exists(self) -> None:
        client = self._build_client()
        existing = MagicMock()
        existing.name = "test_memory"
        client.get_collections.return_value.collections = [existing]
        _make_qdrant_store(client)
        client.create_collection.assert_not_called()

    def test_store_calls_upsert_with_workspace_id_in_payload(self) -> None:
        from worker.memory.base import MemoryEntry
        client = self._build_client()
        store = _make_qdrant_store(client)

        entry = MemoryEntry(
            workspace_id="ws-abc",
            job_id="job-1",
            entry_type="tool_output",
            content="web search result",
            metadata={"tool_name": "web_search"},
        )
        store.store(entry)

        client.upsert.assert_called_once()
        call_kwargs = client.upsert.call_args.kwargs
        assert call_kwargs["collection_name"] == "test_memory"
        point = call_kwargs["points"][0]
        assert point.payload["workspace_id"] == "ws-abc"
        assert point.payload["entry_type"] == "tool_output"

    def test_search_applies_workspace_filter(self) -> None:
        """search() must pass workspace_id as a must-match filter."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        client = self._build_client()
        client.search.return_value = []
        store = _make_qdrant_store(client)

        store.search("ws-xyz", "find similar results", top_k=3)

        client.search.assert_called_once()
        call_kwargs = client.search.call_args.kwargs
        assert call_kwargs["collection_name"] == "test_memory"
        assert call_kwargs["limit"] == 3
        filt: Filter = call_kwargs["query_filter"]
        condition: FieldCondition = filt.must[0]
        assert condition.key == "workspace_id"
        assert condition.match.value == "ws-xyz"

    def test_search_returns_memory_entries(self) -> None:
        from worker.memory.base import MemoryEntry
        entry_id = str(uuid.uuid4())
        hit = MagicMock()
        hit.id = entry_id
        hit.payload = {
            "workspace_id": "ws-1",
            "job_id": "job-x",
            "entry_type": "job_result",
            "content": "the answer",
            "metadata": {},
            "created_at": _now().isoformat(),
        }

        client = self._build_client()
        client.search.return_value = [hit]
        store = _make_qdrant_store(client)

        results = store.search("ws-1", "query")
        assert len(results) == 1
        assert isinstance(results[0], MemoryEntry)
        assert results[0].content == "the answer"
        assert results[0].workspace_id == "ws-1"

    def test_different_workspaces_get_isolated_filters(self) -> None:
        """Two searches for different workspaces must use different filters."""
        client = self._build_client()
        client.search.return_value = []
        store = _make_qdrant_store(client)

        store.search("workspace-A", "query")
        store.search("workspace-B", "query")

        calls = client.search.call_args_list
        ws_values = [c.kwargs["query_filter"].must[0].match.value for c in calls]
        assert ws_values == ["workspace-A", "workspace-B"]


# ---------------------------------------------------------------------------
# 6. Factory — NullMemoryStore when disabled
# ---------------------------------------------------------------------------

class TestMemoryFactory:
    def test_returns_null_store_when_disabled(self) -> None:
        from worker.memory.factory import reset_memory_store
        from worker.memory.null_store import NullMemoryStore

        reset_memory_store()
        with patch("worker.config.settings") as mock_settings:
            mock_settings.memory_enabled = False
            from worker.memory.factory import _build_store
            store = _build_store()
        assert isinstance(store, NullMemoryStore)

    def test_returns_qdrant_store_when_enabled(self) -> None:
        from worker.memory.factory import reset_memory_store

        reset_memory_store()
        mock_client = MagicMock()
        mock_client.get_collections.return_value.collections = []

        with patch("worker.config.settings") as mock_settings:
            mock_settings.memory_enabled = True
            mock_settings.qdrant_url = "http://qdrant:6333"
            mock_settings.qdrant_collection = "agent_memory"
            mock_settings.openai_api_key = "sk-not-set"

            with patch("worker.memory.qdrant_store.QdrantClient", return_value=mock_client):
                from worker.memory.factory import _build_store
                store = _build_store()

        from worker.memory.qdrant_store import QdrantMemoryStore
        assert isinstance(store, QdrantMemoryStore)


# ---------------------------------------------------------------------------
# 7. Planner memory context injection
# ---------------------------------------------------------------------------

class TestPlannerMemoryContext:
    def test_build_user_prompt_without_context(self) -> None:
        with patch("worker.planner.prompt.list_tools", return_value=["web_search"]):
            from worker.planner.prompt import build_user_prompt
            prompt = build_user_prompt("do something")
        assert "RELEVANT PAST RESULTS" not in prompt
        assert "do something" in prompt

    def test_build_user_prompt_with_context_injects_snippets(self) -> None:
        with patch("worker.planner.prompt.list_tools", return_value=["web_search"]):
            from worker.planner.prompt import build_user_prompt
            prompt = build_user_prompt("do something", context=["Past result A", "Past result B"])
        assert "RELEVANT PAST RESULTS" in prompt
        assert "Past result A" in prompt
        assert "Past result B" in prompt

    def test_mock_planner_accepts_context_kwarg(self) -> None:
        from worker.planner.mock import MockPlanner
        planner = MockPlanner()
        plan = planner.plan(uuid.uuid4(), "test prompt", context=["ctx1", "ctx2"])
        assert len(plan.steps) == 3  # mock ignores context but still returns a plan

    def test_get_memory_context_returns_empty_when_no_workspace(self) -> None:
        from worker.tasks.planner import _get_memory_context
        result = _get_memory_context(None, "some prompt")
        assert result == []

    def test_get_memory_context_returns_only_job_results(self) -> None:
        from worker.memory.base import MemoryEntry
        from worker.tasks.planner import _get_memory_context

        tool_entry = MemoryEntry(
            workspace_id="ws", job_id="j1",
            entry_type="tool_output", content="tool output", metadata={},
        )
        job_entry = MemoryEntry(
            workspace_id="ws", job_id="j2",
            entry_type="job_result", content="job result", metadata={},
        )

        mock_store = MagicMock()
        mock_store.search.return_value = [tool_entry, job_entry]

        with patch("worker.tasks.planner.get_memory_store", return_value=mock_store):
            result = _get_memory_context("ws-1", "some prompt")

        # Only job_result entries are returned as context
        assert result == ["job result"]

    def test_get_memory_context_swallows_exceptions(self) -> None:
        from worker.tasks.planner import _get_memory_context
        with patch("worker.tasks.planner.get_memory_store", side_effect=RuntimeError("qdrant down")):
            result = _get_memory_context("ws-1", "prompt")
        assert result == []


# ---------------------------------------------------------------------------
# 8. Executor — store called on tool_call success
# ---------------------------------------------------------------------------

class TestExecutorMemoryStore:
    def test_try_store_task_memory_calls_store(self) -> None:
        from worker.tasks.executor import _try_store_task_memory

        task = MagicMock()
        task.id = uuid.uuid4()
        task.job_id = uuid.uuid4()
        task.tool_name = "web_search"
        task.name = "Search step"
        task.description = "Search for things"
        task.step_id = "search"

        mock_store = MagicMock()
        with patch("worker.tasks.executor.get_memory_store", return_value=mock_store):
            _try_store_task_memory(task, {"results": ["r1", "r2"]}, "ws-abc")

        mock_store.store.assert_called_once()
        stored = mock_store.store.call_args[0][0]
        assert stored.workspace_id == "ws-abc"
        assert stored.entry_type == "tool_output"
        assert "web_search" in stored.content
        assert stored.job_id == str(task.job_id)

    def test_try_store_task_memory_swallows_exceptions(self) -> None:
        from worker.tasks.executor import _try_store_task_memory

        task = MagicMock()
        task.id = uuid.uuid4()
        task.job_id = uuid.uuid4()
        task.tool_name = "web_search"
        task.name = "Search"
        task.description = None
        task.step_id = "search"

        with patch("worker.tasks.executor.get_memory_store", side_effect=RuntimeError("bang")):
            _try_store_task_memory(task, {}, "ws-1")  # must not raise

    def test_try_store_job_memory_calls_store(self) -> None:
        from worker.tasks.executor import _try_store_job_memory

        job = MagicMock()
        job.id = uuid.uuid4()
        job.prompt = "research transformers"
        job.result = "Here are the findings"

        mock_store = MagicMock()
        with patch("worker.tasks.executor.get_memory_store", return_value=mock_store):
            _try_store_job_memory(job, "ws-xyz")

        mock_store.store.assert_called_once()
        stored = mock_store.store.call_args[0][0]
        assert stored.entry_type == "job_result"
        assert stored.workspace_id == "ws-xyz"
        assert "research transformers" in stored.content
        assert "Here are the findings" in stored.content

    def test_try_store_job_memory_swallows_exceptions(self) -> None:
        from worker.tasks.executor import _try_store_job_memory

        job = MagicMock()
        job.id = uuid.uuid4()
        job.prompt = "p"
        job.result = "r"

        with patch("worker.tasks.executor.get_memory_store", side_effect=Exception("bang")):
            _try_store_job_memory(job, "ws-1")  # must not raise


# ---------------------------------------------------------------------------
# 9. Executor synthesis enrichment
# ---------------------------------------------------------------------------

class TestSynthesisEnrichment:
    def test_invoke_tool_synthesis_includes_memory_context(self) -> None:
        from worker.tasks.executor import _invoke_tool

        task = MagicMock()
        task.task_type = "synthesis"

        output = _invoke_tool(task, memory_context=["fact 1", "fact 2"])
        assert output["memory_context"] == ["fact 1", "fact 2"]

    def test_invoke_tool_synthesis_without_context(self) -> None:
        from worker.tasks.executor import _invoke_tool

        task = MagicMock()
        task.task_type = "synthesis"

        output = _invoke_tool(task, memory_context=None)
        assert "memory_context" not in output
        assert "final_answer" in output

    def test_retrieve_memory_context_returns_entry_contents(self) -> None:
        from worker.memory.base import MemoryEntry
        from worker.tasks.executor import _retrieve_memory_context

        entries = [
            MemoryEntry(workspace_id="ws", job_id="j", entry_type="job_result",
                        content="content A", metadata={}),
            MemoryEntry(workspace_id="ws", job_id="j", entry_type="tool_output",
                        content="content B", metadata={}),
        ]
        mock_store = MagicMock()
        mock_store.search.return_value = entries

        task = MagicMock()
        task.description = "synthesise findings"
        job = MagicMock()
        job.prompt = "original prompt"

        with patch("worker.tasks.executor.get_memory_store", return_value=mock_store):
            result = _retrieve_memory_context("ws-1", task, job)

        assert result == ["content A", "content B"]
        mock_store.search.assert_called_once_with("ws-1", query="synthesise findings", top_k=3)

    def test_retrieve_memory_context_swallows_exceptions(self) -> None:
        from worker.tasks.executor import _retrieve_memory_context

        task = MagicMock()
        task.description = "synthesise"
        job = MagicMock()
        job.prompt = "p"

        with patch("worker.tasks.executor.get_memory_store", side_effect=RuntimeError("boom")):
            result = _retrieve_memory_context("ws-1", task, job)

        assert result == []


# ---------------------------------------------------------------------------
# 10. Workspace isolation contract
# ---------------------------------------------------------------------------

class TestWorkspaceIsolation:
    def test_store_records_workspace_id(self) -> None:
        """Every stored entry must carry the correct workspace_id in its payload."""
        from worker.memory.base import MemoryEntry
        client = MagicMock()
        client.get_collections.return_value.collections = []

        from worker.memory.qdrant_store import QdrantMemoryStore
        from worker.memory.embeddings import _mock_embed

        with patch("worker.memory.qdrant_store.QdrantClient", return_value=client):
            store = QdrantMemoryStore("url", "col", _mock_embed)

        entry = MemoryEntry(
            workspace_id="tenant-A",
            job_id="j1", entry_type="job_result",
            content="result", metadata={},
        )
        store.store(entry)
        payload = client.upsert.call_args.kwargs["points"][0].payload
        assert payload["workspace_id"] == "tenant-A"

    def test_search_never_omits_workspace_filter(self) -> None:
        """Even with top_k=1 and trivial query, filter must always be present."""
        from qdrant_client.models import Filter
        client = MagicMock()
        client.get_collections.return_value.collections = []
        client.search.return_value = []

        from worker.memory.qdrant_store import QdrantMemoryStore
        from worker.memory.embeddings import _mock_embed

        with patch("worker.memory.qdrant_store.QdrantClient", return_value=client):
            store = QdrantMemoryStore("url", "col", _mock_embed)

        store.search("tenant-B", "x", top_k=1)
        filt = client.search.call_args.kwargs["query_filter"]
        assert isinstance(filt, Filter)
        assert filt.must[0].match.value == "tenant-B"
