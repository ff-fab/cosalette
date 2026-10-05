"""CLI scaffolding for cosalette applications (Typer-based).

Provides :func:`build_cli` which constructs a Typer app that parses
framework-level options (``--dry-run``, ``--version``, ``--log-level``,
``--log-format``, ``--env-file``) and hands off to the application's
async lifecycle.  :meth:`App.cli` skips it for a plain run, see
:mod:`cosalette._cli_run`.

See Also:
    ADR-005 — CLI framework decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from cosalette import _cli_run
from cosalette._health._liveness_cli import HealthCommand, health_command
from cosalette._mcp._introspect import format_asyncapi_table
from cosalette._schema._cli import schema_app
from cosalette._utils import _typer_options

if TYPE_CHECKING:
    from cosalette._app import App


def _validate_log_options(log_level: str | None, log_format: str | None) -> None:
    """Validate ``--log-level`` and ``--log-format`` values.

    Raises:
        typer.BadParameter: If the value is not ``None`` and not among
            the allowed choices.
    """
    if log_level is not None and log_level.upper() not in _cli_run.VALID_LOG_LEVELS:
        raise typer.BadParameter(
            f"Invalid log level '{log_level}'. "
            f"Choose from: {', '.join(_cli_run.VALID_LOG_LEVELS)}",
            param_hint="'--log-level'",
        )

    if log_format is not None and log_format.lower() not in _cli_run.VALID_LOG_FORMATS:
        raise typer.BadParameter(
            f"Invalid log format '{log_format}'. "
            f"Choose from: {', '.join(_cli_run.VALID_LOG_FORMATS)}",
            param_hint="'--log-format'",
        )


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
        **_typer_options(),
    )

    # -- schema subcommands -------------------------------------------------
    cli.add_typer(schema_app, name="schema")

    # -- health probe (ADR-083) ---------------------------------------------
    cli.command("health", cls=HealthCommand)(health_command)

    # -- main command -------------------------------------------------------

    @cli.callback(invoke_without_command=True)
    def main(
        ctx: typer.Context,
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

        # -- a subcommand (health, schema ...) runs instead of the app -------
        if ctx.invoked_subcommand is not None:
            return

        # -- validate enum-like options, then run ----------------------------
        _validate_log_options(log_level, log_format)
        _cli_run.run_with_args(
            app,
            _cli_run.RunArgs(dry_run, log_level, log_format, env_file, config_file),
        )

    return cli
