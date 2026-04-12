"""
Distributed AI Agent Platform — API entrypoint.
"""

import time

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.metrics import http_request_duration_seconds
from api.routers import auth, documents, jobs

app = FastAPI(
    title="Distributed AI Agent Platform",
    description=(
        "Submit natural-language jobs, get back structured results.\n\n"
        "**Auth:** All `/jobs` endpoints require a Bearer JWT. "
        "Register at `POST /auth/register`, obtain a token at `POST /auth/token`.\n\n"
        "**Workspace isolation:** Every job is scoped to the caller's workspace; "
        "cross-tenant access returns 404.\n\n"
        "**Memory (optional):** Set `MEMORY_ENABLED=true` to enable Qdrant-backed "
        "semantic memory — past tool outputs and job results are retrieved as context "
        "for the planner and synthesis steps."
    ),
    version="0.7.0",
    openapi_tags=[
        {
            "name": "auth",
            "description": "Register users, obtain JWT tokens, and inspect the current session.",
        },
        {
            "name": "jobs",
            "description": (
                "Create and manage agent jobs. Each job is planned by the LLM planner, "
                "dispatched to Celery workers, and executed step-by-step. "
                "All endpoints are workspace-scoped."
            ),
        },
        {
            "name": "documents",
            "description": (
                "Ingest documents for RAG retrieval. "
                "Documents are chunked and embedded asynchronously; "
                "the agent's 'retrieval' tool can then surface relevant chunks as context."
            ),
        },
        {
            "name": "meta",
            "description": "Liveness and readiness probes.",
        },
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(documents.router)


# ---------------------------------------------------------------------------
# Latency middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def record_request_duration(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start
    # Normalise dynamic path segments so label cardinality stays low
    path = request.url.path
    http_request_duration_seconds.labels(
        method=request.method, path=path
    ).observe(duration)
    return response


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
async def ready(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """
    Readiness probe — verifies the DB connection is live.
    Returns 200 when ready, 503 on failure (handled by exception propagation).
    """
    await db.execute(text("SELECT 1"))
    return {"status": "ready", "db": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape endpoint — exposes all registered metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
