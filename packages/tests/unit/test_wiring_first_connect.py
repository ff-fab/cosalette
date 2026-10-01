"""Unit tests for the bounded first-connect startup barrier.

``MqttClient.start()`` only schedules the connection, so entity tasks used to
start before CONNACK and their first publishes failed.  The barrier holds
device, telemetry, periodic and stream startup until the first connect
callback pass has run, bounded by ``App(startup_connect_timeout=...)``
(ADR-016 execution order).

Test Techniques Used:
    - Decision Table Testing: await_first_connect skip conditions
    - State Transition Testing: waiting -> connected / timed out / shut down
    - Boundary Value Analysis: startup_connect_timeout validation
    - Mock-based Isolation: connect-aware MQTT fakes, ManualClock, FakeClock
"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import pytest

from cosalette._app import App
from cosalette._mqtt import MqttPort
from cosalette._wiring import await_first_connect, register_first_connect_gate
from cosalette.testing import FakeClock, ManualClock, MockMqttClient, make_settings
from tests.fixtures.mqtt import (
    FakeConnectAwareMqttClient,
    FakeLifecycleConnectAwareMqttClient,
)

pytestmark = pytest.mark.unit

PREFIX = "testapp"
WIRING_LOGGER = "cosalette._wiring"


# ---------------------------------------------------------------------------
# register_first_connect_gate
# ---------------------------------------------------------------------------


class TestRegisterFirstConnectGate:
    """The gate exists only for connect-aware adapters.

    Technique: State Transition Testing — closed until the first connect.
    """

    def test_returns_none_for_non_connect_aware_adapter(self) -> None:
        """MockMqttClient is always 'connected', so no barrier applies."""
        assert register_first_connect_gate(MockMqttClient()) is None

    async def test_gate_opens_only_after_connect(self) -> None:
        """The event is clear until the connect callbacks have run."""
        # Arrange
        mqtt = FakeConnectAwareMqttClient()
        gate = register_first_connect_gate(cast(MqttPort, mqtt))
        assert gate is not None
        assert not gate.is_set()

        # Act
        await mqtt.simulate_connect()

        # Assert
        assert gate.is_set()

    async def test_gate_opens_after_earlier_callbacks(self) -> None:
        """Callbacks run in registration order, so the announce comes first."""
        # Arrange
        mqtt = FakeConnectAwareMqttClient()
        order: list[str] = []

        async def _announce() -> None:
            order.append("announce")

        mqtt.add_connect_callback(_announce)
        gate = register_first_connect_gate(cast(MqttPort, mqtt))
        assert gate is not None

        # Act
        await mqtt.simulate_connect()
        order.append("gate" if gate.is_set() else "closed")

        # Assert
        assert order == ["announce", "gate"]


# ---------------------------------------------------------------------------
# await_first_connect
# ---------------------------------------------------------------------------


class TestAwaitFirstConnect:
    """Decision table and transitions of the bounded wait.

    Technique: Decision Table Testing for the skip conditions, State
    Transition Testing for the three ways a wait ends.
    """

    @pytest.mark.parametrize(
        ("gate", "timeout"),
        [
            (None, 10.0),  # adapter not connect-aware
            (asyncio.Event(), None),  # barrier disabled
        ],
        ids=["no-gate", "disabled"],
    )
    async def test_skips_without_waiting(
        self, gate: asyncio.Event | None, timeout: float | None
    ) -> None:
        """Returns at once; a ManualClock sleep would otherwise block."""
        await asyncio.wait_for(
            await_first_connect(gate, timeout, asyncio.Event(), ManualClock()),
            timeout=1.0,
        )

    async def test_returns_at_once_when_already_connected(self) -> None:
        """A gate opened before the run phase means no wait."""
        gate = asyncio.Event()
        gate.set()

        await asyncio.wait_for(
            await_first_connect(gate, 10.0, asyncio.Event(), ManualClock()),
            timeout=1.0,
        )

    async def test_waits_until_connect(self, caplog: pytest.LogCaptureFixture) -> None:
        """Pending while disconnected, done on connect, and no warning."""
        # Arrange
        gate = asyncio.Event()
        clock = ManualClock()
        caplog.set_level(logging.WARNING, logger=WIRING_LOGGER)
        task = asyncio.create_task(
            await_first_connect(gate, 10.0, asyncio.Event(), clock)
        )
        await clock.settle()
        assert not task.done()

        # Act
        gate.set()
        await asyncio.wait_for(task, timeout=1.0)

        # Assert
        assert caplog.records == []

    async def test_timeout_logs_one_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """After the timeout the wait ends with a single WARNING."""
        caplog.set_level(logging.WARNING, logger=WIRING_LOGGER)

        await asyncio.wait_for(
            await_first_connect(asyncio.Event(), 10.0, asyncio.Event(), FakeClock()),
            timeout=1.0,
        )

        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "not connected after 10.0s" in caplog.records[0].getMessage()

    async def test_shutdown_ends_wait_without_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Shutdown during the wait is not a connection problem."""
        # Arrange
        shutdown = asyncio.Event()
        clock = ManualClock()
        caplog.set_level(logging.WARNING, logger=WIRING_LOGGER)
        task = asyncio.create_task(
            await_first_connect(asyncio.Event(), 10.0, shutdown, clock)
        )
        await clock.settle()

        # Act
        shutdown.set()
        await asyncio.wait_for(task, timeout=1.0)

        # Assert
        assert caplog.records == []


