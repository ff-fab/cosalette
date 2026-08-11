"""CLI subcommands for schema validation and tooling.

Provides ``cosalette schema validate|check|dump|init|slice``
subcommands for static validation, CI gating, and schema generation.

AsyncAPI conversion utilities live in :mod:`cosalette._schema._asyncapi`.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from cosalette._app import App
    from cosalette._schema import SchemaRegistry

import typer

from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema._cli_helpers import (
    _import_app,
    _load_schema_or_exit,
    _print_check_results,
    _reject_unexpanded_name_specs,
    _resolve_app_settings,
)

# ---------------------------------------------------------------------------
# Schema subcommand group
# ---------------------------------------------------------------------------

schema_app = typer.Typer(
    help="Schema validation and tooling. Commands that take --app import that "
    "module (running its top-level code) — do not point them at untrusted "
    "specs/repos; see SECURITY.md."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dump_yaml(data: object) -> str:
    """Serialize *data* to YAML for schema-doc output, sans trailing newline.

    Single source of truth for the schema CLI's emission contract, shared by
    every command that writes YAML (``slice``, ``dump``, ``init``,
    ``ha-discovery``): block style, source key order preserved
    (``sort_keys=False``), and non-ASCII emitted literally
    (``allow_unicode=True``) so unicode consumer metadata like ``°C`` / ``Bq/m³``
    stays readable in the generated (zensical) docs. Centralising this keeps the
    kwargs from drifting per call site — the drift that let the escaping bug hide.

    Exits with a friendly hint when the optional PyYAML dependency is missing.
    """
    try:
        import yaml
    except ImportError as exc:
        typer.echo(
            "Error: PyYAML is required for this command.\n\n"
            "Hint: Install schema dependencies with: pip install cosalette[schema]",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    return yaml.safe_dump(
        data, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).rstrip("\n")


def _warn_unreachable_consumer_annotations(registry: SchemaRegistry) -> None:
    """Warn on stderr about x-cosalette-consumer blocks the loader could not reach.

    Non-fatal: nested annotations beyond one level of array/object descent
    (Finding 16 scope) are dropped rather than raising, so without this the
    author gets no signal that a ``consumer()`` call anywhere but the top
    level or one level of ``[]``/``.`` nesting was silently ignored (F23).
    """
    if not registry.unreachable_consumer_channels:
        return
    channels = ", ".join(sorted(registry.unreachable_consumer_channels))
    typer.echo(
        "Warning: consumer() annotations found in unreachable positions "
        f"(deeper than one level of array/object nesting) in channel(s): "
        f"{channels}. These will not produce discovery entities.",
        err=True,
    )


def _import_validated_app(spec: str) -> App:
    """Import app and reject unexpanded callable name= registrations."""
    app = _import_app(spec)
    _reject_unexpanded_name_specs(app)
    return app


def _import_schema_app(
    spec: str,
    *,
    resolve_settings: bool,
    env_file: str | Path | None,
    config_file: Path | None = None,
) -> App:
    """Import app for schema commands, honouring --resolve-settings."""
    if resolve_settings:
        return _resolve_app_settings(_import_app(spec), env_file, config_file)
    return _import_validated_app(spec)


# Shared Annotated aliases for the --resolve-settings / --env-file flag pair.
_ResolveSettingsOpt = Annotated[
    bool,
    typer.Option(
        "--resolve-settings",
        help="Resolve settings and run the configure/expand lifecycle "
        "phases (ADR-051) so settings-derived (ADR-023 callable name=) "
        "entity names are expanded to their real, post-expansion names "
        "instead of tripping the unexpanded-name_spec guard. "
        "Note: configure hooks are executed; use only with trusted apps "
        "and settings files.",
    ),
]

_EnvFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--env-file",
        help="Path to a .env file used to resolve Settings. Must exist if"
        " given. Omit to silently look up '.env' in CWD."
        " Only used with --resolve-settings.",
    ),
]

_ConfigFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--config-file",
        help="Path to a TOML/YAML/JSON config file used to resolve Settings "
        "(env vars override it). Must exist if given. Only used with "
        "--resolve-settings.",
    ),
]


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
        # Load the YAML again to get the title - this double-read is intentional
        # as the SchemaRegistry doesn't preserve the original title for network schemas
        # Title extraction only — not emission; use _dump_yaml for any YAML output.
        try:
            import yaml
        except ImportError as exc:
            typer.echo(
                "Error: PyYAML is required for this command.\n\n"
                "Hint: Install schema dependencies with: pip install cosalette[schema]",
                err=True,
            )
            raise typer.Exit(EXIT_CONFIG_ERROR) from exc

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
    from cosalette._schema._asyncapi import _registry_to_asyncapi_dict

    output_dict = _registry_to_asyncapi_dict(filtered_registry)
    typer.echo(_dump_yaml(output_dict))

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
    resolve_settings: _ResolveSettingsOpt = False,
    env_file: _EnvFileOpt = None,
    config_file: _ConfigFileOpt = None,
) -> None:
    """Check app registrations against schema (CI gate).

    Imports the specified app, extracts its registrations, loads the schema,
    and validates that all schema-expected devices are registered.
    Returns exit code 0 for compliance, 1 for violations.
    """
    app = _import_schema_app(
        app_spec,
        resolve_settings=resolve_settings,
        env_file=env_file,
        config_file=config_file,
    )

    # Periodic registrations have no MQTT/AsyncAPI presence (ADR-041); exclude them.
    periodic_names = frozenset(r.name for r in app.periodic_registrations)
    registered_names = app.registered_names - periodic_names

    # Load schema
    registry = _load_schema_or_exit(schema_path)

    # For network-level schemas, filter to this app's slice
    if registry.enforcement.network_level:
        # Verify the app exists in the schema
        available_apps = registry.all_app_names()
        app_name = app.name
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
    from cosalette._schema._enforcement import _validate_registrations

    violations = _validate_registrations(registered_names, registry)

    # Print results and exit
    _print_check_results(registered_names, registry, violations, schema_path, app.name)


@schema_app.command()
def dump(
    app_spec: Annotated[
        str, typer.Option("--app", help="App import spec (module:attr)")
    ],
    resolve_settings: _ResolveSettingsOpt = False,
    env_file: _EnvFileOpt = None,
    config_file: _ConfigFileOpt = None,
) -> None:
    """Generate AsyncAPI YAML from app's registry.

    Imports the specified app, extracts its registrations via introspection,
    and converts them to a canonical AsyncAPI 3.0.0 document.

    Output includes all ``x-cosalette-*`` extensions (archetype, summary,
    behavior, effects, and contract-version).  Use ``init`` instead if you
    want the enforcement scaffold layered on top for editing.
    """
    app = _import_schema_app(
        app_spec,
        resolve_settings=resolve_settings,
        env_file=env_file,
        config_file=config_file,
    )

    # Build canonical AsyncAPI document
    asyncapi_dict = app.asyncapi()

    # Output as YAML
    typer.echo(_dump_yaml(asyncapi_dict))

    raise typer.Exit(EXIT_OK)


@schema_app.command()
def init(
    app_spec: Annotated[
        str, typer.Option("--app", help="App import spec (module:attr)")
    ],
    resolve_settings: _ResolveSettingsOpt = False,
    env_file: _EnvFileOpt = None,
    config_file: _ConfigFileOpt = None,
) -> None:
    """Generate starter schema with cosalette extensions.

    Like dump but scaffolded for editing — includes x-cosalette-enforcement
    section and archetype extensions on channels for user customization.
    """
    app = _import_schema_app(
        app_spec,
        resolve_settings=resolve_settings,
        env_file=env_file,
        config_file=config_file,
    )

    # Build canonical AsyncAPI document (already includes archetype extensions)
    asyncapi_dict = app.asyncapi()

    # Layer on the enforcement scaffold for editing convenience
    asyncapi_dict["x-cosalette-enforcement"] = {
        "mode": "warn",
        "on_configure": True,
        "on_publish": False,
        "network_level": False,
    }

    # Output as YAML
    typer.echo(_dump_yaml(asyncapi_dict))

    raise typer.Exit(EXIT_OK)


@schema_app.command()
def acl(
    schema_path: Annotated[Path, typer.Argument(help="Path to AsyncAPI schema file.")],
    format_name: Annotated[
        str, typer.Option("--format", "-f", help="Broker format.")
    ] = "mosquitto",
) -> None:
    """Generate broker ACL configuration from schema."""
    from cosalette._schema._acl import FORMATTERS, derive_acl_principals

    if format_name not in FORMATTERS:
        available = ", ".join(sorted(FORMATTERS))
        typer.echo(
            f"Unknown format: {format_name}. Available: {available}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    registry = _load_schema_or_exit(schema_path)
    principals = derive_acl_principals(registry)
    output = FORMATTERS[format_name](principals)
    typer.echo(output)


@schema_app.command(name="ha-discovery")
def ha_discovery(
    schema_path: Annotated[Path, typer.Argument(help="Path to AsyncAPI schema file.")],
    prefix: Annotated[
        str, typer.Option("--prefix", "-p", help="Discovery topic prefix.")
    ] = "homeassistant",
    format_name: Annotated[
        str, typer.Option("--format", "-f", help="Output format (json or yaml).")
    ] = "json",
) -> None:
    """Generate Home Assistant MQTT discovery payloads from schema."""
    from cosalette._schema._consumer_gen import (
        HaDiscoveryGenerator,
        ha_discovery_to_json,
        has_consumer_visible_channels,
    )

    if format_name not in ("json", "yaml"):
        typer.echo(f"Unknown format: {format_name}. Available: json, yaml", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    registry = _load_schema_or_exit(schema_path)
    _warn_unreachable_consumer_annotations(registry)
    generator = HaDiscoveryGenerator(registry=registry, discovery_prefix=prefix)
    payloads = generator.generate()

    if format_name == "json":
        typer.echo(ha_discovery_to_json(payloads))
    else:
        data = [{"topic": p.topic, "config": p.config} for p in payloads]
        typer.echo(_dump_yaml(data))

    if not payloads and has_consumer_visible_channels(registry):
        typer.echo(
            "Error: registry has consumer-visible channels but produced no "
            "discovery payloads — every channel is missing consumer()/"
            "ha_entities() annotations. Nothing will show up in Home Assistant.",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)


@schema_app.command()
def openhab(
    schema_path: Annotated[Path, typer.Argument(help="Path to AsyncAPI schema file.")],
    broker_uid: Annotated[
        str, typer.Option("--broker-uid", "-b", help="OpenHAB broker Thing UID.")
    ] = "broker",
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output: things, items, or both.")
    ] = "both",
) -> None:
    """Generate OpenHAB .things/.items configuration from schema."""
    from cosalette._schema._consumer_gen import (
        OpenHabGenerator,
        has_consumer_visible_channels,
    )

    if output not in ("things", "items", "both"):
        typer.echo(
            f"Unknown output: {output}. Available: things, items, both",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    registry = _load_schema_or_exit(schema_path)
    _warn_unreachable_consumer_annotations(registry)
    generator = OpenHabGenerator(registry=registry, broker_uid=broker_uid)
    consumer_channels = generator.consumer_channels()

    if output in ("things", "both"):
        typer.echo(generator.generate_things())
    if output == "both":
        typer.echo("// ---")
        typer.echo()
    if output in ("items", "both"):
        typer.echo(generator.generate_items())

    if not consumer_channels and has_consumer_visible_channels(registry):
        typer.echo(
            "Error: registry has consumer-visible channels but none carry "
            "consumer() annotations — nothing will show up in openHAB.",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)


@schema_app.command()
def monitor(
    schema_path: Annotated[
        Path, typer.Argument(help="Path to network-level AsyncAPI schema.")
    ],
    broker: Annotated[
        str, typer.Option("--broker", "-b", help="MQTT broker host:port.")
    ] = "localhost:1883",
    timeout: Annotated[
        float, typer.Option("--timeout", "-t", help="Collection period in seconds.")
    ] = 10.0,
) -> None:
    """Monitor fleet schema compliance via MQTT."""
    import asyncio

    from cosalette._schema._monitor import run_monitor

    registry = _load_schema_or_exit(schema_path)

    if not registry.enforcement.network_level:
        typer.echo("Warning: schema is not marked as network_level", err=True)

    typer.echo(f"Monitoring {broker} for {timeout}s...")
    typer.echo(f"Expected apps: {', '.join(sorted(registry.all_app_names()))}")
    typer.echo()

    report = asyncio.run(run_monitor(broker, registry, timeout=timeout))
    typer.echo(report.summary())

    if report.non_compliant or report.offline:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# CLI entry point (for standalone usage)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    schema_app()
