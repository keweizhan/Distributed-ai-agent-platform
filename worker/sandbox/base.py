"""
Sandbox interface for isolated Python code execution.

New backends only need to subclass BaseSandbox, set backend_name, and
implement run().  The factory (factory.py) selects the active backend
based on SANDBOX_BACKEND in config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SandboxError(Exception):
    """Raised when the sandbox infrastructure itself fails.

    This is distinct from a non-zero exit code or a timeout — those are
    normal execution outcomes captured in SandboxResult.  SandboxError
    means the sandbox could not be started or communicated with at all
    (e.g. Docker daemon unreachable, image pull failure).
    """


@dataclass
class SandboxResult:
    """Outcome of a single sandbox execution."""

    stdout: str
    stderr: str
    exit_code: int           # -1 when timed_out
    timed_out: bool
    duration_seconds: float


class BaseSandbox(ABC):
    """Abstract base for all execution backends."""

    #: Short identifier returned in tool output (e.g. "subprocess", "docker").
    backend_name: str

    @abstractmethod
    def run(self, code: str, timeout_seconds: int) -> SandboxResult:
        """Execute *code* as a Python script and return the result.

        Must never raise on normal execution failures (non-zero exit, timeout).
        Only raises SandboxError when the backend itself is broken.
        """
