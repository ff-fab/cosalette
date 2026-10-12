"""Integration tests — error labels, message disclosure and verbose gating.

Validates ADR-091 end-to-end: ``App(error_type_map=...)`` only assigns the
``error_type`` label, a message is published only for the exact types listed
in ``App(disclose_messages_for=...)``, and verbose publishing needs both
``App(allow_error_publish_verbose=True)`` and the ``mqtt.error_publish_verbose``
setting.  Rejected typed-command input carries the framework label
``invalid_payload``.

Test Techniques Used:
    - Integration Testing: full lifecycle via AppHarness (real create_services).
    - Equivalence Partitioning: registered vs unregistered exception types;
      ``disclose_messages_for=None`` vs ``frozenset()`` (same partition).
    - Decision Table: disclosure mode x rejected-input kind for typed commands;
      error source x ``FRAMEWORK_DISCLOSED_ERRORS | {AppError}`` membership;
      app verbose permission x operator verbose setting.

See Also:
    ADR-011 — Error handling and publishing.
    ADR-091 — Labelling-only error type map, secure-by-default disclosure.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal, override

import pytest
from pydantic import BaseModel

from cosalette import FRAMEWORK_DISCLOSED_ERRORS, App, MqttSettings, Payload
from cosalette._context import DeviceContext
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


class CalDavConnectionError(Exception):
    """App-owned domain exception carrying an intentionally safe message."""


async def _wait_for_error(harness: AppHarness) -> None:
    """Yield to the loop until an error is published, or fail after a bound.

    The error publish is fire-and-forget, so poll the recorded messages rather
    than couple the test to a fixed number of scheduler turns.
    """
    for _ in range(100):
        if harness.mqtt.get_messages_for("testapp/error"):
            return
        await asyncio.sleep(0)
    raise AssertionError("error was not published within the expected window")


async def _run_command_raising(
    harness: AppHarness,
    exc: Exception,
) -> None:
    """Register a device whose command handler raises *exc*, then deliver one."""
    handler_registered = asyncio.Event()

    @harness.app.device("blind")
    async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
        @ctx.on_command
        async def handle(sub_topic: str | None, payload: str) -> None:
            raise exc

        handler_registered.set()
        while not ctx.shutdown_requested:
            await ctx.sleep(1)
            yield

    async def _simulate() -> None:
        await handler_registered.wait()
        await harness.mqtt.deliver("testapp/blind/set", "OPEN")
        await _wait_for_error(harness)
        harness.trigger_shutdown()

    _task = asyncio.create_task(_simulate())
    await asyncio.wait_for(harness.run(), timeout=5.0)


async def _deliver(
    harness: AppHarness, register: Callable[[], object], topic: str, payload: str
) -> None:
    """Register ``@app.command`` handlers via *register*, then deliver one message."""
    register()

    async def _simulate() -> None:
        await asyncio.sleep(0.02)
        await harness.mqtt.deliver(topic, payload)
        await _wait_for_error(harness)
        harness.trigger_shutdown()

    _task = asyncio.create_task(_simulate())
    await asyncio.wait_for(harness.run(), timeout=5.0)


class _ValveCmd(BaseModel):
    """Typed command payload; the literal makes rejected values easy to spot."""

    state: Literal["open", "closed"]


def _register_typed_command(harness: AppHarness) -> None:
    @harness.app.command("valve")
    async def handle(cmd: Annotated[_ValveCmd, Payload()]) -> None: ...


def _error_message(harness: AppHarness) -> str:
    """Return the ``message`` field of the payload on the global error topic."""
    messages = harness.mqtt.get_messages_for("testapp/error")
    assert messages, "expected an error published on the global error topic"
    payload = json.loads(messages[0][0])
    assert isinstance(payload, dict)
    message = payload["message"]
    assert isinstance(message, str)
    return message


def _error_type(harness: AppHarness) -> str:
    """Return the ``error_type`` field of the payload on the global error topic."""
    messages = harness.mqtt.get_messages_for("testapp/error")
    assert messages, "expected an error published on the global error topic"
    payload = json.loads(messages[0][0])
    assert isinstance(payload, dict)
    error_type = payload["error_type"]
    assert isinstance(error_type, str)
    return error_type


class TestErrorTypeMapLabelsOnly:
    """error_type_map assigns the label only; it never discloses (ADR-091)."""

    async def test_registered_exception_publishes_label_and_class_name(
        self,
    ) -> None:
        """A registered domain type gets its label but not its message."""
        harness = AppHarness.create(
            error_type_map={CalDavConnectionError: "caldav_connection_error"},
        )

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_type(harness) == "caldav_connection_error"
        assert _error_message(harness) == "CalDavConnectionError"

    async def test_unregistered_exception_is_redacted(self) -> None:
        """Without registration the message is redacted to the class name."""
        harness = AppHarness.create()

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "CalDavConnectionError"


class TestDiscloseMessagesForOptIn:
    """disclose_messages_for alone decides disclosure (F-DP1, ADR-091)."""

    async def test_disclose_messages_for_publishes_full_message(self) -> None:
        """A type listed in disclose_messages_for keeps its message, mapped or not."""
        harness = AppHarness.create(
            error_type_map={CalDavConnectionError: "caldav_connection_error"},
            disclose_messages_for=frozenset({CalDavConnectionError}),
        )

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "server unreachable"

    async def test_mapped_type_redacted_when_not_in_disclose_messages_for(
        self,
    ) -> None:
        """A mapped type is redacted when disclose_messages_for excludes it.

        Registering a label via error_type_map never implies disclosure.
        """
        harness = AppHarness.create(
            error_type_map={CalDavConnectionError: "caldav_connection_error"},
            disclose_messages_for=frozenset(),
        )

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "CalDavConnectionError"

    async def test_unlabelled_type_discloses_when_in_disclose_messages_for(
        self,
    ) -> None:
        """A type in disclose_messages_for but absent from error_type_map is
        disclosed with fallback error_type 'error' — disclosure and labeling
        are fully independent (F-DP1 pure decoupling).

        Technique: Specification-based Testing — exercises the wiring from
        App through create_services to ErrorPublisher.
        """
        harness = AppHarness.create(
            disclose_messages_for=frozenset({CalDavConnectionError}),
        )

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "server unreachable"
        assert _error_type(harness) == "error"


class TestRedactHook:
    """App(redact=...) scrubs disclosed messages end-to-end (ADR-085)."""

    async def test_disclosed_message_is_redacted(self) -> None:
        """A disclosed message goes through the redactor before publishing."""
        harness = AppHarness.create(
            disclose_messages_for=frozenset({CalDavConnectionError}),
            redact=[r"token=\S+"],
        )

        await _run_command_raising(
            harness, CalDavConnectionError("server unreachable token=abc")
        )

        assert _error_message(harness) == "server unreachable [REDACTED]"

    async def test_undisclosed_message_still_class_name_only(self) -> None:
        """Redaction never widens disclosure: undisclosed stays the class name."""
        harness = AppHarness.create(redact=[r"token=\S+"])

        await _run_command_raising(
            harness, CalDavConnectionError("server unreachable token=abc")
        )

        assert _error_message(harness) == "CalDavConnectionError"


class TestRejectedTypedCommandInput:
    """Rejected typed-command input is labelled ``invalid_payload`` (ADR-011).

    Technique: Decision Table — disclosure mode (framework errors listed /
    default / explicit empty set) x input kind (schema failure / bad JSON).
    """

    async def test_schema_failure_publishes_sanitised_summary(self) -> None:
        """Listed: framework label plus the summary, never the input value."""
        harness = AppHarness.create(disclose_messages_for=FRAMEWORK_DISCLOSED_ERRORS)

        await _deliver(
            harness,
            lambda: _register_typed_command(harness),
            "testapp/valve/set",
            '{"state": "secret-ajar"}',
        )

        assert _error_type(harness) == "invalid_payload"
        message = _error_message(harness)
        assert message.startswith("Payload validation failed (parameter 'cmd')")
        assert message.endswith("[field]: type=literal_error")
        assert "secret-ajar" not in message

    async def test_bad_json_publishes_sanitised_message(self) -> None:
        """Listed: a JSON decode failure is ``invalid_payload`` too."""
        harness = AppHarness.create(disclose_messages_for=FRAMEWORK_DISCLOSED_ERRORS)

        await _deliver(
            harness,
            lambda: _register_typed_command(harness),
            "testapp/valve/set",
            "secret-not-json{",
        )

        assert _error_type(harness) == "invalid_payload"
        message = _error_message(harness)
        assert message.startswith("MQTT payload is not valid JSON (parameter 'cmd')")
        assert "secret-not-json" not in message

    @pytest.mark.parametrize(
        ("disclose", "payload"),
        [
            pytest.param(None, '{"state": "secret-ajar"}', id="default-schema"),
            pytest.param(None, "secret-not-json{", id="default-bad-json"),
            pytest.param(frozenset(), '{"state": "secret-ajar"}', id="empty-schema"),
            pytest.param(frozenset(), "secret-not-json{", id="empty-bad-json"),
        ],
    )
    async def test_unlisted_framework_error_publishes_class_name_only(
        self, disclose: frozenset[type[Exception]] | None, payload: str
    ) -> None:
        """Default and empty set alike: the label, never the message.

        Technique: Equivalence Partitioning — omitting disclose_messages_for
        behaves exactly like an explicit empty set (ADR-091 point 2).
        """
        harness = AppHarness.create(disclose_messages_for=disclose)

        await _deliver(
            harness,
            lambda: _register_typed_command(harness),
            "testapp/valve/set",
            payload,
        )

        assert _error_type(harness) == "invalid_payload"
        assert _error_message(harness) == "PayloadValidationError"

    async def test_return_validation_error_stays_generic(self) -> None:
        """A handler returning the wrong shape is an app fault: ``error``."""
        harness = AppHarness.create()

        def register() -> None:
            @harness.app.command("valve")
            async def handle(payload: str) -> _ValveCmd:
                return {"state": "ajar"}  # ty: ignore[invalid-return-type]

        await _deliver(harness, register, "testapp/valve/set", "go")

        assert _error_type(harness) == "error"
        assert _error_message(harness) == "ReturnValidationError"

    async def test_sub_command_bad_json_stays_invalid_json(self) -> None:
        """Regression: sub-command routing failures keep ``invalid_json``."""
        harness = AppHarness.create()

        def register() -> None:
            @harness.app.command("valve", sub="open")
            async def handle(payload: str) -> None: ...

        await _deliver(harness, register, "testapp/valve/set", "not json{")

        assert _error_type(harness) == "invalid_json"


class TestTelemetryTriggerValidation:
    """Typed telemetry trigger failures share ``PayloadValidationError``."""

    @pytest.mark.parametrize(
        ("disclose_messages_for", "expected_message"),
        [
            pytest.param(
                FRAMEWORK_DISCLOSED_ERRORS,
                "Payload validation failed",
                id="framework-listed",
            ),
            pytest.param(None, "PayloadValidationError", id="default"),
            pytest.param(frozenset(), "PayloadValidationError", id="explicit-empty"),
        ],
    )
    async def test_trigger_validation_is_invalid_payload(
        self,
        disclose_messages_for: frozenset[type[Exception]] | None,
        expected_message: str,
    ) -> None:
        """Trigger validation follows the same label and disclosure rules."""
        harness = AppHarness.create(disclose_messages_for=disclose_messages_for)

        @harness.app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor(
            value: Annotated[dict[str, int] | None, Payload()] = None,
        ) -> dict[str, object]:
            return {"value": value}

        async def _simulate() -> None:
            await asyncio.sleep(0.02)
            await harness.mqtt.deliver(
                "testapp/sensor/set", '{"dynamic-field-marker-xyz": "invalid"}'
            )
            await _wait_for_error(harness)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert _error_type(harness) == "invalid_payload"
        message = _error_message(harness)
        assert message.startswith(expected_message)
        assert "dynamic-field-marker-xyz" not in message


class _UnlistedError(Exception):
    """App-owned exception that is not in the disclosure set."""


def _register_sub_commands(harness: AppHarness) -> None:
    @harness.app.command("valve", sub="open")
    async def handle(payload: str) -> None: ...


def _register_raising(harness: AppHarness, exc: Exception) -> None:
    @harness.app.command("valve")
    async def handle(payload: str) -> None:
        raise exc


class TestFrameworkDisclosedErrors:
    """``FRAMEWORK_DISCLOSED_ERRORS | {AppError}`` keeps framework messages.

    Technique: Decision Table — error source x set membership. The set keeps
    replace semantics: unlisted types, including the builtin TimeoutError,
    publish the class name only (ADR-091 point 4).
    """

    @pytest.mark.parametrize(
        ("register", "payload", "error_type", "message_prefix"),
        [
            pytest.param(
                _register_typed_command,
                '{"state": "ajar"}',
                "invalid_payload",
                "Payload validation failed",
                id="PayloadValidationError",
            ),
            pytest.param(
                _register_sub_commands,
                '{"command": on}',
                "invalid_json",
                "unexpected character",
                id="InvalidJsonError",
            ),
            pytest.param(
                _register_sub_commands,
                '{"command": "blink"}',
                "unknown_sub_command",
                "Unknown sub-command for 'valve'",
                id="UnknownSubCommandError",
            ),
            pytest.param(
                lambda h: _register_raising(h, CalDavConnectionError("unreachable")),
                "go",
                "error",
                "unreachable",
                id="listed-app-error",
            ),
            pytest.param(
                lambda h: _register_raising(h, _UnlistedError("secret")),
                "go",
                "error",
                "_UnlistedError",
                id="unlisted-app-error",
            ),
            pytest.param(
                lambda h: _register_raising(h, TimeoutError("http://secret")),
                "go",
                "timeout",
                "TimeoutError",
                id="handler-TimeoutError",
            ),
        ],
    )
    async def test_disclosure_decision(
        self,
        register: Callable[[AppHarness], None],
        payload: str,
        error_type: str,
        message_prefix: str,
    ) -> None:
        harness = AppHarness.create(
            disclose_messages_for=FRAMEWORK_DISCLOSED_ERRORS | {CalDavConnectionError},
        )

        await _deliver(harness, lambda: register(harness), "testapp/valve/set", payload)

        assert _error_type(harness) == error_type
        message = _error_message(harness)
        assert message.startswith(message_prefix)
        assert "secret" not in message


class _Recorder(logging.Handler):
    """Collect records; configure_logging() replaces root handlers."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


