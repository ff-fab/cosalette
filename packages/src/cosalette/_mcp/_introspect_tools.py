"""App introspection tools for the cosalette MCP server.

Provides tools for inspecting cosalette application structure,
devices, adapters, and registrations.

Security: These tools accept user-provided ``module:attribute`` specs and
import them dynamically.  See ``_imports.py`` for risk discussion.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

# Cache registry snapshots: app_spec → snapshot dict (LRU, max 32 entries)
_snapshot_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_SNAPSHOTS = 32


def _get_or_build_snapshot(app_spec: str, app: Any) -> dict[str, Any]:
    """Return a cached registry snapshot, building on first access (LRU, max 32)."""
    if app_spec in _snapshot_cache:
        _snapshot_cache.move_to_end(app_spec)
        return _snapshot_cache[app_spec]
    from cosalette._mcp._introspect import build_registry_snapshot

    snapshot = build_registry_snapshot(app)
    _snapshot_cache[app_spec] = snapshot
    if len(_snapshot_cache) > _MAX_SNAPSHOTS:
        _snapshot_cache.popitem(last=False)
    return snapshot


def _import_app(spec: str) -> tuple[Any, str | None]:
    """Import and validate an App instance from spec.

    Returns:
        ``(app, None)`` on success, ``(None, error_message)`` on failure.
    """
    from cosalette._mcp._imports import import_from_spec

    obj, err = import_from_spec(spec)
    if err is not None:
        return None, err

    from cosalette._app import App

    if not isinstance(obj, App):
        actual_type = type(obj).__name__
        return None, f"❌ '{spec}' is not an App instance (found {actual_type})"

    return obj, None


def register_introspect_tools(mcp: Any) -> None:
    """Register introspection tools with the MCP server."""

    @mcp.tool()
    def cosalette_inspect_app(app_spec: str) -> str:
        """Inspect a cosalette application and return its registry snapshot.

        Imports the module specified by *app_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).

        Args:
            app_spec: App specification in format "module.path:attribute"
                     (e.g., "myapp.main:app" or "myapp:app")

        Returns:
            JSON string containing app metadata, devices, telemetry,
            commands, and adapters
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        from cosalette._mcp._introspect import format_registry_json

        snapshot = _get_or_build_snapshot(app_spec, app)
        return format_registry_json(snapshot)

    @mcp.tool()
    def cosalette_inspect_device(app_spec: str, device_name: str) -> str:
        """Inspect a specific device in a cosalette application.

        Imports the module specified by *app_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).

        Args:
            app_spec: App specification in format "module.path:attribute"
            device_name: Name of the device to inspect

        Returns:
            JSON string containing the device information, or error message
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        snapshot = _get_or_build_snapshot(app_spec, app)

        # Find the device in the devices list
        for device in snapshot["devices"]:
            if device["name"] == device_name:
                return json.dumps(device, indent=2)

        available = [d["name"] for d in snapshot["devices"]]
        return (
            f"❌ Device '{device_name}' not found in app. "
            f"Available devices: {available}"
        )

    @mcp.tool()
    def cosalette_inspect_adapters(app_spec: str) -> str:
        """Inspect all adapters in a cosalette application.

        Imports the module specified by *app_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).

        Args:
            app_spec: App specification in format "module.path:attribute"

        Returns:
            JSON string containing the list of adapters, or error message
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        snapshot = _get_or_build_snapshot(app_spec, app)

        return json.dumps(snapshot["adapters"], indent=2)

    @mcp.tool()
    def cosalette_manifest(app_spec: str) -> str:
        """Return the canonical AsyncAPI contract for a cosalette application.

        Returns the full AsyncAPI 3.0.0 document as JSON, including typed
        payload schemas, operations, components, and contract metadata
        (summary, behavior, effects, x-cosalette-contract-version).

        Imports the module specified by *app_spec*, which executes the
        module's top-level code; imports are gated by the
        COSALETTE_MCP_IMPORT_ALLOW allowlist (see the _imports security note).

        Args:
            app_spec: App specification in format "module.path:attribute"
                     (e.g., "myapp.main:app" or "myapp:app")

        Returns:
            JSON string containing the canonical AsyncAPI contract.
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        asyncapi_dict = app.asyncapi()
        return json.dumps(asyncapi_dict, indent=2)
