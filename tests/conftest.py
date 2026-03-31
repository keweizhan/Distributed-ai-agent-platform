"""
Shared pytest fixtures.

Unit tests in this suite do NOT require a live Postgres or Redis instance.
Integration tests (future) will use a separate conftest with Docker fixtures.
"""

import uuid
import pytest


@pytest.fixture
def sample_job_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def sample_prompt() -> str:
    return "Research the latest advances in transformer architectures and summarise key findings"
