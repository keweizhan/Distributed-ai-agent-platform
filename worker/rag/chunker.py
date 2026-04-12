"""
Fixed-size text chunker with overlap.

chunk_text("...", chunk_size=500, overlap=100)
  → splits *text* into chunks of at most *chunk_size* characters,
    each consecutive pair sharing *overlap* characters.

The step size is (chunk_size - overlap), so for the defaults:
  step = 400 chars, each chunk starts 400 chars after the previous one.
"""

from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """
    Split *text* into fixed-size chunks with *overlap* character overlap.

    Returns an empty list if *text* is empty or whitespace-only.
    The last chunk may be shorter than *chunk_size*.
    """
    text = text.strip()
    if not text:
        return []

    step = chunk_size - overlap
    if step <= 0:
        raise ValueError(f"overlap ({overlap}) must be less than chunk_size ({chunk_size})")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += step

    return chunks
