"""cosalette MCP (Model Context Protocol) server.

Provides MCP tools for framework guidance, ADR context, and app introspection.
Entry point for downstream AI agents to access cosalette framework knowledge
and development guidance.
"""

from __future__ import annotations

from typing import Any


def create_server() -> Any:
    """Create the cosalette MCP server with all tools registered.

    Returns:
        FastMCP instance with all cosalette tools registered

    Note:
        Assumes fastmcp is available. Import check should be done by caller.
    """
    from cosalette._mcp._server import create_server_instance

    return create_server_instance()
