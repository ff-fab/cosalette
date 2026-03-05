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
from typing import TYPE_CHECKING, Annotated, get_args

import typer
from pydantic import ValidationError

from cosalette._settings import LoggingSettings

if TYPE_CHECKING:
    from cosalette._app import App
    from cosalette._settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 3

# ---------------------------------------------------------------------------
# Allowed values (extracted from LoggingSettings Literal types)
# ---------------------------------------------------------------------------

_VALID_LOG_LEVELS: tuple[str, ...] = get_args(
    LoggingSettings.model_fields["level"].annotation,
)
_VALID_LOG_FORMATS: tuple[str, ...] = get_args(
    LoggingSettings.model_fields["format"].annotation,
)


# ---------------------------------------------------------------------------
# Helpers (extracted to reduce cognitive complexity of build_cli)
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
    name = app._name
    version = app._version
    description = app._description

    cli = typer.Typer(
        help=f"{name} v{version} — {description} (powered by cosalette)",
    )

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
            str,
            typer.Option("--env-file", help="Path to .env file."),
        ] = ".env",
    ) -> None:
        # -- version ---------------------------------------------------------
        if version_flag:
            typer.echo(f"{name} v{version}")
            raise typer.Exit()

        # -- validate enum-like options -------------------------------------
        _validate_log_options(log_level, log_format)

        # -- propagate dry-run flag -----------------------------------------
        app._dry_run = dry_run

        # -- build settings -------------------------------------------------
        try:
            settings: Settings = app._settings_class(_env_file=env_file)  # type: ignore[call-arg]
        except ValidationError as exc:
            logger.error("Configuration error: %s", exc)
            raise SystemExit(EXIT_CONFIG_ERROR) from exc

        # -- apply CLI overrides & run --------------------------------------
        settings = _apply_cli_overrides(settings, log_level, log_format)
        _run_app(app, settings)

    return cli
