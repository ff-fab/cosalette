"""Unit tests for per-invocation command timeout backstop.

Covers: timeout registration, timeout enforcement, TimeoutError composition with
unavailable_on, timeout=None disables backstop.

Test Techniques Used:
- Boundary Value Analysis: timeout > 0, timeout = None
- State Transition Testing: timeout → TimeoutError → error topic
- Specification-based Testing: TimeoutError ⊆ OSError composition with unavailable_on
- Mock-based Isolation: AsyncMock replaces MQTT/HealthReporter
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
    timeout: float | None = None,
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
        timeout=timeout,
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
# Registration tests
# ---------------------------------------------------------------------------


class TestCommandTimeoutRegistration:
    """Validation and registration of the timeout parameter."""

    def test_timeout_float_stored_on_registration(self) -> None:
        """Explicit positive float timeout is stored on the registration."""

        async def handler() -> dict[str, object]:
            return {}

        reg = _make_reg(handler, timeout=5.0)
        assert reg.timeout == 5.0

    def test_timeout_none_stored_on_registration(self) -> None:
        """Explicit None timeout (disabled backstop) is stored."""

        async def handler() -> dict[str, object]:
            return {}

        reg = _make_reg(handler, timeout=None)
        assert reg.timeout is None

    def test_timeout_zero_stored_on_registration(self) -> None:
        """timeout=0 is stored as-is (BVA: lower boundary — always fires)."""

        async def handler() -> dict[str, object]:
            return {}

        reg = _make_reg(handler, timeout=0.0)
        assert reg.timeout == 0.0

    def test_timeout_negative_stored_on_registration(self) -> None:
        """timeout=-1.0 stored as-is; asyncio.timeout() raises ValueError at runtime."""

        async def handler() -> dict[str, object]:
            return {}

        reg = _make_reg(handler, timeout=-1.0)
        assert reg.timeout == -1.0


# ---------------------------------------------------------------------------
# Timeout enforcement tests
# ---------------------------------------------------------------------------


class TestCommandTimeoutEnforcement:
    """Test that timeout is enforced and TimeoutError is published."""

    @pytest.mark.asyncio
    async def test_timeout_fires_and_publishes_error(self) -> None:
        """Handler exceeding timeout raises TimeoutError, published to error topic."""
        completed = False

        async def slow_handler() -> dict[str, object]:
            nonlocal completed
            await asyncio.sleep(10)  # Will be cancelled by wait_for
            completed = True
            return {}

        reg = _make_reg(slow_handler, timeout=0.01)
        ctx = _make_ctx()
        mock_mqtt = MockMqttClient()
        error_publisher = ErrorPublisher(mqtt=mock_mqtt, topic_prefix="testapp")
        runner = CommandRunner(store=None)

        # Timeout should fire and publish error
        await runner.run_command(
            reg=reg,
            ctx=ctx,
            topic="testapp/sensor/set",
            payload="",
            error_publisher=error_publisher,
        )

        # Handler should not complete
        assert not completed

        # Error should be published
        assert len(mock_mqtt.published) > 0
        error_pub = [m for m in mock_mqtt.published if "error" in m[0]]
        assert len(error_pub) > 0

    @pytest.mark.asyncio
    async def test_timeout_none_disables_backstop(self) -> None:
        """timeout=None allows handler to complete normally."""
        completed = False

        async def handler() -> dict[str, object]:
            nonlocal completed
            await asyncio.sleep(0.001)
            completed = True
            return {"status": "ok"}

        reg = _make_reg(handler, timeout=None)
        ctx = _make_ctx()

        await _run_cmd(reg, ctx)

        # Handler should complete successfully
        assert completed

    @pytest.mark.asyncio
    async def test_short_handler_completes_within_timeout(self) -> None:
        """Handler completing within timeout succeeds normally."""
        completed = False

        async def fast_handler() -> dict[str, object]:
            nonlocal completed
            completed = True
            return {"status": "ok"}

        reg = _make_reg(fast_handler, timeout=1.0)
        ctx = _make_ctx()

        await _run_cmd(reg, ctx)

        # Handler should complete successfully
        assert completed


# ---------------------------------------------------------------------------
# Composition with unavailable_on
# ---------------------------------------------------------------------------


class TestCommandTimeoutWithUnavailableOn:
    """Test timeout composition with unavailable_on."""

    @pytest.mark.asyncio
    async def test_timeout_with_unavailable_on_marks_offline(self) -> None:
        """Timeout + unavailable_on=(TimeoutError,) marks device offline."""
        completed = False

        async def slow_handler() -> dict[str, object]:
            nonlocal completed
            await asyncio.sleep(10)  # Will timeout
            completed = True
            return {}

        health_reporter = AsyncMock(spec=HealthReporter)
        reg = _make_reg(
            slow_handler,
            timeout=0.01,
            unavailable_on=(TimeoutError,),
        )
        ctx = _make_ctx(health_reporter=health_reporter)

        await _run_cmd(reg, ctx)

        # Handler should not complete
        assert not completed

        # Device should be marked unavailable
        health_reporter.publish_device_unavailable.assert_called_once_with(
            "sensor", is_root=False
        )

    @pytest.mark.asyncio
    async def test_timeout_with_unavailable_on_oserror(self) -> None:
        """Timeout + unavailable_on=(OSError,) marks offline (catches TimeoutError)."""
        completed = False

        async def slow_handler() -> dict[str, object]:
            nonlocal completed
            await asyncio.sleep(10)  # Will timeout
            completed = True
            return {}

        health_reporter = AsyncMock(spec=HealthReporter)
        reg = _make_reg(
            slow_handler,
            timeout=0.01,
            unavailable_on=(OSError,),  # TimeoutError is subclass of OSError
        )
        ctx = _make_ctx(health_reporter=health_reporter)

        await _run_cmd(reg, ctx)

        # Handler should not complete
        assert not completed

        # Device should be marked unavailable (TimeoutError ⊆ OSError)
        health_reporter.publish_device_unavailable.assert_called_once_with(
            "sensor", is_root=False
        )

    @pytest.mark.asyncio
    async def test_timeout_without_unavailable_on_publishes_error(self) -> None:
        """Timeout without unavailable_on publishes error but doesn't mark offline."""
        completed = False

        async def slow_handler() -> dict[str, object]:
            nonlocal completed
            await asyncio.sleep(10)  # Will timeout
            completed = True
            return {}

        health_reporter = AsyncMock(spec=HealthReporter)
        reg = _make_reg(slow_handler, timeout=0.01)
        ctx = _make_ctx(health_reporter=health_reporter)
        mock_mqtt = MockMqttClient()
        error_publisher = ErrorPublisher(mqtt=mock_mqtt, topic_prefix="testapp")
        runner = CommandRunner(store=None)

        await runner.run_command(
            reg=reg,
            ctx=ctx,
            topic="testapp/sensor/set",
            payload="",
            error_publisher=error_publisher,
        )

        # Handler should not complete
        assert not completed

        # Error should be published
        error_pub = [m for m in mock_mqtt.published if "error" in m[0]]
        assert len(error_pub) > 0

        # Device should NOT be marked unavailable
        health_reporter.publish_device_unavailable.assert_not_called()


