"""Shared CLI helpers for the schema subcommands."""

from __future__ import annotations

import itertools
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema import SchemaRegistry
from cosalette._schema._loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema_sync,
)

if TYPE_CHECKING:
    from cosalette._app import App


def _load_schema_or_exit(path: Path) -> SchemaRegistry:
    """Load schema from file path or exit with error.

    Args:
        path: Path to the schema file.

    Returns:
        Parsed SchemaRegistry.

    Note:
        On SchemaLoadError or ImportError, prints errors and exits with
        EXIT_CONFIG_ERROR.
    """
    source = FileSchemaSource(path=path)
    try:
        return load_schema_sync(source)
    except (SchemaLoadError, ImportError) as exc:
        typer.echo(
            f"Error: {exc}\n\nHint: Install schema dependencies with: "
            "pip install cosalette[schema]",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc


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
    import importlib as _importlib

    # Import App here to avoid circular imports at module level
    from cosalette._app import App

    spec = spec.strip()
    if ":" not in spec:
        typer.echo(
            f"Error: Invalid app spec '{spec}'. "
            "Expected format: 'module.path:attribute'",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)

    module_path, attr_name = spec.rsplit(":", 1)
    module_path = module_path.strip()
    attr_name = attr_name.strip()

    try:
        module = _importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        typer.echo(
            f"Error: Could not import module '{module_path}': {exc}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    except Exception as exc:
        typer.echo(
            f"Error: Failed to import module '{module_path}': {exc}",
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


def _reject_unexpanded_name_specs(app: App) -> None:
    """Abort if any registration still carries an unexpanded callable name=.

    Settings-derived NameSpec callables (ADR-023) are only expanded inside
    app.run().  Static schema commands are settings-free and cannot expand
    them, so they would silently emit a phantom channel named after the
    handler qualname.  This guard fails loudly instead.

    Args:
        app: The imported App instance to inspect.

    Raises:
        typer.Exit: With EXIT_CONFIG_ERROR when any name_spec is set.
    """
    offending = [
        reg.name
        for reg in itertools.chain(
            app.devices, app.telemetry_registrations, app.commands
        )
        if reg.name_spec is not None
    ]
    if not offending:
        return

    names_list = "\n".join(f"  - {n}" for n in offending)
    typer.echo(
        "Error: one or more registrations use a settings-derived entity set "
        "(ADR-023 callable name= NameSpec) that cannot be represented in a "
        "static schema artifact.  These handlers must be bootstrapped via "
        "app.run() before their entity names are known — the static schema "
        "pipeline does not do that.\n\n"
        f"Offending handlers:\n{names_list}",
        err=True,
    )
    raise typer.Exit(EXIT_CONFIG_ERROR)


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
    violation_count = missing_count + scope_violation_count

    typer.echo()
    if violation_count > 0:
        if extra_count > 0:
            typer.echo(
                f"Result: {violation_count} violations, {extra_count} extra, "
                f"{compliant_count} compliant"
            )
        else:
            typer.echo(
                f"Result: {violation_count} violations, {compliant_count} compliant"
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
