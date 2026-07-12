"""Unit tests for register_connect_reannounce wiring helper.

Tests the core F-1/F-2 behavior:
  - Nothing is published before simulate_connect().
  - First connect: availability 'online' for all devices + registry + heartbeat.
  - Reconnect: reannounce() for tracked devices + registry + heartbeat.
  - MockMqttClient (non-connect-aware) path still triggers eager publishes.

Test Techniques Used:
    - State-based Testing: compare publish records before/after connect.
    - Protocol Conformance: FakeConnectAwareMqttClient satisfies MqttConnectAware.
    - Specification-based Testing: topic, payload, and retain assertions.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._mqtt import MqttConnectAware
from cosalette._wiring import publish_device_availability, register_connect_reannounce
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.fixtures.mqtt import FakeConnectAwareMqttClient

pytestmark = pytest.mark.unit

PREFIX = "testapp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reporter(mqtt: FakeConnectAwareMqttClient) -> HealthReporter:
    from typing import cast

    from cosalette._mqtt import MqttPort

    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=cast(MqttPort, mqtt),
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


# ---------------------------------------------------------------------------
# FakeConnectAwareMqttClient protocol conformance
# ---------------------------------------------------------------------------


class TestFakeConnectAwareMqttClientProtocol:
    """FakeConnectAwareMqttClient satisfies MqttConnectAware."""

    def test_isinstance_mqtt_connect_aware(self) -> None:
        """FakeConnectAwareMqttClient is an MqttConnectAware."""
        fake = FakeConnectAwareMqttClient()
        assert isinstance(fake, MqttConnectAware)

    def test_not_a_mock_mqtt_client(self) -> None:
        """FakeConnectAwareMqttClient is not a MockMqttClient."""
        fake = FakeConnectAwareMqttClient()
        assert not isinstance(fake, MockMqttClient)


# ---------------------------------------------------------------------------
# register_connect_reannounce — core behavior
# ---------------------------------------------------------------------------


class TestRegisterConnectReannounce:
    """Core F-1/F-2 unit tests for register_connect_reannounce."""

    async def test_nothing_published_before_connect(self) -> None:
        """No MQTT publishes occur before simulate_connect() is called."""
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, app._store
        )

        assert fake.publish_count == 0

    async def test_first_connect_publishes_availability_registry_heartbeat(
        self,
    ) -> None:
        """First connect: availability 'online', registry, and heartbeat published."""
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, app._store
        )

        await fake.simulate_connect()

        topics = [t for t, _, _, _ in fake.published]

        # availability for 'sensor' device
        assert f"{PREFIX}/sensor/availability" in topics

        # registry snapshot
        assert f"{PREFIX}/_meta/registry" in topics

        # heartbeat
        assert f"{PREFIX}/status" in topics

        # availability is 'online'
        avail_msgs = fake.get_messages_for(f"{PREFIX}/sensor/availability")
        assert avail_msgs[0][0] == "online"

    async def test_second_connect_uses_reannounce_not_full_publish(self) -> None:
        """Reconnect re-publishes only tracked-online devices, not all registrations.

        Registers two devices; makes one unavailable before reconnect.
        If full publish_device_availability were called on reconnect, the
        offline device would be re-published 'online' — proving reannounce() is used.
        """
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        @app.device("camera")
        async def _camera(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, app._store
        )

        # First connect — both devices go online
        await fake.simulate_connect()

        # Camera goes offline (removed from tracking)
        await reporter.publish_device_unavailable("camera", is_root=False)
        fake.reset()

        # Reconnect
        await fake.simulate_connect()

        topics = [t for t, _, _, _ in fake.published]

        # sensor (still tracked) must be re-asserted online
        assert f"{PREFIX}/sensor/availability" in topics
        sensor_msgs = fake.get_messages_for(f"{PREFIX}/sensor/availability")
        assert sensor_msgs[0][0] == "online"

        # camera (made unavailable) must NOT be re-published online on reconnect.
        # If publish_device_availability(all_registrations) were called instead of
        # reannounce(), this would fail — that's the regression this test guards.
        camera_msgs = fake.get_messages_for(f"{PREFIX}/camera/availability")
        assert all(payload != "online" for payload, _, _ in camera_msgs), (
            "Offline device must not be ghost-revived on reconnect via reannounce()"
        )

        # registry and heartbeat still re-published
        assert f"{PREFIX}/_meta/registry" in topics
        assert f"{PREFIX}/status" in topics

    async def test_reconnect_does_not_re_publish_removed_device(self) -> None:
        """A device removed (made unavailable) before reconnect is not re-onlined."""
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, app._store
        )

        # First connect — sensor goes online
        await fake.simulate_connect()

        # Device goes offline (removed from tracking)
        await reporter.publish_device_unavailable("sensor", is_root=False)
        fake.reset()

        # Reconnect — sensor should NOT be re-onlined
        await fake.simulate_connect()

        avail_msgs = fake.get_messages_for(f"{PREFIX}/sensor/availability")
        assert all(payload != "online" for payload, _, _ in avail_msgs)

    async def test_first_connect_publishes_online_even_if_device_went_offline_before_connect(  # noqa: E501
        self,
    ) -> None:
        """First connect: optimistic full announce publishes 'online' for all
        registrations.

        Even if a device was explicitly made unavailable after registration but
        before the first MQTT connection, the first-connect callback publishes
        'online' via publish_device_availability() (which uses all_registrations,
        not the tracked-devices map). This is the known first-connect asymmetry —
        subsequent reconnects use HealthReporter.reannounce() which only re-asserts
        currently-tracked devices.

        Technique: State Transition Testing (offline-before-first-connect edge case).
        """
        fake = FakeConnectAwareMqttClient()
        reporter = _make_reporter(fake)
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        register_connect_reannounce(
            fake, app, reporter, app._all_registrations, PREFIX, app._store
        )

        # Device goes unavailable BEFORE the first MQTT connect fires
        await reporter.publish_device_unavailable("sensor", is_root=False)
        fake.reset()

        # First connect — optimistic full announce regardless of tracking state
        await fake.simulate_connect()

        sensor_msgs = fake.get_messages_for(f"{PREFIX}/sensor/availability")
        assert any(payload == "online" for payload, _, _ in sensor_msgs), (
            "First connect must publish 'online' for all registrations "
            "(optimistic announce — known asymmetry with reconnect path)"
        )


# ---------------------------------------------------------------------------
# Non-connect-aware path preserved
# ---------------------------------------------------------------------------


class TestNonConnectAwarePath:
    """MockMqttClient (not connect-aware) triggers eager startup publishes."""

    async def test_mock_mqtt_client_not_connect_aware(self) -> None:
        """MockMqttClient does not satisfy MqttConnectAware."""
        mock = MockMqttClient()
        assert not isinstance(mock, MqttConnectAware)

    async def test_eager_publish_device_availability_with_mock(self) -> None:
        """publish_device_availability still works with MockMqttClient."""
        mock = MockMqttClient()
        clock = FakeClock()
        reporter = HealthReporter(
            mqtt=mock,  # type: ignore[arg-type]
            topic_prefix=PREFIX,
            version="1.0.0",
            clock=clock,
        )
        app = App(name=PREFIX, version="1.0.0")

        @app.device("sensor")
        async def _sensor(ctx: DeviceContext) -> None:  # pragma: no cover
            pass

        # Simulate what _lifecycle.py does for non-connect-aware clients
        await publish_device_availability(app._all_registrations, reporter)

        avail_msgs = mock.get_messages_for(f"{PREFIX}/sensor/availability")
        assert len(avail_msgs) >= 1
        assert avail_msgs[0][0] == "online"

    async def test_full_app_run_with_mock_still_publishes_heartbeat(self) -> None:
        """Full _run_async with MockMqttClient publishes an initial heartbeat."""
        mock = MockMqttClient()
        clock = FakeClock()
        app = App(name=PREFIX, version="1.0.0", heartbeat_interval=60.0)
        shutdown = asyncio.Event()

        @app.device("trigger")
        async def _trigger(ctx: DeviceContext) -> AsyncIterator[None]:
            shutdown.set()
            yield

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock,
                clock=clock,
            ),
            timeout=5.0,
        )

        # initial heartbeat must have been published
        status = mock.get_messages_for(f"{PREFIX}/status")
        assert len(status) >= 1
        parsed = json.loads(status[0][0])
        assert parsed["status"] == "online"
