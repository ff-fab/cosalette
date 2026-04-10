"""CLI subcommands for schema validation and tooling.

Provides ``cosalette schema validate|check|dump|init|slice``
subcommands for static validation, CI gating, and schema generation.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from cosalette._introspect import build_registry_snapshot
from cosalette._schema import SchemaRegistry
from cosalette._schema_enforcement import _validate_registrations
from cosalette._schema_loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema,
)

if TYPE_CHECKING:
    from cosalette._schema import ChannelSchema

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


def _add_channel_extensions(
    channel: ChannelSchema, channel_dict: dict[str, Any]
) -> None:
    """Add x-cosalette extensions to channel dict.

    Args:
        channel: The ChannelSchema to read extensions from.
        channel_dict: The channel dict to add extensions to.
    """
    if channel.app_name:
        channel_dict["x-cosalette-app"] = channel.app_name
    if channel.archetype:
        channel_dict["x-cosalette-archetype"] = channel.archetype
    if channel.scope:
        channel_dict["x-cosalette-scope"] = channel.scope
    if channel.coalescing_group:
        channel_dict["x-cosalette-coalescing-group"] = channel.coalescing_group


def _add_mqtt_binding(channel: ChannelSchema, channel_dict: dict[str, Any]) -> None:
    """Add MQTT binding to channel dict if non-default.

    Args:
        channel: The ChannelSchema to read MQTT binding from.
        channel_dict: The channel dict to add binding to.
    """
    if channel.mqtt_binding.qos != 1 or channel.mqtt_binding.retain is not False:
        channel_dict["bindings"] = {
            "mqtt": {
                "qos": channel.mqtt_binding.qos,
                "retain": channel.mqtt_binding.retain,
            }
        }


def _add_capability_requirements(
    channel: ChannelSchema, channel_dict: dict[str, Any]
) -> None:
    """Add capability requirements to channel dict.

    Args:
        channel: The ChannelSchema to read requirements from.
        channel_dict: The channel dict to add requirements to.
    """
    if channel.capability_requirements:
        reqs = []
        for req in channel.capability_requirements:
            req_dict = {"tag": req.tag}
            if req.description:
                req_dict["description"] = req.description
            reqs.append(req_dict)
        channel_dict["x-cosalette-requires"] = reqs


def _add_payload_schema(channel: ChannelSchema, channel_dict: dict[str, Any]) -> None:
    """Add message payload schema to channel dict if present.

    Args:
        channel: The ChannelSchema to read payload schema from.
        channel_dict: The channel dict to add schema to.
    """
    if channel.payload_schema:
        channel_dict["messages"] = {
            channel.message_name or "message": {"payload": channel.payload_schema}
        }


def _channel_to_dict(channel: ChannelSchema) -> dict[str, Any]:
    """Convert a single ChannelSchema to a dict for AsyncAPI output.

    Args:
        channel: The ChannelSchema to convert.

    Returns:
        Channel dict suitable for AsyncAPI document.
    """
    channel_dict: dict[str, Any] = {
        "address": channel.address,
    }

    _add_channel_extensions(channel, channel_dict)
    _add_mqtt_binding(channel, channel_dict)
    _add_capability_requirements(channel, channel_dict)
    _add_payload_schema(channel, channel_dict)

    return channel_dict


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
        channels[name] = _channel_to_dict(channel)

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


def _snapshot_to_asyncapi(
    app_name: str,
    app_version: str,
    snapshot: dict[str, Any],
    *,
    include_extensions: bool = False,
) -> dict[str, Any]:
    """Convert a registry snapshot to an AsyncAPI document dict.

    Args:
        app_name: App name from snapshot.
        app_version: App version from snapshot.
        snapshot: Dict returned by build_registry_snapshot().
        include_extensions: Whether to include x-cosalette extensions.

    Returns:
        AsyncAPI 3.0.0-compliant document dict.
    """
    result: dict[str, Any] = {
        "asyncapi": "3.0.0",
        "info": {
            "title": app_name,
            "version": app_version,
        },
    }

    if include_extensions:
        result["x-cosalette-enforcement"] = {
            "mode": "warn",
            "on_configure": True,
            "on_publish": False,
            "network_level": False,
        }

    channels: dict[str, Any] = {}
    operations: dict[str, Any] = {}

    # Process devices - map to state channels
    for device in snapshot.get("devices", []):
        device_name = device["name"]
        channel_name = f"{device_name}State"
        operation_name = f"publish{device_name.title()}State"

        channels[channel_name] = {
            "address": f"{app_name}/{device_name}/state",
            "messages": {"message": {"payload": {"type": "object"}}},
        }

        if include_extensions:
            channels[channel_name]["x-cosalette-archetype"] = "device"

        operations[operation_name] = {
            "action": "send",
            "channel": {"$ref": f"#/channels/{channel_name}"},
        }

    # Process telemetry - map to state channels
    for telemetry in snapshot.get("telemetry", []):
        device_name = telemetry["name"]
        channel_name = f"{device_name}State"
        operation_name = f"publish{device_name.title()}State"

        channels[channel_name] = {
            "address": f"{app_name}/{device_name}/state",
            "messages": {"message": {"payload": {"type": "object"}}},
        }

        if include_extensions:
            channels[channel_name]["x-cosalette-archetype"] = "telemetry"

        operations[operation_name] = {
            "action": "send",
            "channel": {"$ref": f"#/channels/{channel_name}"},
        }

    # Process commands - map to command channels
    for command in snapshot.get("commands", []):
        device_name = command["name"]
        channel_name = f"{device_name}Command"
        operation_name = f"receive{device_name.title()}Command"

        channels[channel_name] = {
            "address": f"{app_name}/{device_name}/set",
            "messages": {"message": {"payload": {"type": "object"}}},
        }

        if include_extensions:
            channels[channel_name]["x-cosalette-archetype"] = "command"

        operations[operation_name] = {
            "action": "receive",
            "channel": {"$ref": f"#/channels/{channel_name}"},
        }

    # Add to result if any channels exist
    if channels:
        result["channels"] = channels
        result["operations"] = operations

    return result


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


def _print_missing_devices(missing_devices: AbstractSet[str]) -> int:
    """Print missing devices and return count.

    Args:
        missing_devices: Device names expected but not registered.

    Returns:
        Number of missing devices printed.
    """
    count = 0
    for device_name in sorted(missing_devices):
        count += 1
        typer.echo(f"✗ {device_name} — MISSING")
        typer.echo(
            f"    Schema expects device '{device_name}' but no registration found"
        )
        typer.echo()
    return count


def _print_scope_violations(violations: list[Any]) -> int:
    """Print scope violations and return count.

    Args:
        violations: List of validation violations.

    Returns:
        Number of scope violations printed.
    """
    count = 0
    for violation in violations:
        if violation.category == "scope_violation":
            count += 1
            typer.echo(f"✗ {violation.channel_name or 'unknown'} — SCOPE VIOLATION")
            typer.echo(f"    {violation.message}")
            typer.echo()
    return count


def _print_device_status(
    registered_names: AbstractSet[str], schema_device_names: AbstractSet[str]
) -> tuple[int, int]:
    """Print device registration status and return counts.

    Args:
        registered_names: Collection of registered device names.
        schema_device_names: Collection of device names expected by schema.

    Returns:
        Tuple of (compliant_count, extra_count).
    """
    compliant_count = 0
    extra_count = 0

    for device_name in sorted(registered_names):
        if device_name in schema_device_names:
            compliant_count += 1
            typer.echo(f"✓ {device_name} — OK")
        else:
            # Extra device (registered but not in schema)
            extra_count += 1
            typer.echo(f"⚠ {device_name} — EXTRA")
            typer.echo("    Device registered but not found in schema")

    return compliant_count, extra_count


def _print_summary_and_exit(
    missing_count: int,
    scope_violation_count: int,
    compliant_count: int,
    extra_count: int,
) -> None:
    """Print summary and exit with appropriate code.

    Args:
        missing_count: Number of missing devices.
        scope_violation_count: Number of scope violations.
        compliant_count: Number of compliant devices.
        extra_count: Number of extra devices.

    Raises:
        typer.Exit: Always exits with appropriate code.
    """
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


def _print_check_results(
    registered_names: AbstractSet[str],
    registry: SchemaRegistry,
    violations: list[Any],
    schema_path: Path,
    app_name: str,
) -> None:
    """Print check results and exit with appropriate code.

    Args:
        registered_names: Collection of device names registered by the app.
        registry: The loaded schema registry.
        violations: List of validation violations.
        schema_path: Path to the schema file.
        app_name: Name of the app being checked.

    Raises:
        typer.Exit: Always exits with appropriate code.
    """
    # Build a set of missing device names for display
    missing_devices = registry.device_names - registered_names
    schema_device_names = registry.device_names

    # Print header with schema and app info
    typer.echo(f"Schema: {schema_path} (v{registry.app_version})")
    typer.echo(f"App:    {app_name}")
    typer.echo()

    # Print findings and collect counts
    missing_count = _print_missing_devices(missing_devices)
    scope_violation_count = _print_scope_violations(violations)
    compliant_count, extra_count = _print_device_status(
        registered_names, schema_device_names
    )

    # Print summary and exit
    _print_summary_and_exit(
        missing_count, scope_violation_count, compliant_count, extra_count
    )


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

    # Print results and exit
    _print_check_results(registered_names, registry, violations, schema_path, app._name)


@schema_app.command()
def dump(
    app_spec: Annotated[
        str, typer.Option("--app", help="App import spec (module:attr)")
    ],
) -> None:
    """Generate AsyncAPI YAML from app's registry.

    Imports the specified app, extracts its registrations via introspection,
    and converts them to a minimal AsyncAPI 3.0.0 document.
    """
    # Import the app
    app = _import_app(app_spec)

    # Build registry snapshot
    snapshot = build_registry_snapshot(app)

    # Convert to AsyncAPI dict
    asyncapi_dict = _snapshot_to_asyncapi(
        app_name=snapshot["app"]["name"],
        app_version=snapshot["app"]["version"],
        snapshot=snapshot,
        include_extensions=False,
    )

    # Output as YAML
    import yaml

    yaml_output = yaml.safe_dump(
        asyncapi_dict, default_flow_style=False, sort_keys=False
    )
    typer.echo(yaml_output.rstrip())

    raise typer.Exit(EXIT_OK)


@schema_app.command()
def init(
    app_spec: Annotated[
        str, typer.Option("--app", help="App import spec (module:attr)")
    ],
) -> None:
    """Generate starter schema with cosalette extensions.

    Like dump but scaffolded for editing — includes x-cosalette-enforcement
    section and archetype extensions on channels for user customization.
    """
    # Import the app
    app = _import_app(app_spec)

    # Build registry snapshot
    snapshot = build_registry_snapshot(app)

    # Convert to AsyncAPI dict with extensions
    asyncapi_dict = _snapshot_to_asyncapi(
        app_name=snapshot["app"]["name"],
        app_version=snapshot["app"]["version"],
        snapshot=snapshot,
        include_extensions=True,
    )

    # Output as YAML
    import yaml

    yaml_output = yaml.safe_dump(
        asyncapi_dict, default_flow_style=False, sort_keys=False
    )
    typer.echo(yaml_output.rstrip())

    raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------------------
# CLI entry point (for standalone usage)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    schema_app()
