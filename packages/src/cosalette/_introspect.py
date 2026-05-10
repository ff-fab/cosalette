"""Compatibility stub: _introspect moved to cosalette._mcp._introspect_impl.

Deprecated: import from cosalette._mcp._introspect_impl directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._mcp._introspect_impl import (
    build_registry_snapshot,
    format_asyncapi_table,
    format_registry_json,
    format_registry_table,
)

__all__ = [
    "build_registry_snapshot",
    "format_asyncapi_table",
    "format_registry_json",
    "format_registry_table",
]
