"""Integration tests — consumer error_type_map opt-in (cos-ooj).

Validates the LEAK-01 targeted opt-in end-to-end: an app that registers a
domain exception via ``App(error_type_map=...)`` gets that exception's full
message published on the broker-visible error topic, while an unregistered
exception keeps having its message redacted to the class name.

Test Techniques Used:
    - Integration Testing: full lifecycle via AppHarness (real create_services).
    - Equivalence Partitioning: registered vs unregistered exception types.

See Also:
    ADR-011 — Error handling and publishing (pluggable + LEAK-01 opt-in).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

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


def _error_message(harness: AppHarness) -> str:
    """Return the ``message`` field of the payload on the global error topic."""
    messages = harness.mqtt.get_messages_for("testapp/error")
    assert messages, "expected an error published on the global error topic"
    return json.loads(messages[0][0])["message"]


class TestErrorTypeMapOptIn:
    """Registered domain exceptions publish full messages; others are redacted."""

    async def test_registered_exception_publishes_full_message(self) -> None:
        """A registered domain type keeps its (safe) message on the error topic."""
        harness = AppHarness.create(
            error_type_map={CalDavConnectionError: "caldav_connection_error"},
        )

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "server unreachable"

    async def test_unregistered_exception_is_redacted(self) -> None:
        """Without registration the message is redacted to the class name."""
        harness = AppHarness.create()

        await _run_command_raising(harness, CalDavConnectionError("server unreachable"))

        assert _error_message(harness) == "CalDavConnectionError"
