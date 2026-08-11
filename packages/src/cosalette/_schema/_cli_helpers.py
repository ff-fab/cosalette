"""Shared CLI helpers for the schema subcommands."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import ValidationError

from cosalette._clock import SystemClock
from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema import SchemaRegistry
from cosalette._schema._loader import (
    FileSchemaSource,
    SchemaLoadError,
    load_schema_sync,
)
from cosalette._settings._config_file import SettingsLoadError
from cosalette._wiring import _adapter_lifecycle
from cosalette._wiring._bootstrap import run_configure_hooks
from cosalette._wiring._resolution import resolve_enabled
from cosalette._wiring._resolution_checks import (
    _check_expanded_duplicates,
    expand_name_specs,
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
    all_regs = [
        *itertools.chain(app.devices, app.telemetry_registrations, app.commands)
    ]
    if not any(reg.name_spec is not None for reg in all_regs):
        return

    names_list = "\n".join(
        f"  - {repr(reg.name)}" for reg in all_regs if reg.name_spec is not None
    )
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


def _assert_file_arg(path: str | Path, label: str) -> None:
    """Exit with CONFIG_ERROR when an explicitly supplied file does not exist."""
    if not Path(path).is_file():
        typer.echo(f"Error: {label} not found: {path}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)


def _resolve_app_settings(
    app: App, env_file: str | Path | None, config_file: Path | None = None
) -> App:
    """Run the ADR-051 settings-resolving pipeline on an imported App.

    Mirrors the settings -> adapters -> configure-hooks -> expand ->
    resolve_enabled sequence in ``_app/_lifecycle.py::_run_async`` so that
    settings-derived entity names (ADR-023 callable ``name=`` NameSpecs)
    are expanded before the static AsyncAPI document is built — closing
    the import-time vs bootstrap-time name-set split described in ADR-051.

    Deliberately diverges from the runtime bootstrap in two ways:

    - Adapters are resolved with ``dry_run=True`` unconditionally, ignoring
      ``app._dry_run``.  Schema generation is static analysis and must never
      construct real hardware/network adapters; a reader expecting this to
      honor the app's configured dry-run default would be surprised, hence
      this note.
    - The Store is never resolved (``store=None`` is passed to
      ``resolve_enabled``) and adapter lifecycle context managers
      (``__aenter__``/``__aexit__``) are never entered — schema generation
      reads registrations, it does not run the application.

    Only settings construction and post-expansion resolution
    (``resolve_enabled`` / ``_check_expanded_duplicates``) get friendly
    ``typer.Exit`` treatment below — those are the expected, user-facing
    config errors (bad ``.env``, duplicate settings-derived names, missing
    store for ``persist=``).  ``resolve_adapters(...)`` and
    ``run_configure_hooks(...)`` are deliberately left unwrapped: a factory
    or configure hook raising is an app bug, not a config error, and
    ``app.run()`` itself does not catch these either (see
    ``_app/_lifecycle.py::_run_async``) — wrapping them here would hide a
    real traceback behind a misleadingly generic "config error" message.

    Args:
        app: The imported App instance to resolve in place.
        env_file: Path to a ``.env`` file used to construct Settings.
            ``None`` means use pydantic-settings' default behaviour (silent
            `.env` look-up).  An explicit path that does not exist is
            rejected fail-loud, matching the main CLI contract.
        config_file: Optional path to a TOML/YAML/JSON config file.

    Returns:
        The same *app* instance, with its registration lists mutated in
        place (name specs expanded, disabled registrations pruned).

    Raises:
        typer.Exit: With EXIT_CONFIG_ERROR when Settings construction fails
            validation, or when settings resolution raises (e.g. duplicate
            names after expansion, or persist= without a store).
    """
    if env_file is not None:
        _assert_file_arg(env_file, "env file")

    # _ConfigFileSource already raises SettingsLoadError.not_found when the
    # file is absent; the except SettingsLoadError handler below covers it.
    settings_kwargs: dict[str, Any] = {"_env_file": env_file or ".env"}
    if config_file is not None:
        settings_kwargs["_config_file"] = config_file
    try:
        settings = app._settings_class(**settings_kwargs)
    except ValidationError as exc:
        field_errors = ", ".join(
            ".".join(str(part) for part in e["loc"]) for e in exc.errors()
        )
        typer.echo(
            f"Error: Configuration validation failed "
            f"({exc.error_count()} error(s)): {field_errors}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    except SettingsLoadError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    # dry_run=True requests the dry-run variant; falls back to real impl when
    # none is registered — adapter factories may still run.
    resolved_adapters = _adapter_lifecycle.resolve_adapters(
        app._adapters, True, settings
    )

    asyncio.run(  # sync-only: must not be called from an async context
        run_configure_hooks(
            app._configure_hooks,
            settings,
            resolved_adapters,
            SystemClock(),
        )
    )

    expand_name_specs(app._telemetry, app._devices, app._commands, settings)

    try:
        # store=None: schema generation never touches Store (no persistence
        # I/O during static analysis).  A surviving telemetry registration
        # that declares persist= is still rejected below, same as runtime.
        resolve_enabled(
            app._telemetry,
            app._devices,
            app._commands,
            settings,
            None,
            periodic_list=app._periodic,
            stream_list=app._streams,
        )
        _check_expanded_duplicates(app._devices, app._telemetry, app._commands)
    except ValueError as exc:
        typer.echo(
            f"Error: settings resolution failed after expanding "
            f"settings-derived (ADR-023) name=/enabled= specs: {exc!r}",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc

    # Safety net: expand_name_specs should leave nothing unexpanded — a
    # name_spec kind it doesn't support would be a real framework bug, so
    # surface it loudly via the existing guard rather than silently
    # emitting a phantom channel.
    _reject_unexpanded_name_specs(app)
    if hasattr(app, "_asyncapi_cache"):
        object.__delattr__(app, "_asyncapi_cache")  # stale after in-place mutation
    return app


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
