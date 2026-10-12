"""Framework errors raised while dispatching commands, and their error types.

Kept apart from :mod:`cosalette._runners._command_runner` so the error
publisher can map them without loading the command runner in apps that
register no commands (cos-8jxg.5).  Dependency-free, so
:class:`PayloadValidationError` lives here too instead of in the
pydantic-backed :mod:`cosalette._runners._contracts`, which re-exports it.
"""

from __future__ import annotations


class InvalidJsonError(Exception):
    """Raised when command payload is not valid JSON."""


class MissingSubKeyError(Exception):
    """Raised when sub-command payload missing required routing key."""


class UnknownSubCommandError(Exception):
    """Raised when sub-command value is not recognized."""


class PayloadValidationError(Exception):
    """Raised when an inbound MQTT payload fails schema validation.

    Provides handler and parameter context for actionable diagnostics.

    Attributes:
        param: Parameter name that failed validation, or ``None``.
        handler: Qualified name of the handler, or ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        handler: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.param = param
        self.handler = handler
        if cause is not None:
            self.__cause__ = cause


_FRAMEWORK_ERROR_TYPE_MAP: dict[type[Exception], str] = {
    InvalidJsonError: "invalid_json",
    MissingSubKeyError: "missing_sub_key",
    UnknownSubCommandError: "unknown_sub_command",
    # Rejected typed-command input; the message is the sanitised summary
    # (ADR-011 amendment 2026-10-12).
    PayloadValidationError: "invalid_payload",
    # Watchdog cancellation (ADR-060); matches the documented taxonomy
    # (reference/errors.md) which already promised this mapping.
    TimeoutError: "timeout",
}

FRAMEWORK_DISCLOSED_ERRORS: frozenset[type[Exception]] = frozenset(
    {
        InvalidJsonError,
        MissingSubKeyError,
        UnknownSubCommandError,
        PayloadValidationError,
    }
)
"""Framework exceptions whose messages the framework builds and sanitises itself.

Keep their messages under an explicit disclosure set with
``App(disclose_messages_for=cosalette.FRAMEWORK_DISCLOSED_ERRORS | {MyError})``.
The builtin :class:`TimeoutError` is excluded: library and application code
raise it too, with text the framework does not control (ADR-061).
"""
