"""
Sandbox factory — selects the active backend from configuration.

SANDBOX_BACKEND=subprocess  (default)  → SubprocessSandbox
SANDBOX_BACKEND=docker                 → DockerSandbox

Adding a new backend: implement BaseSandbox in a new module, add a
branch here, and set SANDBOX_BACKEND to the new name.
"""

from __future__ import annotations

from worker.sandbox.base import BaseSandbox


def get_sandbox() -> BaseSandbox:
    """Return the configured sandbox backend (one instance per call)."""
    from worker.config import settings  # lazy import avoids circular deps

    if settings.sandbox_backend == "docker":
        from worker.sandbox.docker_sandbox import DockerSandbox
        return DockerSandbox(image=settings.sandbox_image)

    from worker.sandbox.subprocess_sandbox import SubprocessSandbox
    return SubprocessSandbox()
