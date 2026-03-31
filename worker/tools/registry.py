"""
Tool registry — maps tool names to callable implementations.
New tools are registered with @register_tool.
"""

from __future__ import annotations

from typing import Any, Callable

_REGISTRY: dict[str, Callable[..., Any]] = {}


class ToolError(Exception):
    """Raised by tool implementations for expected, non-retryable failures."""


def register_tool(name: str) -> Callable:
    """Decorator to register a tool implementation."""
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_tool(name: str) -> Callable[..., Any]:
    if name not in _REGISTRY:
        raise ToolError(f"Unknown tool: '{name}'. Registered tools: {list(_REGISTRY)}")
    return _REGISTRY[name]


def list_tools() -> list[str]:
    return list(_REGISTRY.keys())
