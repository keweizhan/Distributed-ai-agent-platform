"""
Celery application factory.
Import this module to get the configured Celery instance.
"""

import logging

from celery import Celery
from celery.signals import worker_ready

from worker.config import settings

logger = logging.getLogger(__name__)

app = Celery(
    "agent_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "worker.tasks.planner",
        "worker.tasks.executor",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "worker.tasks.planner.*":  {"queue": "planner"},
        "worker.tasks.executor.*": {"queue": "executor"},
    },
    worker_prefetch_multiplier=1,   # fair dispatch; important for long-running tasks
    task_acks_late=True,            # ack only after task completes (safer for retries)
)


# ---------------------------------------------------------------------------
# Prometheus metrics server
# ---------------------------------------------------------------------------
# Starts a lightweight HTTP server on WORKER_METRICS_PORT (default 9090) in the
# main worker process so Prometheus can scrape it.
#
# Tradeoff: Celery's default prefork pool spawns child processes; metrics written
# by child workers are not visible here unless PROMETHEUS_MULTIPROC_DIR is set
# (prometheus_client multiprocess mode).  For the demo concurrency=1 is used so
# everything runs in one process and all metrics are captured.
# In production, set PROMETHEUS_MULTIPROC_DIR to a shared tmpfs volume.

@worker_ready.connect
def _start_metrics_server(**_kw: object) -> None:
    from prometheus_client import start_http_server
    from worker.tools.registry import list_tools
    port = settings.worker_metrics_port
    start_http_server(port)
    logger.info("Prometheus metrics server started on :%d", port)
    logger.info("Registered tools at worker startup: %s", list_tools())
