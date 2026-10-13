"""Log severity of framework-rejected command input (ADR-011, cos-ked4.6).

Test Techniques Used:
- Decision Table: origin (framework input binding, handler body, sub-command
    proxy) x error type -> local log records and error-topic payload.
- Boundary Value Analysis: ``publish_error_safely`` ``log_traceback``
    default (``True``) versus ``False``.
- Regression: handler faults keep ERROR plus traceback; the error-topic
    payload is unchanged by the log-only change.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal, override
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from cosalette import (
    FRAMEWORK_DISCLOSED_ERRORS,
    Depends,
    LoggingSettings,
    Payload,
    PayloadValidationError,
)
from cosalette._runners._runner_utils import publish_error_safely
from cosalette.testing import AppHarness

_RUNNER_LOGGER = "cosalette._runners._command_runner"
_ERROR_TOPIC = "testapp/error"
_DEPENDENCY_ERROR = PayloadValidationError("dependency failed")


def _raise_dependency_error() -> str:
    raise _DEPENDENCY_ERROR


class _Cmd(BaseModel):
    state: Literal["on", "off"] | None = None


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def at(self, level: int) -> list[logging.LogRecord]:
        return [r for r in self.records if r.levelno == level]


@contextmanager  # ty: ignore[deprecated]
def _recording() -> Iterator[_Recorder]:
    # configure_logging() replaces root handlers, so caplog cannot see the
    # records; listen on the framework logger directly.
    handler = _Recorder()
    logger = logging.getLogger("cosalette")
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _harness() -> AppHarness:
    # The runner's DEBUG record only exists when its logger allows DEBUG. The
    # tests inspect the sanitised messages, so disclose them (ADR-091).
    return AppHarness.create(
        logging=LoggingSettings(levels={_RUNNER_LOGGER: "DEBUG"}),
        disclose_messages_for=FRAMEWORK_DISCLOSED_ERRORS,
    )


async def _deliver(
    harness: AppHarness, topic: str, payload: str
) -> tuple[_Recorder, dict[str, Any]]:
    """Run the app, deliver one message, and return logs plus the error payload."""
    with _recording() as log:
        run = asyncio.create_task(harness.run())
        try:
            while topic not in harness.mqtt.subscriptions:
                await asyncio.sleep(0.01)
            await harness.mqtt.deliver(topic, payload)
            while not harness.mqtt.get_messages_for(_ERROR_TOPIC):
                await asyncio.sleep(0.01)
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)
    error = json.loads(harness.mqtt.get_messages_for(_ERROR_TOPIC)[0][0])
    return log, error


def _register_typed(harness: AppHarness) -> None:
    @harness.app.command("display")
    async def handle_display(cmd: Annotated[_Cmd, Payload()]) -> None:
        return None


def _register_raising(harness: AppHarness, exc: Exception) -> None:
    @harness.app.command("display")
    async def handle_display(payload: str) -> None:
        raise exc


def _register_sub(harness: AppHarness) -> None:
    @harness.app.command("mode", sub="auto")
    async def handle_auto(cmd: Annotated[_Cmd, Payload()]) -> None:
        return None

    @harness.app.command("mode", sub="manual")
    async def handle_manual() -> None:
        return None


class TestFrameworkRejectedInput:
    """Decision table rows where the framework rejects the input."""

    @pytest.mark.parametrize(
        ("register", "topic", "payload", "error_type"),
        [
            pytest.param(
                _register_typed,
                "testapp/display/set",
                "not-json",
                "invalid_payload",
                id="binding-bad-json",
            ),
            pytest.param(
                _register_typed,
                "testapp/display/set",
                '{"state":"dim"}',
                "invalid_payload",
                id="binding-schema-failure",
            ),
            pytest.param(
                _register_sub,
                "testapp/mode/set",
                '{"command":"auto","state":"dim"}',
                "invalid_payload",
                id="sub-command-binding-schema-failure",
            ),
        ],
    )
    async def test_binding_failure_logs_one_warning_and_debug_traceback(
        self,
        register: Callable[[AppHarness], None],
        topic: str,
        payload: str,
        error_type: str,
    ) -> None:
        """Binding failure: one WARNING without traceback, no ERROR, DEBUG cause."""
        # Arrange
        harness = _harness()
        register(harness)

        # Act
        log, error = await _deliver(harness, topic, payload)

        # Assert
        warnings = log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is None
        assert log.at(logging.ERROR) == []
        debug = [r for r in log.at(logging.DEBUG) if r.name == _RUNNER_LOGGER]
        assert len(debug) == 1
        assert debug[0].exc_info is not None
        assert isinstance(debug[0].exc_info[1], PayloadValidationError)
        assert error["error_type"] == error_type
        assert "(parameter 'cmd')" in error["message"]

    @pytest.mark.parametrize(
        ("payload", "error_type"),
        [
            pytest.param("not-json", "invalid_json", id="invalid-json"),
            pytest.param("[1]", "invalid_json", id="non-object"),
            pytest.param('{"state":"on"}', "missing_sub_key", id="missing-sub-key"),
        ],
    )
    async def test_sub_command_proxy_error_publishes_without_traceback(
        self, payload: str, error_type: str
    ) -> None:
        """Sub-command proxy errors: one WARNING without exc_info, no ERROR."""
        # Arrange
        harness = _harness()
        _register_sub(harness)

        # Act
        log, error = await _deliver(harness, "testapp/mode/set", payload)

        # Assert
        warnings = log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is None
        assert log.at(logging.ERROR) == []
        assert error["error_type"] == error_type

    async def test_unknown_sub_command_keeps_raw_value_warning(self) -> None:
        """Unknown sub-command: local raw-value WARNING plus publish WARNING."""
        # Arrange
        harness = _harness()
        _register_sub(harness)

        # Act
        log, error = await _deliver(harness, "testapp/mode/set", '{"command":"turbo"}')

        # Assert
        warnings = log.at(logging.WARNING)
        assert [w.name for w in warnings] == [_RUNNER_LOGGER, "cosalette._errors"]
        assert "fp=" in warnings[0].getMessage()
        assert "turbo" not in warnings[0].getMessage()
        assert all(w.exc_info is None for w in warnings)
        assert log.at(logging.ERROR) == []
        assert error["error_type"] == "unknown_sub_command"
        assert "turbo" not in error["message"]


class TestHandlerFaults:
    """Decision table rows where handler code raises: behaviour unchanged."""

    @pytest.mark.parametrize(
        ("exc", "error_type"),
        [
            pytest.param(
                PayloadValidationError("Payload validation failed (nested)"),
                "invalid_payload",
                id="handler-payload-validation-error",
            ),
            pytest.param(RuntimeError("boom"), "error", id="handler-runtime-error"),
        ],
    )
    async def test_handler_exception_logs_error_and_traceback(
        self, exc: Exception, error_type: str
    ) -> None:
        """Handler-raised exceptions keep ERROR plus a WARNING with traceback."""
        # Arrange
        harness = _harness()
        _register_raising(harness, exc)

        # Act
        log, error = await _deliver(harness, "testapp/display/set", "{}")

        # Assert
        errors = log.at(logging.ERROR)
        assert len(errors) == 1
        assert errors[0].name == _RUNNER_LOGGER
        warnings = log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[1] is exc
        assert error["error_type"] == error_type

    async def test_dependency_payload_validation_error_remains_handler_fault(
        self,
    ) -> None:
        """An application dependency's error is not framework input rejection."""
        # Arrange
        harness = _harness()

        @harness.app.command("display")
        async def handle_display(
            value: Annotated[str, Depends(_raise_dependency_error)],
        ) -> None:
            return None

        # Act
        log, error = await _deliver(harness, "testapp/display/set", "{}")

        # Assert
        errors = log.at(logging.ERROR)
        assert len(errors) == 1
        assert errors[0].name == _RUNNER_LOGGER
        warnings = log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[1] is _DEPENDENCY_ERROR
        assert error["error_type"] == "invalid_payload"


class TestPublishErrorSafelyTraceback:
    """Boundary: ``log_traceback`` default and explicit ``False``."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param({}, True, id="default"),
            pytest.param({"log_traceback": False}, False, id="false"),
        ],
    )
    async def test_forwards_log_traceback(
        self, kwargs: dict[str, bool], expected: bool
    ) -> None:
        """The flag reaches ``ErrorPublisher.publish`` unchanged."""
        # Arrange
        publisher = AsyncMock()
        exc = ValueError("x")

        # Act
        await publish_error_safely(publisher, exc, "dev", False, **kwargs)

        # Assert
        publisher.publish.assert_awaited_once_with(
            exc, device="dev", is_root=False, log_traceback=expected
        )
