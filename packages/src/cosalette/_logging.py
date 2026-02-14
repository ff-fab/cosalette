"""Structured JSON log formatter and logging configuration.

Container orchestrators (Docker, Kubernetes, systemd-journal) parse
structured log output far more effectively than free-text lines.
This module provides a :class:`JsonFormatter` that emits one JSON
object per log record on a single line (JSON Lines / NDJSON format).

Each log line includes **correlation metadata** — ``service`` name
and application ``version`` — so log aggregators (Loki,
Elasticsearch, CloudWatch) can filter and group entries without
extra configuration.

**Why JSON over key-value?**

- JSON is universally parseable — every log aggregator supports it
  natively, whereas key-value formats vary (logfmt, syslog
  structured data, etc.) and require parser configuration.
- Python's ``json.dumps`` is in the stdlib — no extra dependency.

**Why a custom formatter instead of ``python-json-logger``?**

- Zero additional dependencies — this project targets minimal
  container images where every extra package costs build time and
  attack surface.
- Full control over the output schema — the field names match the
  project's conventions, not a third-party library's defaults.

See Also:
    - `The Twelve-Factor App — XI. Logs
      <https://12factor.net/logs>`_:
      "A twelve-factor app never concerns itself with routing or
      storage of its output stream."
    - ADR-004 for logging strategy decisions.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from cosalette._settings import LoggingSettings

_TEN_MB = 10 * 1024 * 1024

_TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects (NDJSON).

    Each record produces a JSON object with these fields:

    - ``timestamp`` — ISO 8601 with timezone (always UTC)
    - ``level`` — Python log level name
    - ``logger`` — dotted logger name
    - ``message`` — the formatted log message
    - ``service`` — application name for log correlation
    - ``version`` — application version (omitted when empty)
    - ``exception`` — formatted traceback (only present when
      an exception is logged)
    - ``stack_info`` — stack trace (only present when
      ``stack_info=True``)

    **Design decision — UTC timestamps:** Container logs cross
    timezone boundaries (host TZ != aggregator TZ).  UTC removes
    ambiguity and lets the *display* layer apply local time when
    needed.  This follows RFC 3339 / ISO 8601 and matches the
    convention in structured logging libraries across ecosystems
    (Go ``zap``, Rust ``tracing``, Node ``pino``).

    Args:
        service: Application name included in every log line.
        version: Application version string.  Omitted from
            output when empty.
    """

    def __init__(
        self,
        *,
        service: str = "",
        version: str = "",
    ) -> None:
        super().__init__()
        self._service = service
        self._version = version

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a single-line JSON string.

        Overrides :meth:`logging.Formatter.format`.  The returned
        string contains no embedded newlines (tracebacks are
        escaped by ``json.dumps``), so each call produces exactly
        one log line — critical for container log drivers that
        split on ``\\n``.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self._service,
        }

        if self._version:
            entry["version"] = self._version

        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(entry, default=str)


def configure_logging(
    settings: LoggingSettings,
    *,
    service: str,
    version: str = "",
) -> None:
    """Configure the root logger from settings.

    Clears any existing handlers on the root logger, then
    installs fresh handlers according to *settings*.

    A :class:`logging.StreamHandler` writing to ``stderr`` is
    always installed.  When ``settings.file`` is set, a
    :class:`~logging.handlers.RotatingFileHandler` is added as
    well (10 MB max size, ``settings.backup_count`` generations).

    Args:
        settings: Logging configuration (level, format, file).
        service: Application name passed to :class:`JsonFormatter`.
        version: Application version passed to
            :class:`JsonFormatter`.  Defaults to ``""``.
    """
    root = logging.getLogger()

    # Clear existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Build formatter
    if settings.format == "json":
        formatter: logging.Formatter = JsonFormatter(service=service, version=version)
    else:
        formatter = logging.Formatter(_TEXT_FORMAT)

    # Stream handler (always present → stderr)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Optional rotating file handler
    if settings.file is not None:
        file_handler = RotatingFileHandler(
            settings.file,
            maxBytes=_TEN_MB,
            backupCount=settings.backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(settings.level)
