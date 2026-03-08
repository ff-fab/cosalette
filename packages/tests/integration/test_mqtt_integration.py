"""MQTT integration tests — real MqttClient against Mosquitto testcontainer.

Tests the production MqttClient adapter against an ephemeral Mosquitto
broker, validating network-level behavior that mock-based unit tests
cannot cover.

Test Techniques Used:
    - State Transition Testing: connect/disconnect lifecycle
    - Round-trip Testing: pub/sub message fidelity through real broker
    - Error Guessing: idempotent stop, retained delivery to late subscriber

See Also:
    ADR-002 — MQTT topic conventions
    ADR-006 — Hexagonal architecture (adapter testing)
    ADR-012 — LWT / availability
"""

from __future__ import annotations

import asyncio

import pytest

from cosalette._mqtt_client import MqttClient
from cosalette._settings import MqttSettings

pytestmark = [pytest.mark.integration, pytest.mark.mqtt]


# ---------------------------------------------------------------------------
# Connect / Disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Verify basic lifecycle of MqttClient against a real broker."""

    async def test_client_connects_to_broker(
        self,
        mqtt_client: MqttClient,
    ) -> None:
        """Connect — client reports connected after start().

        Technique: fixture starts client, assert is_connected property.
        """
        assert mqtt_client.is_connected

    async def test_client_disconnects_cleanly(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Disconnect — client reports not connected after stop().

        Technique: manual start/stop lifecycle, assert state transitions.
        """
        client = MqttClient(settings=mqtt_settings)
        await client.start()

        # Wait for connection
        for _ in range(50):
            if client.is_connected:
                break
            await asyncio.sleep(0.1)
        assert client.is_connected, "Failed to connect"

        await client.stop()
        assert not client.is_connected

    async def test_stop_is_idempotent(
        self,
        mqtt_client: MqttClient,
    ) -> None:
        """Stop — calling stop() multiple times does not raise.

        Technique: call stop() twice, verify no exception.
        """
        await mqtt_client.stop()
        await mqtt_client.stop()  # second call should not raise
        assert not mqtt_client.is_connected


# ---------------------------------------------------------------------------
# Publish / Subscribe Round-Trip
# ---------------------------------------------------------------------------


class TestPubSubRoundTrip:
    """Verify publish/subscribe message flow through a real broker."""

    async def test_publish_subscribe_round_trip(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Round-trip — published message arrives at subscriber callback.

        Technique: two-client pattern. Client A subscribes and registers
        a callback. Client B publishes. Assert callback receives the
        exact topic and payload.
        """
        received: list[tuple[str, str]] = []
        event = asyncio.Event()

        async def on_msg(topic: str, payload: str) -> None:
            received.append((topic, payload))
            event.set()

        # Subscriber client
        sub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-sub"},
        )
        subscriber = MqttClient(settings=sub_settings)
        subscriber.on_message(on_msg)

        # Publisher client
        pub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-pub"},
        )
        publisher = MqttClient(settings=pub_settings)

        try:
            await subscriber.start()
            await publisher.start()

            await _wait_connected(subscriber, publisher)

            # Subscribe and give broker time to process
            await subscriber.subscribe("test/sensor/temperature")
            await asyncio.sleep(0.3)

            # Publish
            await publisher.publish(
                "test/sensor/temperature",
                '{"value": 22.5}',
            )

            # Wait for delivery
            await asyncio.wait_for(event.wait(), timeout=5.0)

            assert len(received) == 1
            assert received[0] == ("test/sensor/temperature", '{"value": 22.5}')
        finally:
            await publisher.stop()
            await subscriber.stop()

    async def test_publish_subscribe_multiple_topics(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Multi-topic — messages on different topics routed correctly.

        Technique: single subscriber with wildcard, publish to two
        distinct topics, verify both arrive.
        """
        received: list[tuple[str, str]] = []
        all_received = asyncio.Event()

        async def on_msg(topic: str, payload: str) -> None:
            received.append((topic, payload))
            if len(received) >= 2:
                all_received.set()

        sub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-sub"},
        )
        subscriber = MqttClient(settings=sub_settings)
        subscriber.on_message(on_msg)

        pub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-pub"},
        )
        publisher = MqttClient(settings=pub_settings)

        try:
            await subscriber.start()
            await publisher.start()

            await _wait_connected(subscriber, publisher)

            # Subscribe to wildcard
            await subscriber.subscribe("test/sensor/#")
            await asyncio.sleep(0.3)

            await publisher.publish("test/sensor/temperature", "22.5")
            await publisher.publish("test/sensor/humidity", "65.0")

            await asyncio.wait_for(all_received.wait(), timeout=5.0)

            topics = {t for t, _ in received}
            assert "test/sensor/temperature" in topics
            assert "test/sensor/humidity" in topics
        finally:
            await publisher.stop()
            await subscriber.stop()

    async def test_retained_message_delivered_to_new_subscriber(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Retained — new subscriber receives the last retained message.

        Technique: client A publishes with retain=True, then disconnects.
        Client B subscribes afterward and should receive the retained
        message.
        """
        received: list[tuple[str, str]] = []
        event = asyncio.Event()

        # Publish a retained message first
        pub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-pub"},
        )
        publisher = MqttClient(settings=pub_settings)

        try:
            await publisher.start()
            await _wait_connected(publisher)

            await publisher.publish(
                "test/device/status",
                "online",
                retain=True,
            )
            await asyncio.sleep(0.3)
        finally:
            await publisher.stop()

        # Now subscribe — should get the retained message
        async def on_msg(topic: str, payload: str) -> None:
            received.append((topic, payload))
            event.set()

        sub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-sub"},
        )
        subscriber = MqttClient(settings=sub_settings)
        subscriber.on_message(on_msg)

        try:
            await subscriber.start()
            await _wait_connected(subscriber)

            await subscriber.subscribe("test/device/status")

            await asyncio.wait_for(event.wait(), timeout=5.0)
            assert len(received) >= 1
            assert received[0] == ("test/device/status", "online")
        finally:
            await subscriber.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_connected(*clients: MqttClient, timeout: float = 5.0) -> None:
    """Wait until all clients report connected, or raise on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    for client in clients:
        remaining = deadline - loop.time()
        if remaining <= 0:
            msg = "Timed out waiting for client connections"
            raise TimeoutError(msg)
        for _ in range(int(remaining * 10)):
            if client.is_connected:
                break
            await asyncio.sleep(0.1)
        if not client.is_connected:
            msg = "Client failed to connect within timeout"
            raise TimeoutError(msg)
