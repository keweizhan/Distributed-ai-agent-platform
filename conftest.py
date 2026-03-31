"""
Root conftest — sets required environment variables before any module is imported.
This allows worker code (which instantiates Settings at module load) to be imported
in unit tests without a live DB or Redis.

These values are never used in unit tests (all DB/Celery calls are mocked), but
pydantic-settings requires them to be present.
"""

import os

os.environ.setdefault("DATABASE_URL",          "postgresql+asyncpg://agent:agentpass@localhost:5432/agentdb")
os.environ.setdefault("REDIS_URL",             "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL",     "redis://localhost:6379/0")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
os.environ.setdefault("OPENAI_API_KEY",        "sk-not-set")
