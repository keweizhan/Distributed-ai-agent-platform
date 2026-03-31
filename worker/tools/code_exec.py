"""
code_exec tool — executes Python code in a subprocess with a timeout.

Security note: this runs code directly on the host without sandboxing.
A Docker-based sandbox will replace this in Milestone 4.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

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
            "stdout":    str,
            "stderr":    str,
            "exit_code": int,
            "sandbox":   "subprocess"   # will become "docker" in M4
        }
    """
    if not code or not code.strip():
        raise ToolError("code_exec requires non-empty 'code' argument")

    # Lazy import to avoid circular dependency at module load time
    from worker.config import settings  # noqa: PLC0415

    timeout_secs = timeout if timeout is not None else settings.sandbox_timeout_seconds

    logger.debug("code_exec: running %d chars of Python (timeout=%ds)", len(code), timeout_secs)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"code_exec timed out after {timeout_secs}s")
    except Exception as exc:
        raise ToolError(f"code_exec subprocess error: {exc}") from exc

    logger.debug(
        "code_exec exit_code=%d stdout=%d bytes stderr=%d bytes",
        proc.returncode, len(proc.stdout), len(proc.stderr),
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "sandbox": "subprocess",  # upgraded to "docker" in M4
    }
