"""CLI subcommands for schema validation and tooling.

Provides ``cosalette schema validate|check|dump|init|slice``
subcommands for static validation, CI gating, and schema generation.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer

from cosalette._schema import SchemaRegistry
from cosalette._schema_loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema,
)

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


# ---------------------------------------------------------------------------
# CLI entry point (for standalone usage)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    schema_app()
