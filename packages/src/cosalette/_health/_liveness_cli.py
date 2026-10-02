"""The ``health`` CLI subcommand shared by ``<app>`` and ``cosalette`` (ADR-083)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer

from cosalette._health._liveness import (
    DEFAULT_FAIL_ON,
    HEALTH_FILE_ENV,
    MAX_AGE_FACTOR,
    check_health_file,
)

EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1
"""Docker ``HEALTHCHECK`` only knows 0 and 1, and reserves 2."""


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
        typer.echo(
            f"unhealthy: no health file (pass --file or set {HEALTH_FILE_ENV})",
            err=True,
        )
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
