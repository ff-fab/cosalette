"""Typer-free run path for :meth:`App.cli` (ADR-005 amendment).

Building the Typer CLI imports Typer, Click, the schema subcommands and
the health probe, which then stay resident for the life of the process
(about 2 MiB on a Raspberry Pi Zero 2 W class host).  The plain run
path needs only five options, so :func:`parse_run_args` handles them
here and :meth:`App.cli` builds the Typer CLI for everything else.

Parity with the Typer path is by delegation: whenever argv holds
anything this parser does not fully accept (a subcommand, ``--help``,
``--version``, completion, an unknown or malformed option, an invalid
log level or format), it returns ``None`` and Typer handles the
invocation, so error messages and exit codes come from one place.
The run helpers below are shared by both paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args

from pydantic import ValidationError

from cosalette._constants import (
    EXIT_CONFIG_ERROR,
    EXIT_RUNTIME_ERROR,
    EXIT_STALE,
    EXIT_TASK_FAILURE,
)
from cosalette._health._liveness import StaleTelemetryError
from cosalette._health._loop_stall import LoopStallConfigError
from cosalette._settings import LoggingSettings
from cosalette._settings._config_file import SettingsLoadError
from cosalette._supervisor import TaskSupervisionError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cosalette._app import App
    from cosalette._settings import Settings

logger = logging.getLogger("cosalette._cli")

VALID_LOG_LEVELS = cast(
    "tuple[str, ...]", get_args(LoggingSettings.model_fields["level"].annotation)
)
VALID_LOG_FORMATS = cast(
    "tuple[str, ...]", get_args(LoggingSettings.model_fields["format"].annotation)
)

_VALUE_OPTIONS = {
    "--log-level": "log_level",
    "--log-format": "log_format",
    "--env-file": "env_file",
    "--config-file": "config_file",
}


@dataclass(frozen=True, slots=True)
class RunArgs:
    """The run options of :meth:`App.cli`, parsed without Typer."""

    dry_run: bool = False
    log_level: str | None = None
    log_format: str | None = None
    env_file: str | None = None
    config_file: str | None = None


def _completion_requested() -> bool:
    """Return whether Click's shell-completion variable is set."""
    return any(k.startswith("_") and k.endswith("_COMPLETE") for k in os.environ)


def parse_run_args(argv: Sequence[str]) -> RunArgs | None:
    """Parse *argv* if it holds only valid run options, else return ``None``.

    Accepts ``--dry-run`` and ``--log-level``/``--log-format``/
    ``--env-file``/``--config-file`` as ``--opt value`` or
    ``--opt=value``; a repeated option keeps its last value, as in Click.
    """
    if _completion_requested():
        return None
    values: dict[str, Any] = {}
    args = iter(argv)
    for arg in args:
        if arg == "--dry-run":
            values["dry_run"] = True
            continue
        name, sep, value = arg.partition("=")
        if name not in _VALUE_OPTIONS:
            return None
        if not sep:
            value = next(args, None)
            if value is None or value.startswith("-"):
                return None
        values[_VALUE_OPTIONS[name]] = value
    parsed = RunArgs(**values)
    if not log_options_valid(parsed.log_level, parsed.log_format):
        return None
    return parsed


def log_options_valid(log_level: str | None, log_format: str | None) -> bool:
    """Return whether ``--log-level`` and ``--log-format`` are allowed values."""
    return (log_level is None or log_level.upper() in VALID_LOG_LEVELS) and (
        log_format is None or log_format.lower() in VALID_LOG_FORMATS
    )


def run_with_args(app: App, args: RunArgs) -> None:
    """Run *app* with options parsed by :func:`parse_run_args`."""
    app._dry_run = args.dry_run
    settings = resolve_settings_or_exit(app, args.env_file, args.config_file)
    run_app(app, apply_cli_overrides(settings, args.log_level, args.log_format))


def apply_cli_overrides(
    settings: Settings,
    log_level: str | None,
    log_format: str | None,
) -> Settings:
    """Return *settings* with CLI overrides applied."""
    if log_level is not None:
        settings.logging = settings.logging.model_copy(
            update={"level": log_level.upper()},
        )

    if log_format is not None:
        settings.logging = settings.logging.model_copy(
            update={"format": log_format.lower()},
        )

    return settings


def _error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr, flush=True)


def run_app(app: App, settings: Settings) -> None:
    """Execute the application's async lifecycle.

    Handles :class:`KeyboardInterrupt` (suppressed),
    :class:`SystemExit` (re-raised), a supervised task failure (exits
    with :data:`EXIT_TASK_FAILURE`, ADR-081), ``exit_after_stale`` (exits
    with :data:`EXIT_STALE`, ADR-083), an invalid loop-stall timeout
    (exits with :data:`EXIT_CONFIG_ERROR`, ADR-088), and unexpected
    exceptions (exits with :data:`EXIT_RUNTIME_ERROR`).
    """
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(app._run_async(settings=settings))
    except SystemExit:
        raise
    except TaskSupervisionError as exc:
        # The supervisor already logged the failure at CRITICAL.
        logger.error("Exiting after a task failure: %s", exc)
        sys.exit(EXIT_TASK_FAILURE)
    except StaleTelemetryError as exc:
        logger.error("Exiting after stale telemetry: %s", exc)
        sys.exit(EXIT_STALE)
    except LoopStallConfigError as exc:
        _error(str(exc))
        sys.exit(EXIT_CONFIG_ERROR)
    except Exception as exc:
        logger.error("Runtime error: %s", exc)
        sys.exit(EXIT_RUNTIME_ERROR)


def resolve_settings_or_exit(
    app: App, env_file: str | None, config_file: str | None
) -> Settings:
    """Build settings from the resolved ``--env-file`` / ``--config-file``.

    An explicitly named path must exist (fail-loud); a missing or
    malformed file exits with :data:`EXIT_CONFIG_ERROR`.  When
    ``config_file`` is ``None`` it is omitted so a ``config_file=``
    declared in the app's ``model_config`` is still honoured.
    """
    if env_file is not None and not Path(env_file).is_file():
        _error(f"env file not found: {env_file}")
        raise SystemExit(EXIT_CONFIG_ERROR)
    if config_file is not None and not Path(config_file).is_file():
        _error(f"config file not found: {config_file}")
        raise SystemExit(EXIT_CONFIG_ERROR)

    settings_kwargs: dict[str, Any] = {"_env_file": env_file or ".env"}
    if config_file is not None:
        settings_kwargs["_config_file"] = config_file
    try:
        return app._settings_class(**settings_kwargs)
    except (ValidationError, SettingsLoadError) as exc:
        _error(str(exc))
        raise SystemExit(EXIT_CONFIG_ERROR) from exc
