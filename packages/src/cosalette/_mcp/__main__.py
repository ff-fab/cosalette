"""Allow running the MCP server via python -m cosalette._mcp."""

from __future__ import annotations

if __name__ == "__main__":
    from cosalette._mcp import create_server

    server = create_server()
    server.run()  # Defaults to stdio transport
