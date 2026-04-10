"""CLI subcommands for schema validation and tooling.

Provides ``cosalette schema validate|check|dump|init|slice``
subcommands for static validation, CI gating, and schema generation.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from cosalette._schema import SchemaRegistry
from cosalette._schema_enforcement import _validate_registrations
from cosalette._schema_loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema,
)

if TYPE_CHECKING:
    from cosalette._app import App

# ---------------------------------------------------------------------------
# Exit codes (mirrored from _cli to avoid circular import)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1

# ---------------------------------------------------------------------------
# Schema subcommand group
# ---------------------------------------------------------------------------

schema_app = typer.Typer(help="Schema validation and tooling.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_schema_or_exit(path: Path) -> SchemaRegistry:
    """Load schema from file path or exit with error.

    Args:
        path: Path to the schema file.

    Returns:
        Parsed SchemaRegistry.

    Note:
        On SchemaLoadError, prints errors and exits with EXIT_CONFIG_ERROR.
    """
    source = FileSchemaSource(path=path)
    try:
        return asyncio.run(load_schema(source))
    except SchemaLoadError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc


def _registry_to_asyncapi_dict(registry: SchemaRegistry) -> dict[str, Any]:
    """Convert a SchemaRegistry back to an AsyncAPI-like dict for YAML output.

    Reconstructs a minimal AsyncAPI document from the filtered SchemaRegistry.
    Does not include operations as they reference channels by $ref and would
    break without the full document.

    Args:
        registry: The SchemaRegistry to convert.

    Returns:
        AsyncAPI-compatible dict structure.
    """

    result: dict[str, Any] = {
        "asyncapi": registry.asyncapi_version,
        "info": {
            "title": registry.app_name or "Filtered Schema",
            "version": registry.app_version,
        },
    }

    # Add enforcement config if present
    if (
        registry.enforcement.mode != "off"
        or registry.enforcement.on_configure is not True
        or registry.enforcement.on_publish is not False
        or registry.enforcement.network_level is not False
    ):
        result["x-cosalette-enforcement"] = {
            "mode": registry.enforcement.mode,
            "on_configure": registry.enforcement.on_configure,
            "on_publish": registry.enforcement.on_publish,
            "network_level": registry.enforcement.network_level,
        }

    # Add channels
    channels: dict[str, Any] = {}
    for name, channel in registry.channels.items():
        channel_dict: dict[str, Any] = {
            "address": channel.address,
        }

        # Add x-cosalette extensions
        if channel.app_name:
            channel_dict["x-cosalette-app"] = channel.app_name
        if channel.archetype:
            channel_dict["x-cosalette-archetype"] = channel.archetype
        if channel.scope:
            channel_dict["x-cosalette-scope"] = channel.scope
        if channel.coalescing_group:
            channel_dict["x-cosalette-coalescing-group"] = channel.coalescing_group

        # Add MQTT binding if non-default
        if channel.mqtt_binding.qos != 1 or channel.mqtt_binding.retain is not False:
            channel_dict["bindings"] = {
                "mqtt": {
                    "qos": channel.mqtt_binding.qos,
                    "retain": channel.mqtt_binding.retain,
                }
            }

        # Add capability requirements
        if channel.capability_requirements:
            reqs = []
            for req in channel.capability_requirements:
                req_dict = {"tag": req.tag}
                if req.description:
                    req_dict["description"] = req.description
                reqs.append(req_dict)
            channel_dict["x-cosalette-requires"] = reqs

        # Add message payload schema if present
        if channel.payload_schema:
            channel_dict["messages"] = {
                channel.message_name or "message": {"payload": channel.payload_schema}
            }

        channels[name] = channel_dict

    if channels:
        result["channels"] = channels

    return result


def _import_app(spec: str) -> App:
    """Import App instance from module:attribute specification.

    Args:
        spec: Import specification in format "module.path:attribute"
              (e.g., "myapp.main:app" or "myapp:app")

    Returns:
        The App instance.

    Raises:
        typer.Exit: On import failures or invalid specifications.
    """
    # Import App here to avoid circular imports at module level
    from cosalette._app import App

    if ":" not in spec:
        typer.echo(
            f"Error: Invalid app spec '{spec}'. "
            "Expected format: 'module.path:attribute'",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    module_path, attr_name = spec.rsplit(":", 1)

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        typer.echo(
            f"Error: Could not import module '{module_path}': {exc}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    try:
        obj = getattr(module, attr_name)
    except AttributeError as exc:
        typer.echo(
            f"Error: Module '{module_path}' has no attribute '{attr_name}'",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    if not isinstance(obj, App):
        typer.echo(
            f"Error: '{spec}' is not an App instance (got {type(obj).__name__})",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    return obj


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@schema_app.command()
def validate(
    path: Annotated[
        Path,
        typer.Argument(
            help="Path to AsyncAPI schema file",
            exists=True,
            file_okay=True,
            readable=True,
        ),
    ],
) -> None:
    """Validate an AsyncAPI schema document.

    Loads and validates the schema file, checking for structural
    correctness and cosalette-specific extensions.
    """
    registry = _load_schema_or_exit(path)

    # For network-level schemas, we need to get the title from the original document
    # since registry.app_name is set to None for network schemas
    if registry.enforcement.network_level and registry.app_name is None:
        # Load the YAML again to get the title - this is inefficient but simpler
        import yaml

        yaml_content = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(yaml_content)
        title = doc.get("info", {}).get("title", "(untitled)")
    else:
        title = registry.app_name or "(untitled)"

    # Print summary
    typer.echo(f"✅ Schema validated: {title} v{registry.app_version}")
    typer.echo(f"   AsyncAPI version: {registry.asyncapi_version}")
    typer.echo(f"   Channels: {len(registry.channels)}")

    if registry.enforcement.network_level:
        app_count = len(registry.all_app_names())
        typer.echo(f"   Apps in network: {app_count}")
        typer.echo("   Schema type: network-level")
    else:
        typer.echo("   Schema type: single-app")

    raise typer.Exit(EXIT_OK)


@schema_app.command()
def slice(
    network: Annotated[
        Path,
        typer.Option(
            "--network",
            help="Path to network-level schema file",
            exists=True,
            file_okay=True,
            readable=True,
        ),
    ],
    app: Annotated[
        str,
        typer.Option("--app", help="App name to extract from network schema"),
    ],
) -> None:
    """Extract app portion from network schema.

    Filters a network-level schema to include only channels relevant
    to the specified app, outputting the result as YAML.
    """
    registry = _load_schema_or_exit(network)

    # Verify this is a network-level schema
    if not registry.enforcement.network_level:
        typer.echo(
            "Error: Schema is not network-level. Use --network flag only with "
            "schemas that have x-cosalette-enforcement.network_level: true",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    # Check if app exists in schema
    available_apps = registry.all_app_names()
    if app not in available_apps:
        typer.echo(
            f"Error: App '{app}' not found in schema. "
            f"Available apps: {', '.join(sorted(available_apps))}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    # Filter for the specified app
    filtered_registry = registry.filter_for_app(app)

    # Convert back to AsyncAPI dict and output as YAML
    import yaml

    output_dict = _registry_to_asyncapi_dict(filtered_registry)
    yaml_output = yaml.safe_dump(output_dict, default_flow_style=False, sort_keys=False)
    typer.echo(yaml_output.rstrip())

    raise typer.Exit(EXIT_OK)


@schema_app.command()
def check(
    app_spec: Annotated[
        str, typer.Option("--app", help="App import spec (module:attr)")
    ],
    schema_path: Annotated[
        Path,
        typer.Option(
            "--schema",
            help="Path to schema file",
            exists=True,
            file_okay=True,
            readable=True,
        ),
    ],
) -> None:
    """Check app registrations against schema (CI gate).

    Imports the specified app, extracts its registrations, loads the schema,
    and validates that all schema-expected devices are registered.
    Returns exit code 0 for compliance, 1 for violations.
    """
    # Import the app
    app = _import_app(app_spec)

    # Extract registered names
    registered_names = app._registered_names()

    # Load schema
    registry = _load_schema_or_exit(schema_path)

    # For network-level schemas, filter to this app's slice
    if registry.enforcement.network_level:
        # Verify the app exists in the schema
        available_apps = registry.all_app_names()
        app_name = app._name
        if app_name not in available_apps:
            typer.echo(
                f"Error: App '{app_name}' not found in schema. "
                f"Available apps: {', '.join(sorted(available_apps))}",
                err=True,
            )
            raise typer.Exit(EXIT_CONFIG_ERROR)

        # Filter for the app
        registry = registry.filter_for_app(app_name)

    # Validate registrations
    violations = _validate_registrations(registered_names, registry)

    # Build a set of missing device names for display
    missing_devices = registry.device_names - registered_names

    # Print header with schema and app info
    typer.echo(f"Schema: {schema_path} (v{registry.app_version})")
    typer.echo(f"App:    {app._name}")
    typer.echo()

    # Count findings
    missing_count = 0
    scope_violation_count = 0
    compliant_count = 0
    extra_count = 0

    # Print missing devices
    for device_name in sorted(missing_devices):
        missing_count += 1
        typer.echo(f"✗ {device_name} — MISSING")
        typer.echo(
            f"    Schema expects device '{device_name}' but no registration found"
        )
        typer.echo()

    # Print scope violations (non-device violations)
    for violation in violations:
        if violation.category == "scope_violation":
            scope_violation_count += 1
            typer.echo(f"✗ {violation.channel_name or 'unknown'} — SCOPE VIOLATION")
            typer.echo(f"    {violation.message}")
            typer.echo()

    # Find compliant devices (registered and in schema)
    schema_device_names = registry.device_names
    for device_name in sorted(registered_names):
        if device_name in schema_device_names:
            compliant_count += 1
            typer.echo(f"✓ {device_name} — OK")
        else:
            # Extra device (registered but not in schema)
            extra_count += 1
            typer.echo(f"⚠ {device_name} — EXTRA")
            typer.echo("    Device registered but not found in schema")

    # Print summary
    typer.echo()
    if missing_count > 0 or scope_violation_count > 0:
        if extra_count > 0:
            typer.echo(
                f"Result: {missing_count} missing, {extra_count} extra, "
                f"{compliant_count} compliant"
            )
        else:
            typer.echo(
                f"Result: {missing_count + scope_violation_count} violations, "
                f"{compliant_count} compliant"
            )
        typer.echo("Exit code: 1")
        raise typer.Exit(EXIT_CONFIG_ERROR)
    else:
        if extra_count > 0:
            typer.echo(f"Result: {extra_count} extra, {compliant_count} compliant")
        else:
            typer.echo(f"Result: 0 violations, {compliant_count} compliant")
        typer.echo("Exit code: 0")
        raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------------------
# CLI entry point (for standalone usage)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    schema_app()
