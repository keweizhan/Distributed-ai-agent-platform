"""Shared constants across API and worker."""

# Celery queue names
QUEUE_PLANNER  = "planner"
QUEUE_EXECUTOR = "executor"

# Celery task names
TASK_PLAN_JOB     = "worker.tasks.planner.plan_job"
TASK_EXECUTE_STEP = "worker.tasks.executor.execute_step"
