"""App introspection tools for the cosalette MCP server.

Provides tools for inspecting cosalette application structure,
devices, adapters, and registrations.
"""

from __future__ import annotations

import importlib
from typing import Any


def register_introspect_tools(mcp: Any) -> None:
    """Register introspection tools with the MCP server."""

    @mcp.tool()
    def cosalette_inspect_app(app_spec: str) -> str:
        """Inspect a cosalette application and return its registry snapshot.

        Args:
            app_spec: App specification in format "module.path:attribute"
                     (e.g., "myapp.main:app" or "myapp:app")

        Returns:
            JSON string containing app metadata, devices, telemetry,
            commands, and adapters
        """
        app = _import_app_instance(app_spec)
        if isinstance(app, str):
            return app  # Error message

        from cosalette._introspect import build_registry_snapshot, format_registry_json

        snapshot = build_registry_snapshot(app)
        return format_registry_json(snapshot)

    @mcp.tool()
    def cosalette_inspect_device(app_spec: str, device_name: str) -> str:
        """Inspect a specific device in a cosalette application.

        Args:
            app_spec: App specification in format "module.path:attribute"
            device_name: Name of the device to inspect

        Returns:
            JSON string containing the device information, or error message
        """
        app = _import_app_instance(app_spec)
        if isinstance(app, str):
            return app  # Error message

        from cosalette._introspect import build_registry_snapshot

        snapshot = build_registry_snapshot(app)

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

        Args:
            app_spec: App specification in format "module.path:attribute"

        Returns:
            JSON string containing the list of adapters, or error message
        """
        app = _import_app_instance(app_spec)
        if isinstance(app, str):
            return app  # Error message

        from cosalette._introspect import build_registry_snapshot

        snapshot = build_registry_snapshot(app)

        import json

        return json.dumps(snapshot["adapters"], indent=2)


def _import_app_instance(spec: str) -> Any:
    """Import App instance from module:attribute specification.

    Args:
        spec: Import specification in format "module.path:attribute"

    Returns:
        App instance on success, error message string on failure
    """
    spec = spec.strip()
    if ":" not in spec:
        return f"❌ Invalid app spec '{spec}'. Expected format: 'module.path:attribute'"

    try:
        module_path, attr_name = spec.rsplit(":", 1)
        module_path = module_path.strip()
        attr_name = attr_name.strip()

        # Import the module
        module = importlib.import_module(module_path)

        # Get the attribute
        if not hasattr(module, attr_name):
            return f"❌ Module '{module_path}' has no attribute '{attr_name}'"

        app_instance = getattr(module, attr_name)

        # Validate it's an App instance (late import to avoid circular imports)
        from cosalette._app import App

        if not isinstance(app_instance, App):
            actual_type = type(app_instance).__name__
            return f"❌ '{spec}' is not an App instance (found {actual_type})"

        return app_instance

    except ImportError as e:
        return f"❌ Could not import module '{module_path}': {e}"
    except Exception as e:
        return f"❌ Error importing '{spec}': {e}"
