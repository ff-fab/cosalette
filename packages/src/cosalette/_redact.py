"""Optional redaction of disclosed error messages and log output.

An app passes ``App(redact=...)`` to scrub secrets from the text that
leaves the process: the message of a *disclosed* error payload (ADR-061)
and every record written by the log handlers cosalette installs.  The
hook is a safety net on top of the default-deny disclosure policy, not a
replacement for it.

``redact`` is ``None`` (off), a callable ``str -> str``, or an iterable of
regular expressions (``str`` or compiled :class:`re.Pattern`) whose matches
are replaced with :data:`REDACTED`.  A redactor that raises fails open: the
text passes through unchanged and one WARNING is logged for the whole
process.

See Also:
    ADR-085 — Optional redaction hook for logs and disclosed error messages.
"""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable, Iterable
from typing import cast, override

REDACTED = "[REDACTED]"

type RedactSpec = Callable[[str], str] | Iterable[str | re.Pattern[str]] | None
"""What ``App(redact=...)`` accepts: off, a callable, or regular expressions."""

logger = logging.getLogger(__name__)

# One WARNING per process for a failing redactor (ADR-085), not per record.
_failure_warned = False


class Redactor:
    """A validated, fail-open ``str -> str`` redaction function.

    Built by :func:`build_redactor`; calling it never raises.
    """

    __slots__ = ("_func",)

    def __init__(self, func: Callable[[str], str]) -> None:
        self._func = func

    def __call__(self, text: str) -> str:
        """Return *text* redacted, or unchanged when the redactor raises."""
        global _failure_warned  # noqa: PLW0603 — one warning per process
        try:
            result = self._func(text)
            if not isinstance(result, str):
                # As broken as raising: publishing None would hide the text.
                msg = f"redactor must return a str, got {type(result).__name__}"
                raise TypeError(msg)  # noqa: TRY301 — handled below, fail open
        except Exception as exc:  # noqa: BLE001 — fail open (ADR-085)
            if not _failure_warned:
                _failure_warned = True
                # Only the type: the exception text may echo the secret.
                logger.warning(
                    "Redactor raised %s; text passes through unredacted "
                    "(this warning is logged once per process)",
                    type(exc).__name__,
                )
            return text
        return result


def _compile(patterns: Iterable[object]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        if isinstance(pattern, re.Pattern):
            if not isinstance(pattern.pattern, str):
                msg = f"redact patterns must be text patterns, got {pattern!r}"
                raise TypeError(msg)
            compiled.append(pattern)
        elif isinstance(pattern, str):
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                msg = f"invalid redact pattern {pattern!r}: {exc}"
                raise ValueError(msg) from exc
        else:
            msg = f"redact patterns must be str or re.Pattern, got {pattern!r}"
            raise TypeError(msg)
    return compiled


def build_redactor(spec: RedactSpec | Redactor) -> Redactor | None:
    """Validate *spec* and return a :class:`Redactor`, or ``None`` when off.

    Raises:
        TypeError: If *spec* is not ``None``, a callable or an iterable of
            ``str`` / :class:`re.Pattern`.  A bare ``str`` is rejected — it
            would be read as one pattern per character; wrap it in a list.
        ValueError: If a pattern is not a valid regular expression.
    """
    if spec is None or isinstance(spec, Redactor):
        return spec
    if isinstance(spec, (str, bytes)):
        msg = (
            "redact must be a callable or an iterable of patterns, got a "
            f"single {type(spec).__name__}; wrap it in a list"
        )
        raise TypeError(msg)
    if callable(spec):
        return Redactor(cast("Callable[[str], str]", spec))
    if not isinstance(spec, Iterable):
        msg = (
            f"redact must be None, a callable or an iterable of patterns, got {spec!r}"
        )
        raise TypeError(msg)
    patterns = _compile(spec)

    def _substitute(text: str) -> str:
        for pattern in patterns:
            text = pattern.sub(REDACTED, text)
        return text

    return Redactor(_substitute)


class RedactingFilter(logging.Filter):
    """Handler filter that redacts a copy of each record (ADR-085).

    The filter returns a redacted **copy**, so the original record — seen
    by any other handler, such as a test's capture handler — is left
    untouched.  The formatted message, the formatted traceback and the
    stack info are redacted; the traceback is formatted with the handler's
    own *formatter* so the output matches an unredacted line.
    """

    def __init__(self, redactor: Redactor, formatter: logging.Formatter) -> None:
        super().__init__()
        self._redactor = redactor
        self._formatter = formatter

    @override
    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        redacted = copy.copy(record)
        redacted.msg = self._redactor(record.getMessage())
        redacted.args = None
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = record.exc_text or self._formatter.formatException(
                record.exc_info
            )
            redacted.exc_text = self._redactor(exc_text)
            if not redacted.exc_text:
                # An empty cached traceback makes logging.Formatter fall back
                # to exc_info and regenerate the original, unredacted text.
                redacted.exc_info = None
        elif record.exc_text:
            redacted.exc_text = self._redactor(record.exc_text)
        if record.stack_info:
            redacted.stack_info = self._redactor(record.stack_info)
        return redacted
