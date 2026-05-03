"""Public entry point for ``python -m cosalette.mcp``.

Thin wrapper over the private ``cosalette._mcp`` package so the public
invocation documented in ADR-035 works:

    python -m cosalette.mcp

See Also:
    ``cosalette._mcp`` — implementation details.
"""

from __future__ import annotations

from cosalette._mcp import create_server

__all__ = ["create_server"]

if __name__ == "__main__":
    server = create_server()
    server.run(transport="stdio")
