"""
Unit tests for the executor task — Milestones 3A, 3B, and 4.

All DB calls are mocked; no live Postgres or Celery required.

Test organisation
-----------------
1.  execute_step — success path
2.  execute_step — failure paths
3.  execute_step — synthesis step
4.  Pure dependency functions (_transitive_dependents, _newly_ready_step_ids)
5.  _skip_downstream
6.  _check_job_completion
7.  _enqueue_newly_ready (basic cases)
8.  Topology integration tests
     8a. Linear chain  A → B → C
     8b. Fan-out       A → [B, C]
     8c. Fan-in        [A, B] → C
     8d. Upstream failure blocks downstream
9.  Pre-flight skip (task arrives after job already failed)
10. Tool unit tests (web_search, code_exec)
11. Execution hardening (M4)
     11a. Duplicate execution prevention (claim guard)
     11b. attempt_count / started_at / finished_at tracking
     11c. Terminal task guard (at-least-once redelivery)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from worker.tools.registry import ToolError


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_task(
    *,
    task_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    step_id: str = "search",
    task_type: str = "tool_call",
    tool_name: str | None = "web_search",
    tool_input: dict | None = None,
    status: str = "queued",
    dependencies: list[str] | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = task_id or uuid.uuid4()
    t.job_id = job_id or uuid.uuid4()
    t.step_id = step_id
    t.task_type = task_type
    t.tool_name = tool_name
    t.tool_input = tool_input or {}
    t.status = status
    t.error = None
    t.tool_output = None
    t.dependencies = dependencies or []
    t.attempt_count = 0
    t.started_at = None
    t.finished_at = None
    return t


def _make_job(
    *,
    job_id: uuid.UUID | None = None,
    status: str = "running",
) -> MagicMock:
    j = MagicMock()
    j.id = job_id or uuid.uuid4()
    j.status = status
    j.result = None
    j.error = None
    return j


def _session_for(
    *tasks: MagicMock,
    job: MagicMock | None = None,
    claim_rowcount: int = 1,
) -> MagicMock:
    """
    Build a mock session where:
      - session.get(TaskModel, task.id) → that task
      - session.get(JobModel, any_key)  → job (if provided)
      - session.query(...).filter(...).all() → list(tasks)
      - session.execute(...).rowcount → claim_rowcount (default 1 = claim succeeds)
    """
    task_map = {t.id: t for t in tasks}
    _job = job or (tasks[0] and _make_job(job_id=tasks[0].job_id))

    def _get(model, pk):
        if pk in task_map:
            return task_map[pk]
        return _job

    session = MagicMock()
    session.get.side_effect = _get
    session.query.return_value.filter.return_value.all.return_value = list(tasks)
    session.execute.return_value.rowcount = claim_rowcount
    return session


# ---------------------------------------------------------------------------
# 1. execute_step — success path
# ---------------------------------------------------------------------------

class TestExecuteStepSuccess:
    def test_successful_tool_call_transitions_task_to_succeeded(self) -> None:
        task = _make_task(tool_name="web_search", tool_input={"query": "test"})
        job = _make_job(job_id=task.job_id)
        fake_output = {"query": "test", "results": []}

        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: fake_output

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        assert result["status"] == "succeeded"
        assert result["step_id"] == task.step_id
        assert task.status == "succeeded"
        assert task.tool_output == fake_output

    def test_result_contains_newly_enqueued_list(self) -> None:
        task = _make_task()
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        assert "newly_enqueued" in result
        assert isinstance(result["newly_enqueued"], list)

    def test_status_set_running_before_tool_called(self) -> None:
        """Task must be committed as 'running' before invoking the tool."""
        call_order: list[str] = []
        task = _make_task()
        job = _make_job(job_id=task.job_id)

        def _commit() -> None:
            call_order.append(f"commit:{task.status}")

        session = _session_for(task, job=job)
        session.commit.side_effect = _commit

        def _tool(**kw: object) -> dict:
            call_order.append("tool_called")
            return {}

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = _tool

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert call_order[0] == "commit:running"
        assert "tool_called" in call_order


# ---------------------------------------------------------------------------
# 2. execute_step — failure paths
# ---------------------------------------------------------------------------

class TestExecuteStepFailure:
    def test_tool_error_marks_task_failed(self) -> None:
        task = _make_task(tool_name="web_search")
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = MagicMock(side_effect=ToolError("network error"))

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        assert result["status"] == "failed"
        assert "network error" in result["error"]
        assert task.status == "failed"
        assert "network error" in task.error

    def test_unknown_tool_marks_task_failed(self) -> None:
        task = _make_task(tool_name="nonexistent_tool")
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool", side_effect=ToolError("Unknown tool")),
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        assert result["status"] == "failed"
        assert task.status == "failed"

    def test_task_not_found_returns_error(self) -> None:
        session = MagicMock()
        session.get.return_value = None

        with patch("worker.tasks.executor.get_sync_session") as mock_ctx:
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(uuid.uuid4()))

        assert "error" in result
        assert "not found" in result["error"]

    def test_invalid_task_id_returns_error(self) -> None:
        from worker.tasks.executor import execute_step
        result = execute_step("not-a-uuid")
        assert "error" in result

    def test_task_failure_marks_job_failed(self) -> None:
        task = _make_task(tool_name="web_search")
        job = _make_job(job_id=task.job_id, status="running")
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = MagicMock(side_effect=ToolError("boom"))

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert job.status == "failed"


# ---------------------------------------------------------------------------
# 3. Synthesis step
# ---------------------------------------------------------------------------

class TestSynthesisStep:
    def test_synthesis_step_succeeds_without_calling_a_tool(self) -> None:
        task = _make_task(task_type="synthesis", tool_name=None)
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_not_called()
        assert result["status"] == "succeeded"
        assert task.status == "succeeded"


# ---------------------------------------------------------------------------
# 4. Pure dependency functions
# ---------------------------------------------------------------------------

class TestTransitiveDependents:
    """Unit tests for _transitive_dependents — no session needed."""

    def _tasks(self, job_id: uuid.UUID, specs: list[tuple[str, list[str], str]]) -> list[MagicMock]:
        """specs: [(step_id, dependencies, status), ...]"""
        return [_make_task(job_id=job_id, step_id=s, dependencies=d, status=st)
                for s, d, st in specs]

    def test_linear_chain_from_root(self) -> None:
        """A→B→C, seed=A → {B, C}"""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "pending"),
            ("C", ["B"], "pending"),
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert result == {"B", "C"}

    def test_fan_out(self) -> None:
        """A→B and A→C, seed=A → {B, C}"""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "pending"),
            ("C", ["A"], "pending"),
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert result == {"B", "C"}

    def test_fan_in(self) -> None:
        """[A,B]→C, seed=A → {C} (C depends on A transitively)"""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", [], "succeeded"),
            ("C", ["A", "B"], "pending"),
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert "C" in result

    def test_no_dependents(self) -> None:
        """A has no downstream tasks — result is empty."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [("A", [], "succeeded")])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert result == set()

    def test_seed_not_included_in_result(self) -> None:
        """The seed step_id itself must not appear in the returned set."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "pending"),
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert "A" not in result

    def test_transitive_depth_three(self) -> None:
        """A→B→C→D — failing A should skip B, C, D."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "failed"),
            ("B", ["A"], "pending"),
            ("C", ["B"], "pending"),
            ("D", ["C"], "pending"),
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert result == {"B", "C", "D"}

    def test_independent_branch_not_included(self) -> None:
        """Tasks on an independent branch are not reachable from the seed."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "failed"),
            ("B", ["A"], "pending"),   # downstream of A
            ("X", [], "pending"),      # independent root
            ("Y", ["X"], "pending"),   # independent branch
        ])
        from worker.tasks.executor import _transitive_dependents
        result = _transitive_dependents(tasks, {"A"})
        assert result == {"B"}
        assert "X" not in result
        assert "Y" not in result


class TestNewlyReadyStepIds:
    """Unit tests for _newly_ready_step_ids — no session needed."""

    def _tasks(self, job_id: uuid.UUID, specs: list[tuple[str, list[str], str]]) -> list[MagicMock]:
        return [_make_task(job_id=job_id, step_id=s, dependencies=d, status=st)
                for s, d, st in specs]

    def test_root_task_ready_immediately(self) -> None:
        """A task with no dependencies is ready when succeeded_ids is empty."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [("A", [], "pending")])
        from worker.tasks.executor import _newly_ready_step_ids
        assert _newly_ready_step_ids(tasks, set()) == {"A"}

    def test_dependent_not_ready_until_dep_succeeds(self) -> None:
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "pending"),
        ])
        from worker.tasks.executor import _newly_ready_step_ids
        # B is not in result when succeeded_ids doesn't include A
        assert "B" not in _newly_ready_step_ids(tasks, set())
        # B is in result once A is marked succeeded
        assert "B" in _newly_ready_step_ids(tasks, {"A"})

    def test_fan_in_requires_all_deps(self) -> None:
        """C depends on both A and B — not ready until both succeed."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", [], "succeeded"),
            ("C", ["A", "B"], "pending"),
        ])
        from worker.tasks.executor import _newly_ready_step_ids
        assert "C" not in _newly_ready_step_ids(tasks, {"A"})
        assert "C" in _newly_ready_step_ids(tasks, {"A", "B"})

    def test_already_queued_task_excluded(self) -> None:
        """Tasks not in 'pending' state must never be returned."""
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "queued"),   # already dispatched
        ])
        from worker.tasks.executor import _newly_ready_step_ids
        assert "B" not in _newly_ready_step_ids(tasks, {"A"})

    def test_skipped_task_excluded(self) -> None:
        jid = uuid.uuid4()
        tasks = self._tasks(jid, [
            ("A", [], "succeeded"),
            ("B", ["A"], "skipped"),
        ])
        from worker.tasks.executor import _newly_ready_step_ids
        assert "B" not in _newly_ready_step_ids(tasks, {"A"})


# ---------------------------------------------------------------------------
# 5. _skip_downstream
# ---------------------------------------------------------------------------

class TestSkipDownstream:
    def test_marks_pending_dependents_skipped(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="failed")
        task_b = _make_task(job_id=jid, step_id="B", status="pending", dependencies=["A"])
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["B"])

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        count = _skip_downstream(session, [task_a, task_b, task_c], "A")

        assert task_b.status == "skipped"
        assert task_c.status == "skipped"
        assert count == 2

    def test_does_not_skip_queued_tasks(self) -> None:
        """Queued tasks are already in-flight; leave them for pre-flight to handle."""
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="failed")
        task_b = _make_task(job_id=jid, step_id="B", status="queued", dependencies=["A"])

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        _skip_downstream(session, [task_a, task_b], "A")

        assert task_b.status == "queued"  # unchanged

    def test_independent_branch_not_skipped(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="failed")
        task_x = _make_task(job_id=jid, step_id="X", status="pending", dependencies=[])

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        _skip_downstream(session, [task_a, task_x], "A")

        assert task_x.status == "pending"  # independent, untouched

    def test_no_dependents_returns_zero(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="failed")

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        count = _skip_downstream(session, [task_a], "A")

        assert count == 0
        session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# 6. _check_job_completion
# ---------------------------------------------------------------------------

class TestCheckJobCompletion:
    def test_all_succeeded_marks_job_succeeded(self) -> None:
        jid = uuid.uuid4()
        tasks = [
            _make_task(job_id=jid, status="succeeded", step_id="a"),
            _make_task(job_id=jid, status="succeeded", task_type="synthesis",
                       tool_name=None, step_id="b"),
        ]
        job = _make_job(job_id=jid, status="running")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is True
        assert job.status == "succeeded"

    def test_any_failed_marks_job_failed(self) -> None:
        jid = uuid.uuid4()
        tasks = [
            _make_task(job_id=jid, status="succeeded", step_id="a"),
            _make_task(job_id=jid, status="failed", step_id="b"),
        ]
        job = _make_job(job_id=jid, status="running")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is True
        assert job.status == "failed"

    def test_partial_completion_returns_false(self) -> None:
        jid = uuid.uuid4()
        tasks = [
            _make_task(job_id=jid, status="succeeded", step_id="a"),
            _make_task(job_id=jid, status="pending", step_id="b"),
        ]
        job = _make_job(job_id=jid, status="running")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is False
        assert job.status == "running"  # unchanged

    def test_skipped_counts_as_terminal(self) -> None:
        """A mix of succeeded + skipped tasks should tip the job to succeeded."""
        jid = uuid.uuid4()
        tasks = [
            _make_task(job_id=jid, status="succeeded", step_id="a"),
            _make_task(job_id=jid, status="skipped", step_id="b"),
        ]
        job = _make_job(job_id=jid, status="running")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is True
        assert job.status == "succeeded"

    def test_already_failed_job_not_overridden(self) -> None:
        """If job is already terminal, status must not be changed."""
        jid = uuid.uuid4()
        tasks = [_make_task(job_id=jid, status="succeeded", step_id="a")]
        job = _make_job(job_id=jid, status="failed")  # already terminal
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is True
        assert job.status == "failed"  # unchanged — guard respected

    def test_already_succeeded_job_not_overridden(self) -> None:
        jid = uuid.uuid4()
        tasks = [_make_task(job_id=jid, status="failed", step_id="a")]
        job = _make_job(job_id=jid, status="succeeded")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        assert _check_job_completion(session, jid) is True
        assert job.status == "succeeded"


# ---------------------------------------------------------------------------
# 7. _enqueue_newly_ready (basic)
# ---------------------------------------------------------------------------

class TestEnqueueNewlyReady:
    def test_dependent_task_enqueued_when_deps_satisfied(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="step_a", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="step_b", status="pending", dependencies=["step_a"])
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [task_a, task_b]

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert str(task_b.id) in enqueued
        assert task_b.status == "queued"
        mock_send.assert_called_once()

    def test_task_with_unsatisfied_dep_not_enqueued(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="step_a", status="succeeded")
        task_c = _make_task(job_id=jid, step_id="step_c", status="running")
        task_b = _make_task(
            job_id=jid, step_id="step_b",
            status="pending", dependencies=["step_a", "step_c"],
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [task_a, task_b, task_c]

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert enqueued == []
        assert task_b.status == "pending"
        mock_send.assert_not_called()

    def test_already_queued_task_not_double_enqueued(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="step_a", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="step_b", status="queued", dependencies=["step_a"])
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [task_a, task_b]

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert enqueued == []
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Topology integration tests
#
# These tests exercise _enqueue_newly_ready and _skip_downstream end-to-end
# using pure in-memory task lists, without a live DB or Celery.
# They validate the correct dispatch decisions for each DAG shape.
# ---------------------------------------------------------------------------

class TestLinearChain:
    """
    Plan:  A → B → C

    Timeline:
        t0: A queued     (planner enqueues A)
        t1: A succeeds   → B enqueued
        t2: B succeeds   → C enqueued
        t3: C succeeds   → job succeeded
    """

    def _build(self) -> tuple[uuid.UUID, list[MagicMock]]:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="B", status="pending", dependencies=["A"])
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["B"])
        return jid, [task_a, task_b, task_c]

    def test_a_succeeds_enqueues_only_b(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert str(task_b.id) in enqueued
        assert str(task_c.id) not in enqueued  # C still waiting for B
        assert task_b.status == "queued"
        assert task_c.status == "pending"
        assert mock_send.call_count == 1

    def test_b_succeeds_enqueues_only_c(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        # Simulate: A succeeded, B now succeeds
        task_b.status = "succeeded"

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_b)

        assert str(task_c.id) in enqueued
        assert task_c.status == "queued"
        assert mock_send.call_count == 1

    def test_c_succeeds_nothing_more_to_enqueue(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        task_b.status = "succeeded"
        task_c.status = "succeeded"

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_c)

        assert enqueued == []
        mock_send.assert_not_called()


class TestFanOut:
    """
    Plan:  A → B
           A → C

    Timeline:
        t0: A queued
        t1: A succeeds → B and C enqueued simultaneously
        t2: B succeeds → nothing more to enqueue (C already queued)
        t3: C succeeds → job succeeded
    """

    def _build(self) -> tuple[uuid.UUID, list[MagicMock]]:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="B", status="pending", dependencies=["A"])
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["A"])
        return jid, [task_a, task_b, task_c]

    def test_a_succeeds_enqueues_both_b_and_c(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert str(task_b.id) in enqueued
        assert str(task_c.id) in enqueued
        assert task_b.status == "queued"
        assert task_c.status == "queued"
        assert mock_send.call_count == 2

    def test_b_succeeds_does_not_re_enqueue_c(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        # C is already queued from when A succeeded
        task_b.status = "succeeded"
        task_c.status = "queued"

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_b)

        assert enqueued == []
        mock_send.assert_not_called()


class TestFanIn:
    """
    Plan:  A ─┐
              ├→ C
           B ─┘

    C cannot run until BOTH A and B succeed.

    Timeline:
        t0: A and B queued (both are roots)
        t1: A succeeds → C not yet ready
        t2: B succeeds → C now ready, enqueued
        t3: C succeeds → job succeeded
    """

    def _build(self) -> tuple[uuid.UUID, list[MagicMock]]:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="B", status="pending")
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["A", "B"])
        return jid, [task_a, task_b, task_c]

    def test_a_succeeds_does_not_enqueue_c(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert str(task_c.id) not in enqueued
        assert task_c.status == "pending"

    def test_both_a_and_b_succeeded_enqueues_c(self) -> None:
        _, tasks = self._build()
        task_a, task_b, task_c = tasks
        task_b.status = "succeeded"  # B now also succeeded

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = tasks

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            # Trigger from B completing (A already succeeded in setup)
            enqueued = _enqueue_newly_ready(session, task_b)

        assert str(task_c.id) in enqueued
        assert task_c.status == "queued"
        assert mock_send.call_count == 1

    def test_c_not_enqueued_if_only_one_dep_satisfied(self) -> None:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="succeeded")
        task_b = _make_task(job_id=jid, step_id="B", status="running")  # not yet done
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["A", "B"])

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = [task_a, task_b, task_c]

        with patch("worker.tasks.executor.app.send_task") as mock_send:
            from worker.tasks.executor import _enqueue_newly_ready
            enqueued = _enqueue_newly_ready(session, task_a)

        assert enqueued == []
        mock_send.assert_not_called()


class TestUpstreamFailure:
    """
    Plan:  A → B → C
           X → Y        (independent branch)

    When A fails:
    - B and C must be SKIPPED (blocked by A)
    - X and Y must be UNAFFECTED
    - Job must be FAILED (fast-fail)
    """

    def _build(self) -> tuple[uuid.UUID, list[MagicMock]]:
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", status="running")
        task_b = _make_task(job_id=jid, step_id="B", status="pending", dependencies=["A"])
        task_c = _make_task(job_id=jid, step_id="C", status="pending", dependencies=["B"])
        task_x = _make_task(job_id=jid, step_id="X", status="succeeded")
        task_y = _make_task(job_id=jid, step_id="Y", status="pending", dependencies=["X"])
        return jid, [task_a, task_b, task_c, task_x, task_y]

    def test_a_fails_skips_b_and_c(self) -> None:
        _, tasks = self._build()
        task_a = tasks[0]
        task_b = tasks[1]
        task_c = tasks[2]

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        _skip_downstream(session, tasks, "A")

        assert task_b.status == "skipped"
        assert task_c.status == "skipped"

    def test_a_fails_does_not_skip_independent_branch(self) -> None:
        _, tasks = self._build()
        task_x = tasks[3]
        task_y = tasks[4]

        session = MagicMock()
        from worker.tasks.executor import _skip_downstream
        _skip_downstream(session, tasks, "A")

        assert task_x.status == "succeeded"  # unchanged
        assert task_y.status == "pending"    # unchanged (independent)

    def test_full_failure_flow_marks_job_failed(self) -> None:
        """End-to-end: task A fails → downstream skipped → job failed."""
        jid = uuid.uuid4()
        task_a = _make_task(job_id=jid, step_id="A", tool_name="web_search")
        task_b = _make_task(job_id=jid, step_id="B", status="pending", dependencies=["A"])
        job = _make_job(job_id=jid)

        all_tasks = [task_a, task_b]
        session = _session_for(task_a, task_b, job=job)
        session.query.return_value.filter.return_value.all.return_value = all_tasks

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = MagicMock(side_effect=ToolError("upstream failed"))

            from worker.tasks.executor import execute_step
            result = execute_step(str(task_a.id))

        assert result["status"] == "failed"
        assert task_a.status == "failed"
        assert task_b.status == "skipped"
        assert job.status == "failed"

    def test_skipped_tasks_count_as_terminal_for_job_completion(self) -> None:
        """After skipping, _check_job_completion must see job as terminal."""
        jid = uuid.uuid4()
        tasks = [
            _make_task(job_id=jid, step_id="A", status="failed"),
            _make_task(job_id=jid, step_id="B", status="skipped"),
            _make_task(job_id=jid, step_id="C", status="skipped"),
        ]
        job = _make_job(job_id=jid, status="running")
        session = MagicMock()
        session.get.return_value = job
        session.query.return_value.filter.return_value.all.return_value = tasks

        from worker.tasks.executor import _check_job_completion
        done = _check_job_completion(session, jid)

        assert done is True
        assert job.status == "failed"


# ---------------------------------------------------------------------------
# 9. Pre-flight skip
# ---------------------------------------------------------------------------

class TestPreFlightSkip:
    def test_queued_task_skipped_when_job_already_failed(self) -> None:
        """
        If a task is picked up by a worker after its job was fast-failed
        (race condition), it must be skipped without invoking the tool.
        """
        jid = uuid.uuid4()
        task = _make_task(job_id=jid, status="queued")
        job = _make_job(job_id=jid, status="failed")  # already terminal

        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_not_called()
        assert result["status"] == "skipped"
        assert task.status == "skipped"

    def test_queued_task_skipped_when_job_cancelled(self) -> None:
        jid = uuid.uuid4()
        task = _make_task(job_id=jid, status="queued")
        job = _make_job(job_id=jid, status="cancelled")

        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_not_called()
        assert result["status"] == "skipped"

    def test_running_job_proceeds_normally(self) -> None:
        """Pre-flight must not trigger for jobs still in 'running' state."""
        jid = uuid.uuid4()
        task = _make_task(job_id=jid, status="queued")
        job = _make_job(job_id=jid, status="running")

        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_called_once()
        assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 10. Tool unit tests
# ---------------------------------------------------------------------------

class TestWebSearchTool:
    def test_empty_query_raises_tool_error(self) -> None:
        from worker.tools.web_search import web_search
        with pytest.raises(ToolError, match="non-empty"):
            web_search(query="")

    def test_whitespace_query_raises_tool_error(self) -> None:
        from worker.tools.web_search import web_search
        with pytest.raises(ToolError, match="non-empty"):
            web_search(query="   ")

    def test_returns_dict_with_query_and_results_keys(self) -> None:
        from worker.tools.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.return_value = [
            {"title": "Result 1", "href": "https://example.com", "body": "Snippet 1"},
        ]
        with patch.dict("sys.modules", {"duckduckgo_search": MagicMock(DDGS=mock_ddgs)}):
            result = web_search(query="python testing")

        assert result["query"] == "python testing"
        assert len(result["results"]) == 1
        assert result["results"][0]["url"] == "https://example.com"

    def test_ddgs_exception_raises_tool_error(self) -> None:
        from worker.tools.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.return_value.text.side_effect = RuntimeError("connection refused")
        with (
            patch.dict("sys.modules", {"duckduckgo_search": MagicMock(DDGS=mock_ddgs)}),
            pytest.raises(ToolError, match="web_search failed"),
        ):
            web_search(query="test query")


class TestCodeExecTool:
    def test_empty_code_raises_tool_error(self) -> None:
        from worker.tools.code_exec import code_exec
        with pytest.raises(ToolError, match="non-empty"):
            code_exec(code="")

    def test_successful_code_returns_stdout(self) -> None:
        from worker.tools.code_exec import code_exec
        result = code_exec(code='print("hello")')
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["sandbox"] == "subprocess"

    def test_failed_code_returns_nonzero_exit_code(self) -> None:
        from worker.tools.code_exec import code_exec
        result = code_exec(code="raise ValueError('test error')")
        assert result["exit_code"] != 0
        assert "ValueError" in result["stderr"]

    def test_timeout_raises_tool_error(self) -> None:
        from worker.tools.code_exec import code_exec
        with pytest.raises(ToolError, match="timed out"):
            code_exec(code="import time; time.sleep(60)", timeout=1)

    def test_successful_result_includes_duration(self) -> None:
        from worker.tools.code_exec import code_exec
        result = code_exec(code='print("hi")')
        assert "duration_seconds" in result
        assert result["duration_seconds"] >= 0


# ---------------------------------------------------------------------------
# 11. Execution hardening (M4)
# ---------------------------------------------------------------------------

class TestDuplicateExecutionPrevention:
    """Claim guard: only one worker may execute a given task_id."""

    def test_claim_succeeds_when_rowcount_is_one(self) -> None:
        """Normal path: claim returns True, task proceeds to running."""
        task = _make_task(status="queued")
        job = _make_job(job_id=task.job_id)
        # rowcount=1 → claim succeeds (default)
        session = _session_for(task, job=job, claim_rowcount=1)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        assert result["status"] == "succeeded"
        assert task.status == "succeeded"

    def test_claim_fails_when_rowcount_is_zero(self) -> None:
        """Another worker already claimed this task — execute_step must exit early."""
        task = _make_task(status="queued")
        job = _make_job(job_id=task.job_id)
        # rowcount=0 → claim fails, task already claimed by another worker
        session = _session_for(task, job=job, claim_rowcount=0)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_not_called()
        assert result["status"] == "skipped"

    def test_claim_sets_started_at(self) -> None:
        """_claim_task must write started_at onto the task object."""
        task = _make_task(status="queued")
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job, claim_rowcount=1)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert task.started_at is not None

    def test_claim_increments_attempt_count(self) -> None:
        task = _make_task(status="queued")
        task.attempt_count = 0
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job, claim_rowcount=1)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert task.attempt_count == 1


class TestTimestampTracking:
    """finished_at is set on both success and failure paths."""

    def test_finished_at_set_on_success(self) -> None:
        task = _make_task(status="queued")
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job, claim_rowcount=1)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = lambda **kw: {}

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert task.finished_at is not None

    def test_finished_at_set_on_failure(self) -> None:
        task = _make_task(status="queued")
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job, claim_rowcount=1)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session
            mock_get_tool.return_value = MagicMock(side_effect=ToolError("boom"))

            from worker.tasks.executor import execute_step
            execute_step(str(task.id))

        assert task.finished_at is not None


class TestTerminalTaskGuard:
    """Tasks in terminal states must not be re-executed (at-least-once redelivery)."""

    @pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "skipped"])
    def test_already_terminal_task_skips_without_tool_call(self, terminal_status: str) -> None:
        task = _make_task(status=terminal_status)
        job = _make_job(job_id=task.job_id)
        session = _session_for(task, job=job)

        with (
            patch("worker.tasks.executor.get_sync_session") as mock_ctx,
            patch("worker.tasks.executor.get_tool") as mock_get_tool,
        ):
            mock_ctx.return_value.__enter__.return_value = session

            from worker.tasks.executor import execute_step
            result = execute_step(str(task.id))

        mock_get_tool.assert_not_called()
        assert result["status"] == terminal_status
