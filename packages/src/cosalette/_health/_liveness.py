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
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cosalette._health._probe import (
    DEFAULT_FAIL_ON,
    DEFAULT_HEALTH_FILE_INTERVAL,
    HEALTH_FILE_ENV,
    HEALTH_FILE_VERSION,
    MAX_AGE_FACTOR,
    ProbeResult,
    check_health_file,
)
from cosalette._json import dumps, loads

if TYPE_CHECKING:
    from cosalette._health._reporter import HealthReporter

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_FAIL_ON",
    "DEFAULT_HEALTH_FILE_INTERVAL",
    "HEALTH_FILE_ENV",
    "HEALTH_FILE_VERSION",
    "MAX_AGE_FACTOR",
    "HealthFileWriter",
    "ProbeResult",
    "StaleTelemetryError",
    "check_health_file",
    "health_file_from_env",
]


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
        data["health_file_version"] = HEALTH_FILE_VERSION
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
