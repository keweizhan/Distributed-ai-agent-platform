"""
SubprocessSandbox — executes Python code in a local subprocess.

Used as the default backend when Docker is unavailable or when
SANDBOX_BACKEND=subprocess (the default).  Provides timeout enforcement
but no CPU/memory/network isolation — suitable for development and CI
environments where the code is trusted.
"""

from __future__ import annotations

import subprocess
import sys
import time

from worker.sandbox.base import BaseSandbox, SandboxResult


class SubprocessSandbox(BaseSandbox):
    backend_name = "subprocess"

    def run(self, code: str, timeout_seconds: int) -> SandboxResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return SandboxResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                timed_out=False,
                duration_seconds=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                stdout="",
                stderr=f"Process killed after {timeout_seconds}s timeout",
                exit_code=-1,
                timed_out=True,
                duration_seconds=time.monotonic() - start,
            )
