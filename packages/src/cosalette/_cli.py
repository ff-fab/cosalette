"""CLI scaffolding for cosalette applications (Typer-based).

Provides :func:`build_cli` which constructs a Typer app that parses
framework-level options (``--dry-run``, ``--version``, ``--log-level``,
``--log-format``, ``--env-file``) and hands off to the application's
async lifecycle.

See Also:
    ADR-005 — CLI framework decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, get_args

import typer
from pydantic import ValidationError

from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_RUNTIME_ERROR
from cosalette._mcp._introspect import format_asyncapi_table
from cosalette._schema._cli import schema_app
from cosalette._settings import LoggingSettings
from cosalette._settings._config_file import SettingsLoadError

if TYPE_CHECKING:
    from cosalette._app import App
    from cosalette._settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed values from LoggingSettings Literal types
# ---------------------------------------------------------------------------

_VALID_LOG_LEVELS: tuple[str, ...] = get_args(
    LoggingSettings.model_fields["level"].annotation,
)
_VALID_LOG_FORMATS: tuple[str, ...] = get_args(
    LoggingSettings.model_fields["format"].annotation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_log_options(log_level: str | None, log_format: str | None) -> None:
    """Validate ``--log-level`` and ``--log-format`` values.

    Raises:
        typer.BadParameter: If the value is not ``None`` and not among
            the allowed choices.
    """
    if log_level is not None and log_level.upper() not in _VALID_LOG_LEVELS:
        raise typer.BadParameter(
            f"Invalid log level '{log_level}'. "
            f"Choose from: {', '.join(_VALID_LOG_LEVELS)}",
            param_hint="'--log-level'",
        )

    if log_format is not None and log_format.lower() not in _VALID_LOG_FORMATS:
        raise typer.BadParameter(
            f"Invalid log format '{log_format}'. "
            f"Choose from: {', '.join(_VALID_LOG_FORMATS)}",
            param_hint="'--log-format'",
        )


def _apply_cli_overrides(
    settings: Settings,
    log_level: str | None,
    log_format: str | None,
) -> Settings:
    """Return a copy of *settings* with CLI overrides applied."""
    if log_level is not None:
        settings.logging = settings.logging.model_copy(
            update={"level": log_level.upper()},
        )

    if log_format is not None:
        settings.logging = settings.logging.model_copy(
            update={"format": log_format.lower()},
        )

    return settings


def _run_app(app: App, settings: Settings) -> None:
    """Execute the application's async lifecycle.

    Handles :class:`KeyboardInterrupt` (suppressed),
    :class:`SystemExit` (re-raised), and unexpected exceptions
    (exits with :data:`EXIT_RUNTIME_ERROR`).
    """
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(app._run_async(settings=settings))
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Runtime error: %s", exc)
        sys.exit(EXIT_RUNTIME_ERROR)


def _resolve_settings_or_exit(
    app: App, env_file: str | None, config_file: str | None
) -> Settings:
    """Build settings from the resolved ``--env-file`` / ``--config-file``.

    An explicitly named path must exist (fail-loud); a missing or
    malformed file exits with :data:`EXIT_CONFIG_ERROR`.  When
    ``config_file`` is ``None`` it is omitted so a ``config_file=``
    declared in the app's ``model_config`` is still honoured.
    """
    if env_file is not None and not Path(env_file).is_file():
        typer.echo(f"Error: env file not found: {env_file}", err=True)
        raise SystemExit(EXIT_CONFIG_ERROR)
    if config_file is not None and not Path(config_file).is_file():
        typer.echo(f"Error: config file not found: {config_file}", err=True)
        raise SystemExit(EXIT_CONFIG_ERROR)

    settings_kwargs: dict[str, Any] = {"_env_file": env_file or ".env"}
    if config_file is not None:
        settings_kwargs["_config_file"] = config_file
    try:
        return app._settings_class(**settings_kwargs)
    except (ValidationError, SettingsLoadError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(EXIT_CONFIG_ERROR) from exc


def build_cli(app: App) -> typer.Typer:
    """Construct a Typer CLI from an :class:`App` instance.

    The returned Typer app exposes a single default command with
    framework-level options.  When invoked it bootstraps settings,
    applies CLI overrides, and delegates to
    :meth:`App._run_async`.

    Args:
        app: The cosalette application to wrap.

    Returns:
        A configured :class:`typer.Typer` ready to invoke.

    See Also:
        ADR-005 — CLI framework decision.
    """
    name = app.name
    version = app.version
    description = app.description

    cli = typer.Typer(
        help=f"{name} v{version} — {description} (powered by cosalette)",
    )

    # -- schema subcommands -------------------------------------------------
    cli.add_typer(schema_app, name="schema")

    # -- main command -------------------------------------------------------

    @cli.callback(invoke_without_command=True)
    def main(
        version_flag: Annotated[
            bool | None,
            typer.Option(
                "--version",
                is_eager=True,
                help="Show version and exit.",
            ),
        ] = None,
        show_devices: Annotated[
            bool | None,
            typer.Option(
                "--show-devices",
                is_eager=True,
                help="Show registered devices and exit.",
            ),
        ] = None,
        show_devices_json: Annotated[
            bool | None,
            typer.Option(
                "--show-devices-json",
                is_eager=True,
                help="Show registered devices as JSON and exit.",
            ),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Enable dry-run mode."),
        ] = False,
        log_level: Annotated[
            str | None,
            typer.Option("--log-level", help="Override log level."),
        ] = None,
        log_format: Annotated[
            str | None,
            typer.Option("--log-format", help="Override log format."),
        ] = None,
        env_file: Annotated[
            str | None,
            typer.Option(
                "--env-file",
                help=(
                    "Path to a .env file. Must exist if given; "
                    "defaults to '.env' in the CWD when omitted."
                ),
            ),
        ] = None,
        config_file: Annotated[
            str | None,
            typer.Option(
                "--config-file",
                help=(
                    "Path to a TOML/YAML/JSON config file supplying structured "
                    "settings (env vars override it). Must exist if given."
                ),
            ),
        ] = None,
    ) -> None:
        # -- version ---------------------------------------------------------
        if version_flag:
            typer.echo(f"{name} v{version}")
            raise typer.Exit()

        # -- show-devices (JSON) --------------------------------------------
        if show_devices_json:
            import json

            typer.echo(json.dumps(app.asyncapi(), indent=2))
            raise typer.Exit()

        # -- show-devices (table) -------------------------------------------
        if show_devices:
            typer.echo(format_asyncapi_table(app.asyncapi()))
            raise typer.Exit()

        # -- validate enum-like options -------------------------------------
        _validate_log_options(log_level, log_format)

        # -- propagate dry-run flag -----------------------------------------
        app._dry_run = dry_run

        # -- build settings -------------------------------------------------
        settings = _resolve_settings_or_exit(app, env_file, config_file)

        # -- apply CLI overrides & run --------------------------------------
        settings = _apply_cli_overrides(settings, log_level, log_format)
        _run_app(app, settings)

    return cli
