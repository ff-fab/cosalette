"""Framework errors raised while dispatching commands, and their error types.

Kept apart from :mod:`cosalette._runners._command_runner` so the error
publisher can map them without loading the command runner in apps that
register no commands (cos-8jxg.5).
"""

from __future__ import annotations


class InvalidJsonError(Exception):
    """Raised when command payload is not valid JSON."""


class MissingSubKeyError(Exception):
    """Raised when sub-command payload missing required routing key."""


class UnknownSubCommandError(Exception):
    """Raised when sub-command value is not recognized."""


_FRAMEWORK_ERROR_TYPE_MAP: dict[type[Exception], str] = {
    InvalidJsonError: "invalid_json",
    MissingSubKeyError: "missing_sub_key",
    UnknownSubCommandError: "unknown_sub_command",
    # Watchdog cancellation (ADR-060); matches the documented taxonomy
    # (reference/errors.md) which already promised this mapping.
    TimeoutError: "timeout",
}
