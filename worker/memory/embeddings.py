"""
Text embedding function.

When OPENAI_API_KEY is set, uses OpenAI text-embedding-3-small (1536 dims).
Otherwise falls back to a deterministic mock for local dev and tests — no
network call, no cost, but the vectors carry no semantic meaning.
"""

from __future__ import annotations

import hashlib
import math
import random

_VECTOR_SIZE = 1536


def embed(text: str) -> list[float]:
    """
    Return a 1536-dimensional unit-norm embedding for *text*.

    Selects the backend lazily to avoid importing openai at module load time
    (which would fail when the package is not installed in test environments).
    """
    # Imported here to defer settings evaluation to call time
    from worker.config import settings

    if settings.openai_api_key not in ("sk-not-set", ""):
        return _openai_embed(text, settings)
    return _mock_embed(text)


def _openai_embed(text: str, settings) -> list[float]:  # type: ignore[no-untyped-def]
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    resp = client.embeddings.create(
        input=text[:8000],  # stay well within token limit
        model=settings.embedding_model,
    )
    return resp.data[0].embedding


def _mock_embed(text: str) -> list[float]:
    """
    Deterministic, unit-norm, hash-derived vector — reproducible across runs.

    The vectors carry no semantic meaning; they exist so every code path
    exercises the same interface without requiring an API key.
    """
    seed = int(hashlib.md5(text.encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(_VECTOR_SIZE)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
