"""Scaffolding tools for the cosalette MCP server.

Generates idiomatic cosalette boilerplate (devices, adapters, tests) using
Jinja2 templates.  Templates are app-aware: when an ``app_spec`` is provided
the tool uses introspection to avoid name collisions and suggest matching
adapter ports from the registry.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _validate_identifier(value: str, label: str) -> str | None:
    """Return an error string if *value* is not a valid Python identifier."""
    if not _IDENTIFIER_RE.match(value):
        return (
            f"❌ Invalid {label} '{value}': must be a valid Python identifier "
            "(letters, digits, underscores; cannot start with a digit)."
        )
    return None


def _validate_module_path(value: str, label: str) -> str | None:
    """Return an error string if *value* is not a valid dotted module path."""
    if not _MODULE_PATH_RE.match(value):
        return (
            f"❌ Invalid {label} '{value}': must be a valid dotted module path "
            "(e.g. 'adapters' or 'myapp.adapters')."
        )
    return None


def _validate_optional_identifier(value: str | None, label: str) -> str | None:
    """Like _validate_identifier, but returns None immediately when value is None."""
    if value is None:
        return None
    return _validate_identifier(value, label)


def _validate_optional_module_path(value: str | None, label: str) -> str | None:
    """Like _validate_module_path, but returns None immediately when value is None."""
    if value is None:
        return None
    return _validate_module_path(value, label)


def _validate_interval(interval: float) -> str | None:
    """Return an error string if *interval* is not a valid positive finite number."""
    if not math.isfinite(interval) or interval <= 0:
        return (
            f"❌ Invalid interval '{interval}': must be a positive finite number "
            "(e.g. 60.0). Got nan, inf, 0, or a negative value."
        )
    return None


# ---------------------------------------------------------------------------
# Port name derivation helpers (DRY — used by device, adapter, and test scaffolding)
# ---------------------------------------------------------------------------


def _derive_dry_run_name(port_name: str) -> str:
    """Derive the dry-run adapter class name from a port name.

    >>> _derive_dry_run_name("TemperaturePort")
    'FakeTemperature'
    """
    return "Fake" + port_name.removesuffix("Port")


def _derive_impl_name(port_name: str) -> str:
    """Derive the concrete adapter class name from a port name.

    >>> _derive_impl_name("TemperaturePort")
    'TemperatureAdapter'
    """
    return port_name.removesuffix("Port") + "Adapter"


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "_templates"


def _render_template(template_name: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template from the ``_templates/`` directory."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        # autoescape intentionally disabled: templates generate Python source code,
        # not HTML/XML.  Enabling it would corrupt operators and string literals.
        autoescape=select_autoescape([]),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_name)
    result: str = template.render(context)
    return result


def _to_pascal(name: str) -> str:
    """Convert a snake_case name to PascalCase."""
    return "".join(part.capitalize() for part in name.split("_"))


def _existing_names(app_spec: str | None) -> set[str]:
    """Return device and telemetry names already registered in the app."""
    if app_spec is None:
        return set()
    try:
        from cosalette._mcp._introspect import _get_or_build_snapshot, _import_app

        app, err = _import_app(app_spec)
        if err is not None:
            return set()
        snapshot = _get_or_build_snapshot(app_spec, app)
        names: set[str] = set()
        for key in ("devices", "telemetry"):  # commands may share a name with telemetry
            for entry in snapshot.get(key, []):
                names.add(entry["name"])
        return names
    except Exception:  # noqa: BLE001  — best-effort introspection
        return set()


def _registered_ports(app_spec: str | None) -> list[str]:
    """Return adapter port type names already registered in the app."""
    if app_spec is None:
        return []
    try:
        from cosalette._mcp._introspect import _get_or_build_snapshot, _import_app

        app, err = _import_app(app_spec)
        if err is not None:
            return []
        snapshot = _get_or_build_snapshot(app_spec, app)
        return [a["port"] for a in snapshot.get("adapters", [])]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Tool implementations (standalone functions for testability)
# ---------------------------------------------------------------------------


def _scaffold_device_impl(
    device_name: str,
    *,
    app_spec: str | None = None,
    adapter_port: str | None = None,
    adapter_module: str | None = None,
    interval: float = 60.0,
) -> str:
    """Render a telemetry device module from template."""
    if err := _validate_identifier(device_name, "device_name"):
        return err
    if err := _validate_optional_identifier(adapter_port, "adapter_port"):
        return err
    if err := _validate_optional_module_path(adapter_module, "adapter_module"):
        return err
    if err := _validate_interval(interval):
        return err

    existing = _existing_names(app_spec)
    if device_name in existing:
        return (
            f"⚠️ Name '{device_name}' is already registered in the app. "
            "Choose a different name or remove the existing registration first."
        )

    ports = _registered_ports(app_spec)
    port_hint = ""
    if adapter_port is None and ports:
        port_hint = (
            f"\n\n💡 Registered adapter ports: {', '.join(ports)}. "
            "Pass `adapter_port` to wire dependency injection."
        )

    context: dict[str, Any] = {
        "device_name": device_name,
        "func_name": device_name,
        "adapter_port": adapter_port,
        "adapter_module": adapter_module or "adapters",
        "interval": interval,
        "dry_run_name": _derive_dry_run_name(adapter_port) if adapter_port else None,
        "impl_name": _derive_impl_name(adapter_port) if adapter_port else None,
    }

    rendered = _render_template("device.py.j2", context)
    return rendered + port_hint


def _scaffold_adapter_impl(
    port_name: str,
    *,
    device_description: str = "a hardware sensor",
    return_type: str = "float",
    default_value: str = "0.0",
) -> str:
    """Render a port-and-adapter module from template."""
    if err := _validate_identifier(port_name, "port_name"):
        return err

    context: dict[str, Any] = {
        "port_name": port_name,
        "device_description": device_description,
        "return_type": return_type,
        "default_value": default_value,
        "impl_name": _derive_impl_name(port_name),
        "dry_run_name": _derive_dry_run_name(port_name),
    }

    return _render_template("adapter.py.j2", context)


def _scaffold_test_impl(
    device_name: str,
    *,
    func_name: str | None = None,
    device_module: str = "devices",
    adapter_port: str | None = None,
    adapter_module: str | None = None,
    dry_run_name: str | None = None,
    interval: float = 60.0,
) -> str:
    """Render a test module from template."""
    if err := _validate_identifier(device_name, "device_name"):
        return err
    fn = func_name or device_name
    if err := _validate_optional_identifier(func_name, "func_name"):
        return err
    if err := _validate_module_path(device_module, "device_module"):
        return err
    if err := _validate_optional_identifier(adapter_port, "adapter_port"):
        return err
    if err := _validate_interval(interval):
        return err

    if adapter_port and not dry_run_name:
        dry_run_name = _derive_dry_run_name(adapter_port)

    context: dict[str, Any] = {
        "device_name": device_name,
        "device_name_pascal": _to_pascal(device_name),
        "func_name": fn,
        "device_module": device_module,
        "adapter_port": adapter_port,
        "adapter_module": adapter_module or "adapters",
        "dry_run_name": dry_run_name,
        "interval": interval,
    }

    return _render_template("test.py.j2", context)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_scaffolding_tools(mcp: Any) -> None:
    """Register scaffolding tools with the MCP server."""

    @mcp.tool()
    def cosalette_scaffold_device(
        device_name: str,
        app_spec: str = "",
        adapter_port: str = "",
        adapter_module: str = "",
        interval: float = 60.0,
    ) -> str:
        """Generate a cosalette telemetry device module.

        Produces an idiomatic, runnable single-file app with an
        ``@app.telemetry`` decorator, composition root, and ``app.run()``
        entry point.  Optional adapter dependency injection is included
        when *adapter_port* is provided.

        When *app_spec* is provided, checks for name collisions and suggests
        registered adapter ports.

        Args:
            device_name: Snake_case name for the device (e.g. "temperature")
            app_spec: Optional "module.path:attribute" to an App instance for
                      introspection (collision check, port suggestions)
            adapter_port: Optional port Protocol class name to inject
                         (e.g. "TemperaturePort")
            adapter_module: Module path for the adapter import
                           (default: "adapters")
            interval: Telemetry polling interval in seconds (default: 60)

        Returns:
            Generated Python source code for the device module
        """
        return _scaffold_device_impl(
            device_name,
            app_spec=app_spec or None,
            adapter_port=adapter_port or None,
            adapter_module=adapter_module or None,
            interval=interval,
        )

    @mcp.tool()
    def cosalette_scaffold_adapter(
        port_name: str,
        device_description: str = "a hardware sensor",
        return_type: str = "float",
        default_value: str = "0.0",
    ) -> str:
        """Generate a cosalette port protocol and adapter pair.

        Produces:
        - A ``Protocol`` class (PEP 544) defining the port contract
        - A concrete adapter class (raises ``NotImplementedError``)
        - A dry-run / fake adapter for testing

        Args:
            port_name: PascalCase name ending in ``Port``
                      (e.g. "TemperaturePort")
            device_description: Human-readable description of the hardware
            return_type: Python type annotation for the ``read()`` return
            default_value: Literal value the dry-run adapter returns

        Returns:
            Generated Python source code for the adapter module
        """
        return _scaffold_adapter_impl(
            port_name,
            device_description=device_description,
            return_type=return_type,
            default_value=default_value,
        )

    @mcp.tool()
    def cosalette_scaffold_test(
        device_name: str,
        func_name: str = "",
        device_module: str = "devices",
        adapter_port: str = "",
        adapter_module: str = "",
        dry_run_name: str = "",
    ) -> str:
        """Generate tests for a cosalette device.

        Produces a test module with:
        - Unit tests verifying the device handler registration
        - Integration tests using ``AppHarness`` for end-to-end validation

        Args:
            device_name: Snake_case device name matching the handler
            func_name: Handler function name (defaults to device_name)
            device_module: Module path for the device import
                          (default: "devices")
            adapter_port: Optional port Protocol class name if the device
                         uses dependency injection
            adapter_module: Module path for the adapter import
                           (default: "adapters")
            dry_run_name: Dry-run adapter class name
                         (default: "Fake" + port_name without "Port")

        Returns:
            Generated Python test source code
        """
        return _scaffold_test_impl(
            device_name,
            func_name=func_name or None,
            device_module=device_module,
            adapter_port=adapter_port or None,
            adapter_module=adapter_module or None,
            dry_run_name=dry_run_name or None,
        )
