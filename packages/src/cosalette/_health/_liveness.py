"""Opt-in health file and its probe for container liveness (ADR-083).

The app writes its heartbeat payload to the path named by the
``COSALETTE_HEALTH_FILE`` environment variable; ``<app> health`` and
``cosalette health`` read it back and exit ``0`` (healthy) or ``1``
(unhealthy), which is what Docker ``HEALTHCHECK`` and Kubernetes exec
probes expect.  Nothing is written unless the variable is set.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cosalette._json import dumps, loads

if TYPE_CHECKING:
    from cosalette._health._reporter import HealthReporter

logger = logging.getLogger(__name__)

HEALTH_FILE_ENV = "COSALETTE_HEALTH_FILE"
"""Environment variable naming the health file; unset means off (ADR-083)."""

DEFAULT_HEALTH_FILE_INTERVAL = 60.0
"""Write interval in seconds when heartbeats are disabled."""

DEFAULT_FAIL_ON: tuple[str, ...] = ("stale",)
"""Device statuses that fail the probe unless ``--fail-on`` says otherwise."""

MAX_AGE_FACTOR = 3
"""Default ``--max-age`` as a multiple of the file's write interval."""


class StaleTelemetryError(RuntimeError):
    """A telemetry entity stayed stale for ``exit_after_stale`` (ADR-083).

    Raised by :meth:`App.run` after the graceful teardown.  :meth:`App.cli`
    maps it to exit code ``5``.

    Attributes:
        entity: Name of the stale telemetry entity.
        stale_for: Seconds it had been stale when the app gave up.
    """

    def __init__(self, entity: str, stale_for: float) -> None:
        self.entity = entity
        self.stale_for = stale_for
        super().__init__(f"Telemetry {entity!r} stale for {stale_for:.0f}s")


def health_file_from_env() -> Path | None:
    """Return the health file path from the environment, or ``None`` (off)."""
    raw = os.environ.get(HEALTH_FILE_ENV, "").strip()
    return Path(raw) if raw else None


@dataclass
class HealthFileWriter:
    """Write the heartbeat payload atomically to *path*.

    Each write goes to a temporary file in the same directory, then
    replaces *path* with :func:`os.replace`, so a reader never sees a
    partial file.  A failed write logs one WARNING and DEBUG afterwards;
    it never raises.
    """

    path: Path
    reporter: HealthReporter
    interval: float
    wall_clock: Callable[[], float] = time.time
    _warned: bool = field(default=False, init=False, repr=False)

    def render(self) -> str:
        """Return the file content: the heartbeat plus write metadata."""
        payload = self.reporter.heartbeat_payload()
        data = loads(payload.to_json(include_version=self.reporter.include_version))
        data["written_at"] = self.wall_clock()
        data["interval"] = self.interval
        return dumps(data)

    def write(self) -> bool:
        """Write the current snapshot; return whether it succeeded."""
        try:
            content = self.render()
            fd, tmp = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp, self.path)
            except BaseException:
                with contextlib.suppress(OSError):
                    Path(tmp).unlink()
                raise
        except OSError as exc:
            self._log_failure(exc)
            return False
        return True

    def remove(self) -> None:
        """Delete the file on a clean shutdown; a missing file is fine."""
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)

    def _log_failure(self, exc: OSError) -> None:
        if self._warned:
            logger.debug("Health file %s not written: %s", self.path, exc)
            return
        self._warned = True
        logger.warning(
            "Cannot write health file %s (%s); container probes will report "
            "unhealthy. Further failures are logged at DEBUG.",
            self.path,
            exc,
        )


@dataclass(frozen=True, slots=True)
class ProbeResult:
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

    Unhealthy when the file is missing or unreadable, older than *max_age*
    (default: ``MAX_AGE_FACTOR`` x the file's ``interval``), or when any
    device reports a status in *fail_on*.
    """
    try:
        data = loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ProbeResult(False, f"health file {path} does not exist")
    except (OSError, ValueError) as exc:
        return ProbeResult(False, f"health file {path} is unreadable: {exc}")
    if not isinstance(data, dict):
        return ProbeResult(False, f"health file {path} is not a JSON object")
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


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


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
