"""Tests for cosalette App — heartbeat publishing and MQTT protocol conformance.

Covers: initial and periodic heartbeat publishing, heartbeat_interval=None
disablement, and MqttLifecycle/MqttMessageHandler/MqttPort protocol
conformance for real, mock, and null MQTT client implementations.

Test Techniques Used:
    - Specification-based Testing: Heartbeat interval, topic, and payload
      format contracts (ADR-012).
    - Protocol Conformance: MqttLifecycle, MqttMessageHandler, and MqttPort
      structural protocol assertions.
    - Boundary Value Analysis: heartbeat_interval=None disables publishing;
      interval=0 edge case.
    - State-based Testing: Heartbeat timer interaction with FakeClock ticks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._mqtt import MqttClient, MqttPort
from cosalette._settings import MqttSettings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestRunAsyncHeartbeat — heartbeat publishing tests
# ---------------------------------------------------------------------------


class TestRunAsyncHeartbeat:
    """Heartbeat publishing integration tests."""

    async def test_heartbeat_published_on_startup(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """An initial heartbeat is published immediately on startup.

        Before the periodic loop starts, ``_run_async`` publishes a
        structured JSON heartbeat to ``{prefix}/status`` so the LWT
        ``"offline"`` string is overwritten right away.

        Technique: Integration Testing — verify status topic contains
        a JSON heartbeat after startup.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=60.0)
        device_done = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # First message should be the JSON heartbeat (before shutdown offline)
        assert len(status) >= 1
        first_payload = status[0][0]
        parsed = json.loads(first_payload)
        assert parsed["status"] == "online"
        assert parsed["version"] == "1.0.0"

    async def test_heartbeat_omits_version_when_opted_out(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """heartbeat_include_version=False removes the field end-to-end.

        Technique: Integration Testing — F-DP6 mitigation must hold
        through the full wiring chain (App → create_services →
        HealthReporter → HeartbeatPayload).
        """
        app = App(
            name="testapp",
            version="1.0.0",
            heartbeat_interval=60.0,
            heartbeat_include_version=False,
        )
        device_done = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        assert len(status) >= 1
        parsed = json.loads(status[0][0])
        assert parsed["status"] == "online"
        assert "version" not in parsed

    async def test_periodic_heartbeat_publishes_multiple_times(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Periodic heartbeat loop publishes at the configured interval.

        Uses a very short interval to verify multiple heartbeats arrive
        within the test timeout.

        Technique: Temporal Testing — short interval triggers multiple
        publications in a controlled window.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=0.02)

        shutdown = asyncio.Event()

        async def wait_for_heartbeats() -> None:
            # Wait long enough for 2+ periodic heartbeats (+ initial)
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(wait_for_heartbeats())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # Filter to only JSON heartbeat payloads (not "offline" strings)
        json_heartbeats = [p for p, _, _ in status if p.startswith("{")]
        # Initial + at least 2 periodic = 3+
        assert len(json_heartbeats) >= 3

    async def test_heartbeat_disabled_with_none_interval(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Setting ``heartbeat_interval=None`` disables periodic heartbeats.

        An initial heartbeat is still published (to overwrite LWT),
        but no periodic loop runs.

        Technique: Negative Testing — verify no extra heartbeats
        after a delay that would produce them with a non-None interval.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=None)

        shutdown = asyncio.Event()

        async def delayed_shutdown() -> None:
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(delayed_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # Only the initial heartbeat + shutdown "offline" — no periodic ones
        json_heartbeats = [p for p, _, _ in status if p.startswith("{")]
        assert len(json_heartbeats) == 1


# ---------------------------------------------------------------------------
# TestMqttProtocolConformance — MqttLifecycle + MqttMessageHandler
# ---------------------------------------------------------------------------


class TestMqttProtocolConformance:
    """Protocol conformance tests for MqttLifecycle and MqttMessageHandler.

    Technique: Protocol Conformance — isinstance checks using
    ``runtime_checkable`` to verify structural subtyping contracts
    introduced for Interface Segregation (ADR-006, PEP 544).
    """

    def test_mqtt_client_satisfies_lifecycle(
        self,
    ) -> None:
        """MqttClient implements start()/stop() — satisfies MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttLifecycle)

    def test_mqtt_client_satisfies_message_handler(self) -> None:
        """MqttClient implements on_message() — satisfies MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttMessageHandler)

    def test_mock_mqtt_client_satisfies_message_handler(self) -> None:
        """MockMqttClient implements on_message() — satisfies MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler

        assert isinstance(MockMqttClient(), MqttMessageHandler)

    def test_mock_mqtt_client_does_not_satisfy_lifecycle(self) -> None:
        """MockMqttClient lacks start()/stop() — not MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle

        assert not isinstance(MockMqttClient(), MqttLifecycle)

    def test_null_mqtt_client_does_not_satisfy_lifecycle(self) -> None:
        """NullMqttClient lacks start()/stop() — not MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle, NullMqttClient

        assert not isinstance(NullMqttClient(), MqttLifecycle)

    def test_null_mqtt_client_does_not_satisfy_message_handler(self) -> None:
        """NullMqttClient lacks on_message() — not MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler, NullMqttClient

        assert not isinstance(NullMqttClient(), MqttMessageHandler)

    def test_all_three_satisfy_mqtt_port(self) -> None:
        """MqttClient, MockMqttClient, NullMqttClient all satisfy MqttPort."""
        from cosalette._mqtt import NullMqttClient

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttPort)
        assert isinstance(MockMqttClient(), MqttPort)
        assert isinstance(NullMqttClient(), MqttPort)
