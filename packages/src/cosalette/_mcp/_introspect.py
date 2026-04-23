"""App introspection tools for the cosalette MCP server.

Provides tools for inspecting cosalette application structure,
devices, adapters, and registrations.

Security: These tools accept user-provided ``module:attribute`` specs and
import them dynamically.  See ``_imports.py`` for risk discussion.
"""

from __future__ import annotations

from typing import Any

# Cache registry snapshots: app_spec → snapshot dict
_snapshot_cache: dict[str, dict[str, Any]] = {}


def _get_or_build_snapshot(app_spec: str, app: Any) -> dict[str, Any]:
    """Return a cached registry snapshot, building on first access."""
    if app_spec not in _snapshot_cache:
        from cosalette._introspect import build_registry_snapshot

        _snapshot_cache[app_spec] = build_registry_snapshot(app)
    return _snapshot_cache[app_spec]


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

        Imports the module specified by *app_spec* (local-only, see security
        note in module docstring).

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

        from cosalette._introspect import format_registry_json

        snapshot = _get_or_build_snapshot(app_spec, app)
        return format_registry_json(snapshot)

    @mcp.tool()
    def cosalette_inspect_device(app_spec: str, device_name: str) -> str:
        """Inspect a specific device in a cosalette application.

        Imports the module specified by *app_spec* (local-only, see security
        note in module docstring).

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
                import json

                return json.dumps(device, indent=2)

        available = [d["name"] for d in snapshot["devices"]]
        return (
            f"❌ Device '{device_name}' not found in app. "
            f"Available devices: {available}"
        )

    @mcp.tool()
    def cosalette_inspect_adapters(app_spec: str) -> str:
        """Inspect all adapters in a cosalette application.

        Imports the module specified by *app_spec* (local-only, see security
        note in module docstring).

        Args:
            app_spec: App specification in format "module.path:attribute"

        Returns:
            JSON string containing the list of adapters, or error message
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        snapshot = _get_or_build_snapshot(app_spec, app)

        import json

        return json.dumps(snapshot["adapters"], indent=2)

    @mcp.tool()
    def cosalette_manifest(app_spec: str) -> str:
        """Return the contract-first manifest for a cosalette application.

        Returns the full registry snapshot as JSON, including contract metadata
        (summary, state_model, payload_model, behavior, effects), interval and
        enabled settings values or setting-reference field names, triggerable flag,
        and publish strategy and persistence policy.

        Imports the module specified by *app_spec* (local-only, see security
        note in module docstring).

        Args:
            app_spec: App specification in format "module.path:attribute"
                     (e.g., "myapp.main:app" or "myapp:app")

        Returns:
            JSON string containing the full app manifest.
        """
        app, err = _import_app(app_spec)
        if err is not None:
            return err

        from cosalette._introspect import format_registry_json

        snapshot = _get_or_build_snapshot(app_spec, app)
        return format_registry_json(snapshot)
