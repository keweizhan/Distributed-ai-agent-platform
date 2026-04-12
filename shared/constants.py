"""Shared constants across API and worker."""

# Celery queue names
QUEUE_PLANNER  = "planner"
QUEUE_EXECUTOR = "executor"

# Celery task names
TASK_PLAN_JOB        = "worker.tasks.planner.plan_job"
TASK_EXECUTE_STEP    = "worker.tasks.executor.execute_step"
TASK_INGEST_DOCUMENT  = "worker.tasks.ingest.ingest_document"
TASK_DELETE_DOCUMENT  = "worker.tasks.ingest.delete_document"

# Queue names
QUEUE_INGEST = "celery"   # ingest tasks run on the default queue
