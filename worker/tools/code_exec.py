"""
code_exec tool — executes Python code through the configured sandbox backend.

The active backend is selected by SANDBOX_BACKEND in config:
  - "subprocess" (default): local subprocess, timeout only, no isolation
  - "docker": isolated container with CPU/memory/network limits

A timeout always raises ToolError regardless of backend, so callers
do not need to inspect SandboxResult.timed_out themselves.
"""

from __future__ import annotations

import logging
from typing import Any

from worker.sandbox.base import SandboxError
from worker.sandbox.factory import get_sandbox
from worker.tools.registry import ToolError, register_tool

logger = logging.getLogger(__name__)


@register_tool("code_exec")
def code_exec(code: str, timeout: int | None = None, **_: Any) -> dict[str, Any]:
    """
    Execute *code* as a Python script and return stdout/stderr/exit_code.

    Args:
        code:    Python source code to execute.
        timeout: Max seconds to wait (default: SANDBOX_TIMEOUT_SECONDS from config).

    Returns:
        {
            "stdout":            str,
            "stderr":            str,
            "exit_code":         int,
            "duration_seconds":  float,
            "sandbox":           "subprocess" | "docker"
        }

    Raises:
        ToolError: on empty code, timeout, or sandbox infrastructure failure.
    """
    if not code or not code.strip():
        raise ToolError("code_exec requires non-empty 'code' argument")

    from worker.config import settings  # lazy import avoids circular dep at load time

    timeout_secs = timeout if timeout is not None else settings.sandbox_timeout_seconds
    sandbox = get_sandbox()

    logger.debug(
        "code_exec: backend=%s timeout=%ds code_len=%d",
        sandbox.backend_name, timeout_secs, len(code),
    )

    try:
        result = sandbox.run(code, timeout_seconds=timeout_secs)
    except SandboxError as exc:
        raise ToolError(f"sandbox error: {exc}") from exc

    if result.timed_out:
        raise ToolError(f"code_exec timed out after {timeout_secs}s")

    logger.debug(
        "code_exec: exit_code=%d stdout=%d bytes stderr=%d bytes duration=%.2fs",
        result.exit_code, len(result.stdout), len(result.stderr), result.duration_seconds,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_seconds": round(result.duration_seconds, 3),
        "sandbox": sandbox.backend_name,
    }
