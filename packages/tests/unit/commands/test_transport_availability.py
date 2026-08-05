"""Unit tests — transport availability signaling.

Covers DeviceContext.mark_unavailable() and the CommandRunner
unavailable_on / auto-recovery paths added by the transport
availability signaling feature.

Test Techniques Used:
    - Mock-based Isolation: AsyncMock replaces HealthReporter so no
      real MQTT publishing occurs.
    - Specification-based Testing: assert exact call signatures on the
      mock to verify the framework calls the right method.
    - Boundary Testing: no-health-reporter no-op, root vs. non-root,
      matching vs. non-matching exception types.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health._reporter import HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._registration import _CommandRegistration
from cosalette._runners._command_runner import CommandRunner
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    name: str = "sensor",
    is_root: bool = False,
    health_reporter: HealthReporter | None = None,
) -> DeviceContext:
    return DeviceContext(
        name=name,
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="testapp",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        is_root=is_root,
        health_reporter=health_reporter,
    )


def _make_reg(
    func: Callable[..., Awaitable[dict[str, object] | None]],
    *,
    unavailable_on: tuple[type[Exception], ...] | None = None,
    name: str = "sensor",
    is_root: bool = False,
) -> _CommandRegistration:
    plan = build_injection_plan(func)
    return _CommandRegistration(
        name=name,
        func=func,
        injection_plan=plan,
        mqtt_params=frozenset(),
        is_root=is_root,
        unavailable_on=unavailable_on,
    )


async def _run_cmd(
    reg: _CommandRegistration,
    ctx: DeviceContext,
) -> None:
    """Execute reg through CommandRunner with a fresh MockMqttClient."""
    runner = CommandRunner(store=None)
    error_publisher = ErrorPublisher(mqtt=MockMqttClient(), topic_prefix="testapp")
    await runner.run_command(
        reg=reg,
        ctx=ctx,
        topic="testapp/sensor/set",
        payload="",
        error_publisher=error_publisher,
    )


# ---------------------------------------------------------------------------
# DeviceContext.mark_unavailable() tests
# ---------------------------------------------------------------------------


class TestMarkUnavailable:
    """Direct tests for DeviceContext.mark_unavailable()."""

    async def test_publishes_offline_via_health_reporter(self) -> None:
        """mark_unavailable() calls publish_device_unavailable with device name."""
        mock_reporter = AsyncMock()
        ctx = _make_ctx(health_reporter=mock_reporter)

        await ctx.mark_unavailable()

        mock_reporter.publish_device_unavailable.assert_awaited_once_with(
            "sensor", is_root=False
        )

    async def test_sets_unavailable_flag(self) -> None:
        """mark_unavailable() sets ctx._is_unavailable to True."""
        ctx = _make_ctx(health_reporter=AsyncMock())
        assert ctx._is_unavailable is False

        await ctx.mark_unavailable()

        assert ctx._is_unavailable is True

    async def test_no_health_reporter_is_noop(self) -> None:
        """mark_unavailable() with no health reporter returns early.

        The _is_unavailable flag stays False.
        """
        ctx = _make_ctx(health_reporter=None)

        await ctx.mark_unavailable()  # must not raise

        assert ctx._is_unavailable is False

    async def test_root_device_passes_is_root_true(self) -> None:
        """mark_unavailable() passes is_root=True for root-device contexts."""
        mock_reporter = AsyncMock()
        ctx = _make_ctx(is_root=True, health_reporter=mock_reporter)

        await ctx.mark_unavailable()

        mock_reporter.publish_device_unavailable.assert_awaited_once_with(
            "sensor", is_root=True
        )


# ---------------------------------------------------------------------------
# DeviceContext.mark_available() tests
# ---------------------------------------------------------------------------


class TestMarkAvailable:
    """Direct tests for DeviceContext.mark_available()."""

    async def test_no_health_reporter_is_noop(self) -> None:
        """mark_available() with no health reporter returns early.

        The _is_unavailable flag is left unchanged.
        """
        ctx = _make_ctx(health_reporter=None)
        ctx._is_unavailable = True

        await ctx.mark_available()  # must not raise

        assert ctx._is_unavailable is True

    async def test_clears_unavailable_flag_and_publishes_online(self) -> None:
        """mark_available() clears _is_unavailable and publishes 'online'."""
        mock_reporter = AsyncMock()
        ctx = _make_ctx(health_reporter=mock_reporter)
        ctx._is_unavailable = True

        await ctx.mark_available()

        assert ctx._is_unavailable is False
        mock_reporter.publish_device_available.assert_awaited_once_with(
            "sensor", is_root=False
        )

    async def test_root_device_passes_is_root_true(self) -> None:
        """mark_available() passes is_root=True for root-device contexts."""
        mock_reporter = AsyncMock()
        ctx = _make_ctx(is_root=True, health_reporter=mock_reporter)
        ctx._is_unavailable = True

        await ctx.mark_available()

        mock_reporter.publish_device_available.assert_awaited_once_with(
            "sensor", is_root=True
        )

    async def test_round_trip_unavailable_available_unavailable(self) -> None:
        """mark_unavailable -> mark_available -> mark_unavailable round-trips.

        Confirms mark_available() is a real symmetric counterpart, not
        just a one-shot recovery hook.
        """
        mock_reporter = AsyncMock()
        ctx = _make_ctx(health_reporter=mock_reporter)

        await ctx.mark_unavailable()
        assert ctx._is_unavailable is True

        await ctx.mark_available()
        assert ctx._is_unavailable is False

        await ctx.mark_unavailable()
        assert ctx._is_unavailable is True

        assert mock_reporter.publish_device_unavailable.await_count == 2
        mock_reporter.publish_device_available.assert_awaited_once_with(
            "sensor", is_root=False
        )


# ---------------------------------------------------------------------------
# CommandRunner auto-recovery and unavailable_on tests
# ---------------------------------------------------------------------------


class TestCommandRunnerTransportAvailability:
    """CommandRunner behavior for unavailable_on and auto-recovery."""

    async def test_auto_recovery_after_success(self) -> None:
        """Successful invocation after mark_unavailable publishes 'online'.

        Also resets the _is_unavailable flag to False.
        """
        mock_reporter = AsyncMock()

        async def handler(ctx: DeviceContext) -> None:
            pass  # succeeds

        reg = _make_reg(handler)
        ctx = _make_ctx(health_reporter=mock_reporter)
        ctx._is_unavailable = True  # simulate a previously-failed transport

        await _run_cmd(reg, ctx)

        mock_reporter.publish_device_available.assert_awaited_once_with(
            "sensor", is_root=False
        )
        assert ctx._is_unavailable is False

    async def test_auto_recovery_not_triggered_when_already_available(self) -> None:
        """publish_device_available is NOT called when _is_unavailable is False."""
        mock_reporter = AsyncMock()

        async def handler(ctx: DeviceContext) -> None:
            pass

        reg = _make_reg(handler)
        ctx = _make_ctx(health_reporter=mock_reporter)
        # _is_unavailable starts False — no recovery needed

        await _run_cmd(reg, ctx)

        mock_reporter.publish_device_available.assert_not_called()

    async def test_matching_exception_is_suppressed(self) -> None:
        """Matching unavailable_on exception does not propagate from run_command."""

        async def handler(ctx: DeviceContext) -> None:
            raise ValueError("transport error")

        reg = _make_reg(handler, unavailable_on=(ValueError,))
        ctx = _make_ctx(health_reporter=AsyncMock())

        # run_command must not raise (exception is swallowed)
        await _run_cmd(reg, ctx)

    async def test_matching_exception_marks_unavailable(self) -> None:
        """Matching exception calls publish_device_unavailable and sets flag."""
        mock_reporter = AsyncMock()

        async def handler(ctx: DeviceContext) -> None:
            raise ValueError("transport error")

        reg = _make_reg(handler, unavailable_on=(ValueError,))
        ctx = _make_ctx(health_reporter=mock_reporter)

        await _run_cmd(reg, ctx)

        assert ctx._is_unavailable is True
        mock_reporter.publish_device_unavailable.assert_awaited_once_with(
            "sensor", is_root=False
        )

    async def test_non_matching_exception_does_not_mark_unavailable(
        self,
    ) -> None:
        """Non-matching exception: _is_unavailable stays False, reporter skipped."""
        mock_reporter = AsyncMock()

        async def handler(ctx: DeviceContext) -> None:
            raise RuntimeError("unrelated error")

        reg = _make_reg(handler, unavailable_on=(ValueError,))
        ctx = _make_ctx(health_reporter=mock_reporter)

        await _run_cmd(reg, ctx)

        assert ctx._is_unavailable is False
        mock_reporter.publish_device_unavailable.assert_not_called()

    async def test_non_matching_exception_publishes_to_error_topic(self) -> None:
        """Non-matching exception goes to the error publisher (not silently dropped)."""
        mock_reporter = AsyncMock()
        mock_mqtt = MockMqttClient()

        async def handler(ctx: DeviceContext) -> None:
            raise RuntimeError("unrelated error")

        reg = _make_reg(handler, unavailable_on=(ValueError,))
        ctx = DeviceContext(
            name="sensor",
            settings=make_settings(),
            mqtt=mock_mqtt,
            topic_prefix="testapp",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
            health_reporter=mock_reporter,
        )

        runner = CommandRunner(store=None)
        error_publisher = ErrorPublisher(mqtt=mock_mqtt, topic_prefix="testapp")
        await runner.run_command(
            reg=reg,
            ctx=ctx,
            topic="testapp/sensor/set",
            payload="",
            error_publisher=error_publisher,
        )

        error_msgs = mock_mqtt.get_messages_for("testapp/sensor/error")
        assert error_msgs, "Expected error to be published to error topic"
