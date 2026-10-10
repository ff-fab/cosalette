"""Shared CLI helpers for the schema subcommands."""

from __future__ import annotations

import itertools
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from pydantic import ValidationError

from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema import SchemaRegistry
from cosalette._schema._loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema_sync,
)
from cosalette._schema._resolve import (
    ResolvedApp,
    SchemaBuildError,
    build_settings,
    check_names_expanded,
    resolve_app,
)
from cosalette._settings._config_file import SettingsLoadError

if TYPE_CHECKING:
    from cosalette._app import App


def _load_schema_or_exit(path: Path) -> SchemaRegistry:
    """Load schema from file path or exit with error.

    Args:
        path: Path to the schema file.

    Returns:
        Parsed SchemaRegistry.

    Note:
        On SchemaLoadError or ImportError, prints the error and exits with
        EXIT_CONFIG_ERROR.  A missing optional dependency raises ImportError
        from ``require_optional``, whose message already carries the dependency
        hint, so no hint is appended here.
    """
    source = FileSchemaSource(path=path)
    try:
        return load_schema_sync(source)
    except (SchemaLoadError, ImportError) as exc:
        typer.echo(f"Error: {exc}", err=True)
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


def _exit_config_error(exc: Exception) -> NoReturn:
    """Print *exc* as a CLI configuration error and exit."""
    if isinstance(exc, ValidationError):
        field_errors = ", ".join(
            ".".join(str(part) for part in e["loc"]) for e in exc.errors()
        )
        message = (
            f"Configuration validation failed "
            f"({exc.error_count()} error(s)): {field_errors}"
        )
    else:
        message = str(exc)
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(EXIT_CONFIG_ERROR) from exc


def _reject_unexpanded_name_specs(app: App) -> None:
    """Abort if a registration has an unresolved callable name= or topic=.

    Settings-derived names and inbound topics need settings resolution before
    they can appear in a schema artifact. Static schema commands without that
    step would otherwise emit incomplete or incorrect channels.

    Raises:
        typer.Exit: With EXIT_CONFIG_ERROR when a name_spec or topic_spec is set.
    """
    try:
        check_names_expanded(
            itertools.chain(
                app.devices,
                app.telemetry_registrations,
                app.commands,
                app.inbound_registrations,
            )
        )
    except SchemaBuildError as exc:
        _exit_config_error(exc)


def _resolve_app_settings(
    app: App, env_file: str | Path | None, config_file: Path | None = None
) -> tuple[ResolvedApp, str]:
    """Run the ADR-051 settings-resolving pipeline for a schema command.

    The pipeline itself is :func:`cosalette._schema._resolve.resolve_app`,
    shared with :func:`cosalette.schema.resolved_asyncapi`; it leaves *app*
    unchanged and returns a resolved snapshot.  This wrapper only turns its
    configuration errors into CLI output.  Exceptions from adapter factories
    and configure hooks are app bugs and propagate with their traceback.

    Returns:
        A ``(resolved, topic_prefix)`` pair, see ``resolve_app``.

    Raises:
        typer.Exit: With EXIT_CONFIG_ERROR when the env/config file is
            missing or broken, the Settings are invalid, or resolution fails
            (duplicate names after expansion, persist= without a store, an
            unexpanded name spec).
    """
    try:
        settings = build_settings(app, env_file, config_file)
    except (SettingsLoadError, ValidationError) as exc:
        _exit_config_error(exc)
    try:
        return resolve_app(app, env_file, config_file, _settings=settings)
    except SchemaBuildError as exc:
        _exit_config_error(exc)


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