_VERBOSE_IGNORED = "MQTT__ERROR_PUBLISH_VERBOSE is set but this app does not allow"


class TestVerboseGating:
    """Verbose publishing needs the app opt-in and the setting (ADR-091 point 5).

    Technique: Decision Table — ``App(allow_error_publish_verbose=...)`` x
    ``mqtt.error_publish_verbose``; only the row with both on discloses, and
    only the row with the setting alone warns.
    """

    @pytest.mark.parametrize(
        ("allow", "setting", "expected_message", "warns"),
        [
            pytest.param(False, False, "_UnlistedError", False, id="neither"),
            pytest.param(True, False, "_UnlistedError", False, id="app-only"),
            pytest.param(False, True, "_UnlistedError", True, id="setting-only"),
            pytest.param(True, True, "boom detail", False, id="both"),
        ],
    )
    async def test_verbose_needs_app_and_setting(
        self, allow: bool, setting: bool, expected_message: str, warns: bool
    ) -> None:
        # Arrange
        harness = AppHarness.create(
            allow_error_publish_verbose=allow,
            mqtt=MqttSettings(error_publish_verbose=setting),
        )
        recorder = _Recorder()
        logger = logging.getLogger("cosalette")
        logger.addHandler(recorder)

        # Act
        try:
            await _deliver(
                harness,
                lambda: _register_raising(harness, _UnlistedError("boom detail")),
                "testapp/valve/set",
                "go",
            )
        finally:
            logger.removeHandler(recorder)

        # Assert
        assert _error_message(harness) == expected_message
        ignored = [
            r
            for r in recorder.records
            if r.levelno == logging.WARNING and _VERBOSE_IGNORED in r.getMessage()
        ]
        assert len(ignored) == (1 if warns else 0)

    async def test_verbose_message_is_still_redacted(self) -> None:
        """Verbose discloses every message, but App(redact=...) still applies."""
        # Arrange
        harness = AppHarness.create(
            allow_error_publish_verbose=True,
            mqtt=MqttSettings(error_publish_verbose=True),
            redact=[r"token=\S+"],
        )

        # Act
        await _deliver(
            harness,
            lambda: _register_raising(harness, _UnlistedError("failed token=abc")),
            "testapp/valve/set",
            "go",
        )

        # Assert
        assert _error_message(harness) == "failed [REDACTED]"

    def test_verbose_is_off_by_default(self) -> None:
        """Both switches default to off (secure by default)."""
        assert App(name="testapp", version="1.0.0").allow_error_publish_verbose is False
        assert MqttSettings().error_publish_verbose is False
