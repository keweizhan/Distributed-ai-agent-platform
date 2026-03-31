"""
Celery application factory.
Import this module to get the configured Celery instance.
"""

from celery import Celery

from worker.config import settings

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