# ---------------------------------------------------------------------------
# App(startup_connect_timeout=...)
# ---------------------------------------------------------------------------


class TestStartupConnectTimeoutValidation:
    """Technique: Boundary Value Analysis around zero; None disables."""

    @pytest.mark.parametrize("value", [0, -1.0])
    def test_rejects_non_positive(self, value: float) -> None:
        with pytest.raises(ValueError, match="startup_connect_timeout"):
            App(name=PREFIX, version="1.0.0", startup_connect_timeout=value)

    @pytest.mark.parametrize("value", [None, 0.001, 10.0])
    def test_accepts_none_and_positive(self, value: float | None) -> None:
        app = App(name=PREFIX, version="1.0.0", startup_connect_timeout=value)
        assert app._startup_connect_timeout == value


class TestAppStartupBarrier:
    """Full run phase over a connect-aware adapter.

    Technique: State Transition Testing — no entity publish before connect,
    availability announce before the first state publish after it.
    """

    async def test_telemetry_waits_for_first_connect(self) -> None:
        # Arrange
        mqtt = FakeLifecycleConnectAwareMqttClient()
        clock = ManualClock()
        shutdown = asyncio.Event()
        app = App(name=PREFIX, version="1.0.0", store=None)
        reads: list[None] = []

        @app.telemetry("sensor", interval=60)
        async def _sensor() -> dict[str, int]:
            reads.append(None)
            return {"value": 1}

        run = asyncio.create_task(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=cast(MqttPort, mqtt),
                clock=clock,
            )
        )
        try:
            await clock.settle()
            assert reads == []
            assert mqtt.get_messages_for(f"{PREFIX}/sensor/state") == []

            # Act
            await mqtt.simulate_connect()
            for _ in range(100):
                if mqtt.get_messages_for(f"{PREFIX}/sensor/state"):
                    break
                await clock.settle()

            # Assert
            topics = [topic for topic, *_ in mqtt.published]
            assert f"{PREFIX}/sensor/state" in topics
            assert topics.index(f"{PREFIX}/sensor/availability") < topics.index(
                f"{PREFIX}/sensor/state"
            )
        finally:
            shutdown.set()
            await asyncio.wait_for(run, timeout=5.0)

    async def test_telemetry_starts_after_timeout_without_connect(self) -> None:
        """The barrier is bounded: handlers run once the timeout elapses."""
        # Arrange
        mqtt = FakeLifecycleConnectAwareMqttClient()
        clock = ManualClock()
        shutdown = asyncio.Event()
        app = App(name=PREFIX, version="1.0.0", store=None, startup_connect_timeout=5.0)
        reads: list[None] = []

        @app.telemetry("sensor", interval=60)
        async def _sensor() -> dict[str, int]:
            reads.append(None)
            return {"value": 1}

        run = asyncio.create_task(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=cast(MqttPort, mqtt),
                clock=clock,
            )
        )
        try:
            await clock.settle()
            assert reads == []

            # Act
            await clock.advance(5.0)
            await clock.settle()

            # Assert
            assert reads == [None]
        finally:
            shutdown.set()
            await asyncio.wait_for(run, timeout=5.0)
