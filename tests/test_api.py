"""
Integration-style tests for the Jobs API (M5).

Uses FastAPI's synchronous TestClient with mocked DB sessions so no
running Postgres or Redis is required.

Test coverage:
  1. POST /jobs          — create a job
  2. GET  /jobs/{id}     — fetch job detail
  3. POST /jobs/{id}/cancel — cancel a job (valid + invalid transitions)
  4. GET  /jobs/{id}/tasks/{tid} — task detail endpoint
  5. GET  /health        — liveness probe
  6. GET  /ready         — readiness probe (mocked DB)
  7. GET  /metrics       — Prometheus scrape endpoint
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# App + dependency override setup
# ---------------------------------------------------------------------------

import uuid as _uuid

from api.auth.dependencies import get_current_workspace
from api.db.session import get_db
from api.main import app


def _make_workspace_mock() -> MagicMock:
    w = MagicMock()
    w.id = _uuid.uuid4()
    w.name = "test-workspace"
    w.owner_id = _uuid.uuid4()
    return w


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job_model(
    *,
    job_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    status: str = "running",
    prompt: str = "test prompt",
    result: str | None = None,
    error: str | None = None,
) -> MagicMock:
    j = MagicMock()
    j.id = job_id or uuid.uuid4()
    j.workspace_id = workspace_id or uuid.uuid4()
    j.prompt = prompt
    j.status = status
    j.result = result
    j.error = error
    j.created_at = _now()
    j.updated_at = _now()
    j.tasks = []
    return j


def _make_task_model(
    *,
    task_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    step_id: str = "search",
    task_type: str = "tool_call",
    name: str = "Search",
    tool_name: str | None = "web_search",
    tool_input: dict | None = None,
    tool_output: dict | None = None,
    status: str = "succeeded",
) -> MagicMock:
    t = MagicMock()
    t.id = task_id or uuid.uuid4()
    t.job_id = job_id or uuid.uuid4()
    t.step_id = step_id
    t.task_type = task_type
    t.name = name
    t.description = None
    t.tool_name = tool_name
    t.tool_input = tool_input or {"query": "test"}
    t.tool_output = tool_output or {"results": []}
    t.dependencies = []
    t.priority = 0
    t.status = status
    t.error = None
    t.sequence = 0
    t.expected_output = None
    t.attempt_count = 1
    t.started_at = _now()
    t.finished_at = _now()
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """
    TestClient with mocked DB session and workspace auth bypassed.
    All job endpoints require a workspace — we inject a fixed mock here so
    existing tests don't need to carry real JWTs.
    """
    mock_session = AsyncMock()
    # Default: db.get() returns None (tests override per-case)
    mock_session.get.return_value = None
    # Default: db.execute().scalar_one_or_none() returns None
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_execute_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_execute_result

    workspace = _make_workspace_mock()

    async def _override():
        yield mock_session

    async def _workspace_override():
        return workspace

    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_current_workspace] = _workspace_override
    with TestClient(app) as c:
        yield c, mock_session
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. POST /jobs
# ---------------------------------------------------------------------------

class TestCreateJob:
    def test_returns_201_with_job_fields(self, client) -> None:
        c, session = client
        job = _make_job_model(status="pending")
        session.refresh = AsyncMock()

        # commit → refresh → return job
        async def _refresh(obj):
            obj.id = job.id
            obj.status = job.status
            obj.created_at = job.created_at
            obj.updated_at = job.updated_at

        session.refresh.side_effect = _refresh

        with patch("api.routers.jobs.get_celery") as mock_celery:
            mock_celery.return_value.send_task = MagicMock()
            resp = c.post("/jobs", json={"prompt": "hello world"})

        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["status"] == "pending"

    def test_empty_prompt_returns_422(self, client) -> None:
        c, _ = client
        resp = c.post("/jobs", json={"prompt": ""})
        assert resp.status_code == 422

    def test_celery_task_is_enqueued(self, client) -> None:
        c, session = client
        async def _refresh(obj):
            obj.id = uuid.uuid4()
            obj.status = "pending"
            obj.created_at = _now()
            obj.updated_at = _now()
        session.refresh.side_effect = _refresh

        with patch("api.routers.jobs.get_celery") as mock_celery:
            mock_send = MagicMock()
            mock_celery.return_value.send_task = mock_send
            c.post("/jobs", json={"prompt": "do something"})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert "planner" in str(call_kwargs)


# ---------------------------------------------------------------------------
# 2. GET /jobs/{id}
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_returns_job_with_tasks(self, client) -> None:
        c, session = client
        job = _make_job_model()
        task = _make_task_model(job_id=job.id)
        job.tasks = [task]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = job
        session.execute.return_value = mock_result

        resp = c.get(f"/jobs/{job.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(job.id)
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["step_id"] == "search"

    def test_unknown_job_returns_404(self, client) -> None:
        c, session = client
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        resp = c.get(f"/jobs/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. POST /jobs/{id}/cancel
# ---------------------------------------------------------------------------

class TestCancelJob:
    @pytest.mark.parametrize("initial_status", ["pending", "planning", "planned", "running"])
    def test_cancellable_statuses_return_200(self, client, initial_status: str) -> None:
        c, session = client
        job = _make_job_model(status=initial_status)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = job
        session.execute.return_value = mock_result

        resp = c.post(f"/jobs/{job.id}/cancel")

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert job.status == "cancelled"

    @pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
    def test_terminal_statuses_return_409(self, client, terminal_status: str) -> None:
        c, session = client
        job = _make_job_model(status=terminal_status)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = job
        session.execute.return_value = mock_result

        resp = c.post(f"/jobs/{job.id}/cancel")

        assert resp.status_code == 409
        assert terminal_status in resp.json()["detail"]

    def test_unknown_job_returns_404(self, client) -> None:
        c, session = client
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        resp = c.post(f"/jobs/{uuid.uuid4()}/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. GET /jobs/{id}/tasks/{tid}
# ---------------------------------------------------------------------------

class TestGetTask:
    def test_returns_task_with_all_fields(self, client) -> None:
        c, session = client
        job_id = uuid.uuid4()
        task = _make_task_model(job_id=job_id, tool_output={"results": [{"title": "t"}]})

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = task
        session.execute.return_value = mock_result

        resp = c.get(f"/jobs/{job_id}/tasks/{task.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["step_id"] == "search"
        assert body["tool_output"] == {"results": [{"title": "t"}]}
        assert body["attempt_count"] == 1
        assert body["started_at"] is not None
        assert body["finished_at"] is not None

    def test_unknown_task_returns_404(self, client) -> None:
        c, session = client
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        resp = c.get(f"/jobs/{uuid.uuid4()}/tasks/{uuid.uuid4()}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_liveness_returns_ok(self, client) -> None:
        c, _ = client
        resp = c.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 6. GET /ready
# ---------------------------------------------------------------------------

class TestReady:
    def test_readiness_with_healthy_db(self, client) -> None:
        c, session = client
        resp = c.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# 7. GET /metrics
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_returns_prometheus_text(self, client) -> None:
        c, _ = client
        resp = c.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        # Standard prometheus header
        assert b"# HELP" in resp.content or b"# TYPE" in resp.content
