"""The ``health`` CLI subcommand shared by ``<app>`` and ``cosalette`` (ADR-083)."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Annotated, Any, override

import typer
from typer.core import TyperCommand

from cosalette._health._probe import (
    DEFAULT_FAIL_ON,
    EXIT_HEALTHY,
    EXIT_UNHEALTHY,
    HEALTH_FILE_ENV,
    MAX_AGE_FACTOR,
    NO_FILE_REASON,
    check_health_file,
)

__all__ = ["EXIT_HEALTHY", "EXIT_UNHEALTHY", "HealthCommand", "health_command"]


class HealthCommand(TyperCommand):
    """Report usage errors as unhealthy (1) instead of Click's usage exit (2).

    Only covers the subcommand's own arguments: errors the parent group
    parses before it reaches ``health`` still exit 2.
    """

    @override
    def make_context(self, *args: Any, **kwargs: Any) -> Any:
        # Any: the base returns typer's vendored, non-public click Context.
        try:
            return super().make_context(*args, **kwargs)
        except typer.Exit:
            # Help exits successfully while the command context is being built.
            raise
        except typer.TyperException as exc:
            typer.echo(f"unhealthy: usage error: {exc.format_message()}", err=True)
            raise typer.Exit(EXIT_UNHEALTHY) from exc


def _finite_max_age(value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
        msg = "expected a finite number"
        raise typer.BadParameter(msg)
    return value


def health_command(
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            envvar=HEALTH_FILE_ENV,
            show_envvar=True,
            help="Health file written by the app.",
        ),
    ] = None,
    max_age: Annotated[
        float | None,
        typer.Option(
            "--max-age",
            min=0.0,
            callback=_finite_max_age,
            help=(
                "Seconds after which the file counts as too old. Default: "
                f"{MAX_AGE_FACTOR} x the write interval recorded in the file."
            ),
        ),
    ] = None,
    fail_on: Annotated[
        list[str] | None,
        typer.Option(
            "--fail-on",
            help=(
                "Device status that makes the check fail; repeatable. "
                f"Default: {', '.join(DEFAULT_FAIL_ON)}."
            ),
        ),
    ] = None,
) -> None:
    """Check the app's health file for a container liveness probe.

    Exits 0 when the file is fresh and no device has a failing status,
    and 1 otherwise, with the reason on stderr.
    """
    if file is None:
        typer.echo(f"unhealthy: {NO_FILE_REASON}", err=True)
        raise typer.Exit(EXIT_UNHEALTHY)
    result = check_health_file(
        file,
        now=time.time(),
        max_age=max_age,
        fail_on=tuple(fail_on) if fail_on else DEFAULT_FAIL_ON,
    )
    if not result.healthy:
        typer.echo(f"unhealthy: {result.reason}", err=True)
        raise typer.Exit(EXIT_UNHEALTHY)
    typer.echo(result.reason)
