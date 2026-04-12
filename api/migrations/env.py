"""
Alembic environment configuration.

Uses an async SQLAlchemy engine (asyncpg) so it matches the engine
the API uses at runtime — no separate sync driver needed.

DATABASE_URL is read from the environment first; alembic.ini's
sqlalchemy.url is the fallback for local runs without the Docker env.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# ── Ensure the project root is importable when running alembic locally ────────
# e.g.: `alembic upgrade head` from /path/to/project/
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import the shared metadata that Alembic compares against the live DB for
# autogenerate.  All ORM models must be imported before this line so
# SQLAlchemy registers their Table objects with Base.metadata.
from api.db.models import Base  # noqa: E402  (must be after sys.path fix)

# ---------------------------------------------------------------------------
# Alembic Config object (gives access to alembic.ini values)
# ---------------------------------------------------------------------------

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# URL resolution — env var takes priority over alembic.ini
# ---------------------------------------------------------------------------

def get_url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Either export it or add sqlalchemy.url to alembic.ini."
        )
    return url


# ---------------------------------------------------------------------------
# Offline mode — emits SQL to stdout instead of executing it
# Useful for reviewing what a migration will do before running it.
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Render PostgreSQL-specific constructs (e.g. ENUM types, partial indexes)
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connects to the DB and runs migrations
# ---------------------------------------------------------------------------

def do_run_migrations(connection) -> None:  # type: ignore[type-arg]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Let Alembic compare server_defaults when autogenerating
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(get_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
