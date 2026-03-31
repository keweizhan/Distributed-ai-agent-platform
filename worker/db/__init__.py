"""
Sync SQLAlchemy session for Celery workers.
The engine is created lazily on first use so that importing worker modules
in unit tests does not require psycopg2 or a live database.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from worker.config import settings

_engine = None
_SessionLocal = None


def _get_session_factory() -> sessionmaker:
    global _engine, _SessionLocal
    if _SessionLocal is None:
        sync_url = settings.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )
        _engine = create_engine(sync_url, pool_size=5, max_overflow=10, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _SessionLocal


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    session = _get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
