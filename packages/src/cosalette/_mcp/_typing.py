"""Typing protocols for optional MCP integrations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast


class ToolRegistrar(Protocol):
    """Minimal shape of FastMCP needed by tool-registration modules."""

    def tool[**P, R](self, **kwargs: Any) -> Callable[[Callable[P, R]], object]: ...


def as_tool_registrar(value: Any) -> ToolRegistrar:
    """Type FastMCP's dynamic ``tool`` decorator at the optional boundary."""
    return cast(ToolRegistrar, value)
