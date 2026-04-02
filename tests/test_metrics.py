"""
Unit tests for Prometheus metrics modules (M5).

No running DB, Celery, or Prometheus needed — just verifies that:
  1. Metrics are defined with the correct names and label sets.
  2. Counters can be incremented and histograms can be observed.
  3. generate_latest() produces non-empty output that includes metric names.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, Counter, Histogram, generate_latest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_metric(name: str):
    """Return the collector registered under *name*, or None."""
    # prometheus_client prefixes the stored name; look by prefix
    for key, collector in list(REGISTRY._names_to_collectors.items()):
        if key == name or key.startswith(name):
            return collector
    return None


# ---------------------------------------------------------------------------
# Worker metrics
# ---------------------------------------------------------------------------

class TestWorkerMetrics:
    def test_all_metrics_defined(self) -> None:
        import worker.metrics as m
        assert isinstance(m.task_executions_total, Counter)
        assert isinstance(m.task_duration_seconds, Histogram)
        assert isinstance(m.task_queue_delay_seconds, Histogram)
        assert isinstance(m.task_retries_total, Counter)
        assert isinstance(m.tool_calls_total, Counter)
        assert isinstance(m.tool_duration_seconds, Histogram)
        assert isinstance(m.job_plans_total, Counter)

    def test_task_executions_counter_increments(self) -> None:
        import worker.metrics as m
        before = m.task_executions_total.labels(
            task_type="tool_call", status="succeeded"
        )._value.get()
        m.task_executions_total.labels(task_type="tool_call", status="succeeded").inc()
        after = m.task_executions_total.labels(
            task_type="tool_call", status="succeeded"
        )._value.get()
        assert after == before + 1

    def test_tool_duration_histogram_observes(self) -> None:
        import worker.metrics as m
        # Should not raise
        m.tool_duration_seconds.labels(tool_name="web_search").observe(0.42)

    def test_task_retries_counter_increments(self) -> None:
        import worker.metrics as m
        before = m.task_retries_total.labels(task_type="tool_call")._value.get()
        m.task_retries_total.labels(task_type="tool_call").inc()
        after = m.task_retries_total.labels(task_type="tool_call")._value.get()
        assert after == before + 1

    def test_job_plans_labels(self) -> None:
        import worker.metrics as m
        m.job_plans_total.labels(status="succeeded").inc()
        m.job_plans_total.labels(status="failed").inc()

    def test_metrics_appear_in_generate_latest(self) -> None:
        import worker.metrics  # noqa: F401 — ensure metrics are registered
        output = generate_latest().decode()
        assert "agent_task_executions_total" in output
        assert "agent_tool_calls_total" in output
        assert "agent_task_duration_seconds" in output


# ---------------------------------------------------------------------------
# API metrics
# ---------------------------------------------------------------------------

class TestAPIMetrics:
    def test_all_metrics_defined(self) -> None:
        import api.metrics as m
        assert isinstance(m.jobs_created_total, Counter)
        assert isinstance(m.jobs_cancelled_total, Counter)
        assert isinstance(m.http_request_duration_seconds, Histogram)

    def test_jobs_created_increments(self) -> None:
        import api.metrics as m
        before = m.jobs_created_total._value.get()
        m.jobs_created_total.inc()
        after = m.jobs_created_total._value.get()
        assert after == before + 1

    def test_http_duration_histogram_observes(self) -> None:
        import api.metrics as m
        m.http_request_duration_seconds.labels(
            method="POST", path="/jobs"
        ).observe(0.05)

    def test_metrics_appear_in_generate_latest(self) -> None:
        import api.metrics  # noqa: F401
        output = generate_latest().decode()
        assert "agent_jobs_created_total" in output
        assert "agent_http_request_duration_seconds" in output