# ---------------------------------------------------------------------------
# Boundary behavior tests
# ---------------------------------------------------------------------------


class TestCommandTimeoutBoundaryBehavior:
    """Boundary-value behavior for timeout=0 and negative timeout."""

    @pytest.mark.asyncio
    async def test_timeout_zero_always_fires(self) -> None:
        """timeout=0 fires immediately — asyncio.timeout(0) never yields to handler.

        Technique: Boundary Value Analysis — lower boundary of valid timeout range.
        Error Guessing — timeout=0 as silent footgun.
        """

        async def handler() -> dict[str, object]:
            await asyncio.sleep(0)  # checkpoint so asyncio.timeout(0) fires
            return {"state": "done"}

        ctx = _make_ctx()
        reg = _make_reg(handler, timeout=0.0)
        runner = CommandRunner(store=None)
        mock_mqtt = MockMqttClient()
        error_publisher = ErrorPublisher(mqtt=mock_mqtt, topic_prefix="testapp")

        await runner.run_command(
            reg=reg,
            ctx=ctx,
            topic="testapp/sensor/set",
            payload="",
            error_publisher=error_publisher,
        )

        # timeout=0 should cause TimeoutError to be published
        published = mock_mqtt.published
        assert any("error" in t for t, *_ in published), (
            "Expected error published for timeout=0, got: " + str(published)
        )
