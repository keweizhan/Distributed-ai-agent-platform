"""
Prometheus metrics for the worker service.

Defined at module level — safe to import multiple times (Python caches the
module, so the registry receives each metric exactly once).

Metric naming convention:  agent_<noun>_<unit>  (Prometheus best practice)
"""

from prometheus_client import Counter, Histogram

# ── Task lifecycle ────────────────────────────────────────────────────────

task_executions_total = Counter(
    "agent_task_executions_total",
    "Task execution outcomes by type and final status",
    ["task_type", "status"],          # status: succeeded | failed | skipped
)

task_duration_seconds = Histogram(
    "agent_task_duration_seconds",
    "Wall-clock time from claim to completion",
    ["task_type"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
)

task_queue_delay_seconds = Histogram(
    "agent_task_queue_delay_seconds",
    "Seconds from task row creation to first worker claim (queue-wait proxy)",
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60],
)

task_retries_total = Counter(
    "agent_task_retries_total",
    "Times a task was retried due to a transient (non-ToolError) exception",
    ["task_type"],
)

# ── Tool calls ────────────────────────────────────────────────────────────

tool_calls_total = Counter(
    "agent_tool_calls_total",
    "Tool invocations by name and outcome",
    ["tool_name", "status"],          # status: succeeded | failed
)

tool_duration_seconds = Histogram(
    "agent_tool_duration_seconds",
    "Tool execution duration in seconds",
    ["tool_name"],
    buckets=[0.1, 0.5, 1, 5, 10, 30, 60],
)

# ── Planning ──────────────────────────────────────────────────────────────

job_plans_total = Counter(
    "agent_job_plans_total",
    "Job planning outcomes",
    ["status"],                        # status: succeeded | failed
)
