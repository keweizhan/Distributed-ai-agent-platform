"""
DockerSandbox — runs Python code inside an isolated Docker container.

Safety boundaries enforced per execution:
  - Network:   disabled (network_disabled=True)
  - Memory:    128 MiB hard limit (configurable)
  - CPU:       50 % of one core via cpu_quota (configurable)
  - Filesystem: only a read-only temp workspace is mounted; the host
                filesystem is not accessible from inside the container
  - Cleanup:   container is force-removed in a finally block even when
               the run times out or raises

Limitations:
  - Requires the `docker` Python package (pip install docker>=7) and a
    Docker daemon accessible from the worker process (docker.sock must be
    mounted in docker-compose; see worker/Dockerfile)
  - Container startup adds ~0.5–2 s latency depending on image caching
  - Stdout and stderr are captured separately after the container exits;
    very large outputs may consume memory before being returned
  - The sandbox image (default: python:3.11-slim) must be pulled in
    advance or the first run will be slow

The docker package is imported lazily inside __init__ so this module can
be loaded in test environments where docker is not installed.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time

from worker.sandbox.base import BaseSandbox, SandboxError, SandboxResult

logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_LIMIT = "128m"
_DEFAULT_CPU_QUOTA = 50_000  # 50 % of one core (100_000 = one full core)


class DockerSandbox(BaseSandbox):
    backend_name = "docker"

    def __init__(
        self,
        image: str,
        memory_limit: str = _DEFAULT_MEMORY_LIMIT,
        cpu_quota: int = _DEFAULT_CPU_QUOTA,
    ) -> None:
        self._image = image
        self._memory_limit = memory_limit
        self._cpu_quota = cpu_quota
        # Lazy import: allows module to load in environments without docker SDK
        try:
            import docker
        except ImportError as exc:
            raise SandboxError(
                "docker package is not installed — install it with: pip install docker>=7"
            ) from exc
        try:
            self._client = docker.from_env()
        except Exception as exc:
            raise SandboxError(f"Cannot connect to Docker daemon: {exc}") from exc

    def run(self, code: str, timeout_seconds: int) -> SandboxResult:
        # Import DockerException here for the same lazy-import reason
        try:
            from docker.errors import DockerException
        except ImportError:
            DockerException = OSError  # type: ignore[misc,assignment]

        with tempfile.TemporaryDirectory() as workspace:
            script_path = os.path.join(workspace, "script.py")
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(code)

            start = time.monotonic()
            container = None
            try:
                container = self._client.containers.run(
                    self._image,
                    command=["python", "/workspace/script.py"],
                    volumes={workspace: {"bind": "/workspace", "mode": "ro"}},
                    mem_limit=self._memory_limit,
                    cpu_quota=self._cpu_quota,
                    network_disabled=True,
                    remove=False,
                    detach=True,
                )
                logger.debug(
                    "docker sandbox container %s started (image=%s timeout=%ds)",
                    container.short_id, self._image, timeout_seconds,
                )

                timed_out = False
                try:
                    result = container.wait(timeout=timeout_seconds)
                    exit_code: int = result["StatusCode"]
                except Exception:
                    container.kill()
                    timed_out = True
                    exit_code = -1
                    logger.warning(
                        "docker sandbox container %s timed out after %ds",
                        container.short_id, timeout_seconds,
                    )

                duration = time.monotonic() - start
                stdout = container.logs(stdout=True, stderr=False).decode(
                    "utf-8", errors="replace"
                )
                stderr = container.logs(stdout=False, stderr=True).decode(
                    "utf-8", errors="replace"
                )

                logger.debug(
                    "docker sandbox finished: exit_code=%d timed_out=%s duration=%.2fs",
                    exit_code, timed_out, duration,
                )
                return SandboxResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    timed_out=timed_out,
                    duration_seconds=duration,
                )

            except DockerException as exc:
                raise SandboxError(f"Docker execution error: {exc}") from exc
            finally:
                if container is not None:
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass  # best-effort cleanup; container may already be gone
