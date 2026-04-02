"""
Prometheus metrics for the API service.
"""

from prometheus_client import Counter, Histogram

jobs_created_total = Counter(
    "agent_jobs_created_total",
    "Total jobs submitted via POST /jobs",
)

jobs_cancelled_total = Counter(
    "agent_jobs_cancelled_total",
    "Total jobs cancelled via POST /jobs/{id}/cancel",
)

http_request_duration_seconds = Histogram(
    "agent_http_request_duration_seconds",
    "HTTP request latency by method and path",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.05, 0.1, 0.5, 1, 2],
)
