"""Rejected trigger input is not a telemetry failure (ADR-082, cos-5wv5).

Test Techniques Used:
- Decision Table: origin (framework trigger parsing vs. telemetry handler)
    x effect (log level and traceback, published ``error_type``, failure
    streak, device status), for single and grouped telemetry.
- State Transition: a healthy entity stays ``ok`` through repeated rejected
    triggers and still runs a later valid trigger normally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, override

import pytest
from pydantic import BaseModel, field_validator

from cosalette import LoggingSettings, Payload, PayloadValidationError
from cosalette.testing import AppHarness, ManualClock

pytestmark = pytest.mark.unit

_RUNNER_LOGGER = "cosalette._runners._telemetry_runner"
_TRIGGER_TOPIC = "testapp/sensor/set"
_STATE_TOPIC = "testapp/sensor/state"


class _Cmd(BaseModel):
    level: int = 0

    @field_validator("level")
    @classmethod
    def reject_application_value(cls, value: int) -> int:
        if value == -1:
            raise PayloadValidationError("Application validator failed")
        return value


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def at(self, level: int) -> list[logging.LogRecord]:
        return [r for r in self.records if r.levelno == level]


@contextmanager
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


@dataclass
class _Outcome:
    log: _Recorder
    errors: list[dict[str, Any]]
    sensor: dict[str, Any]
    calls: list[_Cmd | None] = field(default_factory=list)


async def _run(
    *payloads: str, raise_on_trigger: Exception | None = None, group: str | None = None
) -> _Outcome:
    """Deliver *payloads* as triggers; return logs, errors and the heartbeat entry."""
    clock = ManualClock()
    harness = AppHarness.create(
        clock=clock, logging=LoggingSettings(levels={_RUNNER_LOGGER: "DEBUG"})
    )
    calls: list[_Cmd | None] = []

    @harness.app.telemetry("sensor", interval=3600, triggerable=True, group=group)
    async def sensor(cmd: Annotated[_Cmd | None, Payload()]) -> dict[str, int]:
        calls.append(cmd)
        if cmd is not None and raise_on_trigger is not None:
            raise raise_on_trigger
        return {"calls": len(calls)}

    if group is not None:

        @harness.app.telemetry("sibling", interval=3600, group=group)
        async def sibling() -> dict[str, int]:
            return {"ok": 1}

    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count(_STATE_TOPIC, 1)
        with _recording() as log:
            for payload in payloads:
                await harness.mqtt.deliver(_TRIGGER_TOPIC, payload)
                await clock.settle()
        await harness.advance_time(60)  # next heartbeat
        await clock.settle()
    finally:
        harness.trigger_shutdown()
        await task
    errors = [json.loads(p) for p, *_ in harness.messages_for("testapp/error")]
    # The last status message is the shutdown "offline"; take the last heartbeat.
    heartbeats = [p for p, *_ in harness.messages_for("testapp/status") if p[0] == "{"]
    heartbeat = json.loads(heartbeats[-1])
    return _Outcome(log, errors, heartbeat["devices"]["sensor"], calls)


class TestTriggerParseRejected:
    """Decision table rows where the framework rejects the trigger payload."""

    @pytest.mark.parametrize("group", [None, "g"], ids=["single", "group"])
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param("not-json", id="bad-json"),
            pytest.param('{"level":"high"}', id="schema-failure"),
        ],
    )
    async def test_logged_once_published_and_not_counted(
        self, payload: str, group: str | None
    ) -> None:
        """One WARNING without traceback; no streak, status stays ok."""
        # Act
        out = await _run(payload, group=group)

        # Assert
        warnings = out.log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is None
        assert out.log.at(logging.ERROR) == []
        causes = [
            r.exc_info[1]
            for r in out.log.at(logging.DEBUG)
            if r.name == _RUNNER_LOGGER and r.exc_info
        ]
        assert len(causes) == 1
        assert isinstance(causes[0], PayloadValidationError)
        assert [e["error_type"] for e in out.errors] == ["invalid_payload"]
        assert "(parameter 'cmd')" in out.errors[0]["message"]
        assert out.sensor["status"] == "ok"
        assert out.sensor["consecutive_failures"] == 0
        assert out.sensor["last_error"] is None
        assert out.calls == [None]  # handler did not run for the bad trigger


class TestHandlerFaults:
    """Decision table rows where the handler raises: behaviour unchanged."""

    @pytest.mark.parametrize("group", [None, "g"], ids=["single", "group"])
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
    async def test_counts_as_failure_with_error_and_traceback(
        self, exc: Exception, error_type: str, group: str | None
    ) -> None:
        """ERROR plus a WARNING with traceback; streak counts, status error."""
        # Act
        out = await _run("{}", raise_on_trigger=exc, group=group)

        # Assert
        errors = out.log.at(logging.ERROR)
        assert len(errors) == 1
        assert errors[0].name == _RUNNER_LOGGER
        warnings = out.log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[1] is exc
        assert [e["error_type"] for e in out.errors] == [error_type]
        assert out.sensor["status"] == "error"
        assert out.sensor["consecutive_failures"] == 1
        assert out.sensor["last_error"] == type(exc).__name__

    @pytest.mark.parametrize("group", [None, "g"], ids=["single", "group"])
    async def test_application_validator_error_is_a_telemetry_failure(
        self, group: str | None
    ) -> None:
        """An application-raised validation error is not rejected input."""
        out = await _run('{"level":-1}', group=group)

        errors = out.log.at(logging.ERROR)
        assert len(errors) == 1
        assert errors[0].name == _RUNNER_LOGGER
        warnings = out.log.at(logging.WARNING)
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None
        assert isinstance(warnings[0].exc_info[1], PayloadValidationError)
        assert [e["error_type"] for e in out.errors] == ["invalid_payload"]
        assert out.sensor["status"] == "error"
        assert out.sensor["consecutive_failures"] == 1
        assert out.sensor["last_error"] == "PayloadValidationError"


class TestStateTransition:
    """Healthy -> rejected triggers -> valid trigger, never via ``error``."""

    async def test_repeated_rejections_then_valid_trigger_runs_normally(self) -> None:
        """Three rejected triggers leave the entity healthy; a valid one runs."""
        # Act
        out = await _run("not-json", "[", '{"level":"x"}', '{"level":7}')

        # Assert
        assert [e["error_type"] for e in out.errors] == ["invalid_payload"] * 3
        assert out.calls == [None, _Cmd(level=7)]
        assert out.sensor["status"] == "ok"
        assert out.sensor["consecutive_failures"] == 0
        assert out.log.at(logging.ERROR) == []
