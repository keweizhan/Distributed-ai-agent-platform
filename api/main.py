"""
Distributed AI Agent Platform — API entrypoint.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import jobs

app = FastAPI(
    title="Distributed AI Agent Platform",
    description="Accept natural language requests, plan and dispatch tasks across distributed workers.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
