"""FastMCP server definition and tool registration for cosalette."""

from __future__ import annotations

from typing import Any


def create_server_instance() -> Any:
    """Create the FastMCP server instance with all tools registered."""
    from fastmcp import FastMCP

    mcp = FastMCP(
        "cosalette",
        instructions=(
            "cosalette IoT-to-MQTT framework assistant. Provides framework guidance, "
            "ADR context, app introspection, and scaffolding tools."
        ),
    )

    # Register tools from each module
    from cosalette._mcp._adrs import register_adr_tools
    from cosalette._mcp._config import register_config_tools
    from cosalette._mcp._guidance import register_guidance_tools
    from cosalette._mcp._introspect import register_introspect_tools
    from cosalette._mcp._scaffolding import register_scaffolding_tools

    register_guidance_tools(mcp)
    register_adr_tools(mcp)
    register_introspect_tools(mcp)
    register_config_tools(mcp)
    register_scaffolding_tools(mcp)

    return mcp
