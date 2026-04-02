"""
Unit tests for the sandbox abstraction — Milestone 4.

SubprocessSandbox: tested directly (no Docker required).
DockerSandbox:     tested with mocked docker SDK (no daemon required).
Factory:           tested with patched config.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from worker.sandbox.base import SandboxError, SandboxResult
from worker.sandbox.subprocess_sandbox import SubprocessSandbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docker_module() -> ModuleType:
    """Build a minimal fake `docker` module for use in tests."""
    docker_mod = ModuleType("docker")
    errors_mod = ModuleType("docker.errors")

    class DockerException(Exception):
        pass

    errors_mod.DockerException = DockerException  # type: ignore[attr-defined]
    docker_mod.errors = errors_mod  # type: ignore[attr-defined]
    docker_mod.from_env = MagicMock()  # type: ignore[attr-defined]
    return docker_mod


# ---------------------------------------------------------------------------
# SubprocessSandbox — live execution tests (no mocks needed)
# ---------------------------------------------------------------------------

class TestSubprocessSandbox:
    def test_stdout_captured(self) -> None:
        result = SubprocessSandbox().run("print('hello')", timeout_seconds=5)
        assert "hello" in result.stdout
        assert result.exit_code == 0
        assert not result.timed_out

    def test_stderr_captured(self) -> None:
        result = SubprocessSandbox().run(
            "import sys; sys.stderr.write('err_output')", timeout_seconds=5
        )
        assert "err_output" in result.stderr

    def test_nonzero_exit_code(self) -> None:
        result = SubprocessSandbox().run("raise SystemExit(42)", timeout_seconds=5)
        assert result.exit_code == 42
        assert not result.timed_out

    def test_duration_is_positive(self) -> None:
        result = SubprocessSandbox().run("pass", timeout_seconds=5)
        assert result.duration_seconds > 0

    def test_result_is_sandbox_result_dataclass(self) -> None:
        result = SubprocessSandbox().run("pass", timeout_seconds=5)
        assert isinstance(result, SandboxResult)

    def test_backend_name(self) -> None:
        assert SubprocessSandbox().backend_name == "subprocess"

    def test_timeout_returns_timed_out_result(self) -> None:
        """Process must be killed and timed_out=True returned (not an exception)."""
        result = SubprocessSandbox().run(
            "import time; time.sleep(60)", timeout_seconds=1
        )
        assert result.timed_out
        assert result.exit_code == -1
        assert result.stdout == ""

    def test_syntax_error_captured_in_stderr(self) -> None:
        result = SubprocessSandbox().run("def :", timeout_seconds=5)
        assert result.exit_code != 0
        assert result.stderr


# ---------------------------------------------------------------------------
# DockerSandbox — mocked docker SDK (no daemon required)
# ---------------------------------------------------------------------------

def _make_container(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    exit_code: int = 0,
    wait_raises: Exception | None = None,
) -> MagicMock:
    container = MagicMock()
    container.short_id = "abc123"

    if wait_raises is not None:
        container.wait.side_effect = wait_raises
    else:
        container.wait.return_value = {"StatusCode": exit_code}

    # Capture the bytes in local variables to avoid name shadowing in the closure
    _out, _err = stdout, stderr

    def _logs(stdout=True, stderr=False):  # type: ignore[override]
        if stdout and not stderr:
            return _out
        if stderr and not stdout:
            return _err
        return _out + _err

    container.logs.side_effect = _logs
    return container


class TestDockerSandboxInit:
    def test_raises_sandbox_error_when_docker_not_installed(self) -> None:
        """ImportError on `import docker` must surface as SandboxError."""
        # Temporarily hide the docker module
        saved = sys.modules.pop("docker", None)
        sys.modules["docker"] = None  # type: ignore[assignment]
        try:
            # Re-import DockerSandbox so it re-executes the lazy import
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            with pytest.raises(SandboxError, match="docker package"):
                ds_mod.DockerSandbox(image="python:3.11-slim")
        finally:
            if saved is None:
                sys.modules.pop("docker", None)
            else:
                sys.modules["docker"] = saved

    def test_raises_sandbox_error_when_daemon_unavailable(self) -> None:
        """Exception from docker.from_env() must surface as SandboxError."""
        fake_docker = _make_docker_module()
        fake_docker.from_env.side_effect = Exception("daemon not running")

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            with pytest.raises(SandboxError, match="Cannot connect"):
                ds_mod.DockerSandbox(image="python:3.11-slim")


class TestDockerSandboxRun:
    """All tests inject a fake docker module — no daemon required."""

    def _sandbox(self, container: MagicMock) -> tuple:
        """Return (sandbox, fake_docker_module) with container pre-wired."""
        fake_docker = _make_docker_module()
        fake_docker.from_env.return_value.containers.run.return_value = container
        return fake_docker

    def test_successful_run_captures_exit_code(self) -> None:
        container = _make_container(stdout=b"hello\n", exit_code=0)
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            result = ds_mod.DockerSandbox(image="python:3.11-slim").run(
                "print('hello')", timeout_seconds=10
            )

        assert result.exit_code == 0
        assert not result.timed_out
        assert result.duration_seconds >= 0

    def test_nonzero_exit_code_captured(self) -> None:
        container = _make_container(exit_code=1)
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            result = ds_mod.DockerSandbox(image="python:3.11-slim").run(
                "raise SystemExit(1)", timeout_seconds=10
            )

        assert result.exit_code == 1
        assert not result.timed_out

    def test_timeout_kills_container_and_sets_timed_out(self) -> None:
        container = _make_container(wait_raises=TimeoutError("timed out"))
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            result = ds_mod.DockerSandbox(image="python:3.11-slim").run(
                "import time; time.sleep(999)", timeout_seconds=1
            )

        container.kill.assert_called_once()
        assert result.timed_out
        assert result.exit_code == -1

    def test_container_removed_after_successful_run(self) -> None:
        container = _make_container(exit_code=0)
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            ds_mod.DockerSandbox(image="python:3.11-slim").run("pass", timeout_seconds=5)

        container.remove.assert_called_once_with(force=True)

    def test_container_removed_even_after_timeout(self) -> None:
        container = _make_container(wait_raises=TimeoutError())
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            ds_mod.DockerSandbox(image="python:3.11-slim").run("pass", timeout_seconds=1)

        container.remove.assert_called_once_with(force=True)

    def test_backend_name(self) -> None:
        fake_docker = _make_docker_module()

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            assert ds_mod.DockerSandbox(image="python:3.11-slim").backend_name == "docker"

    def test_network_disabled(self) -> None:
        """Container must always be started with network_disabled=True."""
        container = _make_container()
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            ds_mod.DockerSandbox(image="python:3.11-slim").run("pass", timeout_seconds=5)

        _, kwargs = fake_docker.from_env.return_value.containers.run.call_args
        assert kwargs.get("network_disabled") is True

    def test_memory_limit_applied(self) -> None:
        container = _make_container()
        fake_docker = self._sandbox(container)

        with patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)
            ds_mod.DockerSandbox(image="python:3.11-slim").run("pass", timeout_seconds=5)

        _, kwargs = fake_docker.from_env.return_value.containers.run.call_args
        assert kwargs.get("mem_limit") == ds_mod._DEFAULT_MEMORY_LIMIT


# ---------------------------------------------------------------------------
# Sandbox factory
# ---------------------------------------------------------------------------

class TestSandboxFactory:
    def test_subprocess_backend_by_default(self) -> None:
        from worker.sandbox.factory import get_sandbox

        with patch("worker.config.settings") as mock_settings:
            mock_settings.sandbox_backend = "subprocess"
            sandbox = get_sandbox()

        assert isinstance(sandbox, SubprocessSandbox)

    def test_unknown_backend_falls_back_to_subprocess(self) -> None:
        """An unrecognised backend name falls through to subprocess (safe default)."""
        from worker.sandbox.factory import get_sandbox

        with patch("worker.config.settings") as mock_settings:
            mock_settings.sandbox_backend = "unknown_backend"
            sandbox = get_sandbox()

        assert isinstance(sandbox, SubprocessSandbox)

    def test_docker_backend_when_configured(self) -> None:
        """SANDBOX_BACKEND=docker produces a sandbox with backend_name 'docker'."""
        from worker.sandbox.factory import get_sandbox

        fake_docker = _make_docker_module()
        with (
            patch("worker.config.settings") as mock_settings,
            patch.dict(sys.modules, {"docker": fake_docker, "docker.errors": fake_docker.errors}),
        ):
            import importlib
            import worker.sandbox.docker_sandbox as ds_mod
            importlib.reload(ds_mod)

            mock_settings.sandbox_backend = "docker"
            mock_settings.sandbox_image = "python:3.11-slim"
            sandbox = get_sandbox()

        assert sandbox.backend_name == "docker"
