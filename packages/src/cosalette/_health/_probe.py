"""Health file check and the stdlib-only ``cosalette-health`` fallback (ADR-083).

Platform wheels ship ``cosalette-health`` as a native binary (ADR-087).
Installs from the sdist get this module's :func:`main` as the console
script instead, so the command exists everywhere.  It imports only the
standard library: a probe runs every few seconds and must stay cheap.

The health file contract (``docs/reference/health-file.md``) is checked
here once and reused by the Typer ``health`` subcommand; the Rust probe
implements the same rules, kept in step by the shared golden fixtures.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NamedTuple, NoReturn, cast, override

HEALTH_FILE_ENV = "COSALETTE_HEALTH_FILE"
"""Environment variable naming the health file; unset means off (ADR-083)."""

HEALTH_FILE_VERSION = 1
"""Contract major version written to, and accepted from, ``health_file_version``."""

DEFAULT_HEALTH_FILE_INTERVAL = 60.0
"""Write interval in seconds when heartbeats are disabled."""

DEFAULT_FAIL_ON: tuple[str, ...] = ("stale",)
"""Device statuses that fail the probe unless ``--fail-on`` says otherwise."""

MAX_AGE_FACTOR = 3
"""Default ``--max-age`` as a multiple of the file's write interval."""

EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1
"""Docker ``HEALTHCHECK`` only knows 0 and 1, and reserves 2."""

NO_FILE_REASON = f"no health file (pass --file or set {HEALTH_FILE_ENV})"


class ProbeResult(NamedTuple):
    """Outcome of one health file check."""

    healthy: bool
    reason: str


def check_health_file(
    path: Path,
    *,
    now: float,
    max_age: float | None = None,
    fail_on: Collection[str] = DEFAULT_FAIL_ON,
) -> ProbeResult:
    """Check the health file at *path* against *now* (Unix time).

    Unhealthy when the file is missing or unreadable, declares an
    unsupported ``health_file_version``, is older than *max_age* (default:
    ``MAX_AGE_FACTOR`` x the file's ``interval``), or when any device
    reports a status in *fail_on*.
    """
    data = _load(path)
    if isinstance(data, ProbeResult):
        return data
    written_at = _number(data.get("written_at"))
    if written_at is None:
        return ProbeResult(False, f"health file {path} has no written_at time")
    limit = max_age if max_age is not None else _default_max_age(data)
    age = now - written_at
    if age > limit:
        return ProbeResult(
            False, f"health file is {age:.0f}s old (max age {limit:.0f}s)"
        )
    failing = _failing_devices(data.get("devices"), fail_on)
    if failing:
        listed = ", ".join(f"{name}={status}" for name, status in failing)
        return ProbeResult(False, f"failing devices: {listed}")
    return ProbeResult(True, f"healthy (health file {age:.0f}s old)")


def _load(path: Path) -> dict[str, object] | ProbeResult:
    """Read *path* as a supported health file, or say why it is not one."""
    try:
        data = _parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ProbeResult(False, f"health file {path} does not exist")
    except (OSError, ValueError, RecursionError) as exc:
        return ProbeResult(False, f"health file {path} is unreadable: {exc}")
    if not isinstance(data, dict):
        return ProbeResult(False, f"health file {path} is not a JSON object")
    version = data.get("health_file_version", HEALTH_FILE_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        return ProbeResult(False, f"health file {path} has an invalid version")
    if version != HEALTH_FILE_VERSION:
        return ProbeResult(
            False, f"health file {path} has unsupported version {version}"
        )
    return cast("dict[str, object]", data)


def _parse(text: str) -> object:
    # Strict JSON, like the writer and the Rust probe: no NaN/Infinity
    # tokens and no numbers that overflow to infinity.
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite)


def _reject_constant(token: str) -> NoReturn:
    msg = f"invalid JSON number {token}"
    raise ValueError(msg)


def _finite(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        _reject_constant(token)
    return value


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _default_max_age(data: dict[str, object]) -> float:
    interval = _number(data.get("interval"))
    if interval is None or interval <= 0:
        interval = DEFAULT_HEALTH_FILE_INTERVAL
    return MAX_AGE_FACTOR * interval


def _failing_devices(
    devices: object, fail_on: Collection[str]
) -> list[tuple[str, str]]:
    if not isinstance(devices, dict):
        return []
    failing: list[tuple[str, str]] = []
    for name, entry in sorted(devices.items()):
        status = entry.get("status") if isinstance(entry, dict) else None
        if isinstance(status, str) and status in fail_on:
            failing.append((str(name), status))
    return failing


def parse_max_age(raw: str) -> float:
    """Parse ``--max-age``: a finite number of seconds, at least 0."""
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        msg = f"{raw!r} is not a finite number >= 0"
        raise argparse.ArgumentTypeError(msg)
    return value


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(EXIT_UNHEALTHY, f"unhealthy: usage error: {message}\n")


def _build_parser() -> _Parser:
    parser = _Parser(
        prog="cosalette-health",
        description="Check a cosalette health file for a container liveness probe.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--file",
        help=f"Health file written by the app. Default: ${HEALTH_FILE_ENV}.",
    )
    parser.add_argument(
        "--max-age",
        type=parse_max_age,
        help=(
            "Seconds after which the file counts as too old. Default: "
            f"{MAX_AGE_FACTOR} x the write interval recorded in the file."
        ),
    )
    parser.add_argument(
        "--fail-on",
        action="append",
        help=(
            "Device status that makes the check fail; repeatable. "
            f"Default: {', '.join(DEFAULT_FAIL_ON)}."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe; return 0 (healthy) or 1 (unhealthy, reason on stderr)."""
    args = _build_parser().parse_args(argv)
    file = args.file or os.environ.get(HEALTH_FILE_ENV, "").strip()
    if not file:
        result = ProbeResult(False, NO_FILE_REASON)
    else:
        result = check_health_file(
            Path(file),
            now=time.time(),
            max_age=args.max_age,
            fail_on=tuple(args.fail_on) if args.fail_on else DEFAULT_FAIL_ON,
        )
    if not result.healthy:
        print(f"unhealthy: {result.reason}", file=sys.stderr)
        return EXIT_UNHEALTHY
    print(result.reason)
    return EXIT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
