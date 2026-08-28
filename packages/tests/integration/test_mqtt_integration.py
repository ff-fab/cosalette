"""MQTT integration tests — real MqttClient against Mosquitto testcontainer.

Tests the production MqttClient adapter against an ephemeral Mosquitto
broker, validating network-level behavior that mock-based unit tests
cannot cover.

Test Techniques Used:
    - State Transition Testing: connect/disconnect lifecycle
    - Round-trip Testing: pub/sub message fidelity through real broker
    - Error Guessing: idempotent stop, retained delivery to late subscriber
    - Integration Wiring: LWT config accepted by real broker
    - Disruption Recovery: reconnection after broker restart
    - Test Isolation: unique topic namespaces prevent cross-test interference

See Also:
    ADR-002 — MQTT topic conventions
    ADR-006 — Hexagonal architecture (adapter testing)
    ADR-012 — LWT / availability
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from pathlib import Path
from typing import override

import pytest
from testcontainers.mqtt import MosquittoContainer

from cosalette._health import build_will_config
from cosalette._mqtt import WillConfig
from cosalette._mqtt._client import MqttClient
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
        try:
            await client.start()

            # Wait for connection
            for _ in range(50):
                if client.is_connected:
                    break
                await asyncio.sleep(0.1)
            assert client.is_connected, "Failed to connect"
        finally:
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

        # Subscriber client with unique ID
        sub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-sub"},
        )
        subscriber = MqttClient(settings=sub_settings)
        subscriber.on_message(on_msg)

        # Publisher client with unique ID
        pub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-pub"},
        )
        publisher = MqttClient(settings=pub_settings)

        try:
            await subscriber.start()
            await publisher.start()

            await _wait_connected(subscriber, publisher)

            # Use test's unique topic namespace
            test_topic = f"{mqtt_settings.topic_prefix}/sensor/temperature"
            await subscriber.subscribe(test_topic)

            # Publish
            await publisher.publish(
                test_topic,
                '{"value": 22.5}',
            )

            # Wait for delivery
            await asyncio.wait_for(event.wait(), timeout=5.0)

            assert len(received) == 1
            assert received[0] == (test_topic, '{"value": 22.5}')
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

            # Subscribe to wildcard within test's namespace
            wildcard_topic = f"{mqtt_settings.topic_prefix}/sensor/#"
            await subscriber.subscribe(wildcard_topic)

            topic1 = f"{mqtt_settings.topic_prefix}/sensor/temperature"
            topic2 = f"{mqtt_settings.topic_prefix}/sensor/humidity"

            await publisher.publish(topic1, "22.5")
            await publisher.publish(topic2, "65.0")

            await asyncio.wait_for(all_received.wait(), timeout=5.0)

            topics = {t for t, _ in received}
            assert topic1 in topics
            assert topic2 in topics
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

        status_topic = f"{mqtt_settings.topic_prefix}/device/status"

        try:
            await publisher.start()
            await _wait_connected(publisher)

            await publisher.publish(
                status_topic,
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

            await subscriber.subscribe(status_topic)

            await asyncio.wait_for(event.wait(), timeout=5.0)
            assert len(received) >= 1
            assert received[0] == (status_topic, "online")
        finally:
            await subscriber.stop()


# ---------------------------------------------------------------------------
# LWT Wiring
# ---------------------------------------------------------------------------


class TestLwtWiring:
    """Verify WillConfig integration with real broker connections.

    Full LWT delivery testing (unclean disconnect → will published) is
    impractical in automated tests: aiomqtt uses a 60 s MQTT keep-alive,
    so the broker takes ~90 s to detect a dead connection.  These tests
    verify the will configuration wiring instead.
    """

    async def test_client_with_will_connects_and_publishes(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Will wiring — WillConfig does not prevent normal operation.

        Technique: connect with WillConfig, publish a message, verify
        it arrives.  Proves the will is accepted by the broker without
        breaking the connection.
        """
        received: list[tuple[str, str]] = []
        event = asyncio.Event()

        will_topic = f"{mqtt_settings.topic_prefix}/will-test/status"
        will = WillConfig(topic=will_topic)

        async def on_msg(topic: str, payload: str) -> None:
            received.append((topic, payload))
            event.set()

        pub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-will"},
        )
        publisher = MqttClient(settings=pub_settings, will=will)

        sub_settings = mqtt_settings.model_copy(
            update={"client_id": f"{mqtt_settings.client_id}-obs"},
        )
        subscriber = MqttClient(settings=sub_settings)
        subscriber.on_message(on_msg)

        try:
            await subscriber.start()
            await publisher.start()
            await _wait_connected(subscriber, publisher)

            data_topic = f"{mqtt_settings.topic_prefix}/data"
            await subscriber.subscribe(data_topic)

            await publisher.publish(data_topic, "hello")
            await asyncio.wait_for(event.wait(), timeout=5.0)

            assert received[0] == (data_topic, "hello")
        finally:
            await publisher.stop()
            await subscriber.stop()

    async def test_build_will_config_produces_valid_broker_will(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Framework will — build_will_config() produces a will accepted by broker.

        Technique: use build_will_config() (the production helper) to
        create a WillConfig, connect a client with it, verify connection
        succeeds.  Validates the ADR-012 topic convention is broker-compatible.
        """
        # Use isolated topic namespace for will topic
        will_base = mqtt_settings.topic_prefix.rstrip("/")
        will = build_will_config(will_base)
        client = MqttClient(settings=mqtt_settings, will=will)

        try:
            await client.start()
            await _wait_connected(client)
            assert client.is_connected
        finally:
            await client.stop()


# ---------------------------------------------------------------------------
# Reconnection After Broker Restart
# ---------------------------------------------------------------------------


class TestReconnection:
    """Verify MqttClient auto-reconnection after broker disruption.

    Uses ``_FixedPortMosquitto`` — a thin subclass that pins the host
    port via the ``_configure`` hook — so the port survives a Docker
    ``kill`` + ``start`` cycle.  Each test gets its own container to
    avoid interfering with the module-scoped shared broker.
    """

    async def test_client_reconnects_after_broker_restart(
        self,
        mosquitto_config_path: Path,
    ) -> None:
        """Reconnect — client auto-reconnects when broker returns.

        Technique: fixed-port container.  Connect client, kill the
        Docker container (drops all TCP connections), restart the same
        container, verify the client reconnects within backoff window.
        """
        port = _find_free_port()
        container = _FixedPortMosquitto(host_port=port)

        try:
            container.start(configfile=str(mosquitto_config_path))
            host = container.get_container_host_ip()

            # Use unique client ID with better collision avoidance
            test_id = uuid.uuid4().hex
            settings = MqttSettings(
                host=host,
                port=port,
                # The fixed-port test Mosquitto container has no TLS
                # listener — explicit opt-out (tls defaults True, ADR-062).
                tls=False,
                client_id=f"test-reconnect-{test_id}",
                reconnect_interval=0.3,
                reconnect_max_interval=1.0,
                topic_prefix=f"test/reconnect/{test_id[:8]}",
            )
            client = MqttClient(settings=settings)

            try:
                # Phase 1: connect
                await client.start()
                await _wait_connected(client)
                assert client.is_connected

                # Phase 2: kill the broker (ungraceful — drops TCP)
                docker_container = container.get_wrapped_container()
                docker_container.kill()

                # Wait for client to detect disconnection
                for _ in range(50):
                    if not client.is_connected:
                        break
                    await asyncio.sleep(0.1)
                assert not client.is_connected, "Client should detect broker gone"

                # Phase 3: restart the same container (port preserved)
                docker_container.start()
                await asyncio.sleep(1)  # let mosquitto initialise

                await _wait_connected(client, timeout=10.0)
                assert client.is_connected, (
                    "Client should reconnect to restarted broker"
                )
            finally:
                await client.stop()
        finally:
            with contextlib.suppress(Exception):
                container.stop()

    async def test_subscriptions_restored_after_reconnect(
        self,
        mosquitto_config_path: Path,
    ) -> None:
        """Restore — subscriptions are re-established after reconnect.

        Technique: subscribe before disconnect, kill broker, restart,
        publish on the restored broker, verify message received.  Proves
        the subscription tracking in MqttClient survives reconnection.
        """
        port = _find_free_port()
        container = _FixedPortMosquitto(host_port=port)

        try:
            container.start(configfile=str(mosquitto_config_path))
            host = container.get_container_host_ip()

            received: list[tuple[str, str]] = []
            msg_event = asyncio.Event()

            async def on_msg(topic: str, payload: str) -> None:
                received.append((topic, payload))
                msg_event.set()

            # Use unique test isolation
            test_id = uuid.uuid4().hex
            sub_settings = MqttSettings(
                host=host,
                port=port,
                # The fixed-port test Mosquitto container has no TLS
                # listener — explicit opt-out (tls defaults True, ADR-062).
                tls=False,
                client_id=f"test-resub-{test_id}",
                reconnect_interval=0.3,
                reconnect_max_interval=1.0,
                topic_prefix=f"test/resub/{test_id[:8]}",
            )
            subscriber = MqttClient(settings=sub_settings)
            subscriber.on_message(on_msg)

            try:
                # Phase 1: connect and subscribe
                await subscriber.start()
                await _wait_connected(subscriber)

                sensor_topic = f"{sub_settings.topic_prefix}/sensor/value"
                await subscriber.subscribe(sensor_topic)

                # Phase 2: kill broker
                docker_container = container.get_wrapped_container()
                docker_container.kill()

                for _ in range(50):
                    if not subscriber.is_connected:
                        break
                    await asyncio.sleep(0.1)
                assert not subscriber.is_connected, (
                    "Subscriber should detect broker kill"
                )

                # Phase 3: restart, verify subscription restored via message
                docker_container.start()
                await asyncio.sleep(1)

                await _wait_connected(subscriber, timeout=10.0)
                await asyncio.sleep(0.5)  # let subscription restoration settle

                pub_settings = MqttSettings(
                    host=host,
                    port=port,
                    # No TLS listener on the test broker — see sub_settings.
                    tls=False,
                    client_id=f"test-resub-pub-{test_id}",
                    reconnect_interval=0.3,
                    reconnect_max_interval=1.0,
                    topic_prefix=f"test/resub/{test_id[:8]}",
                )
                publisher = MqttClient(settings=pub_settings)
                await publisher.start()
                await _wait_connected(publisher)

                await publisher.publish(sensor_topic, "42.0")

                try:
                    await asyncio.wait_for(msg_event.wait(), timeout=5.0)
                    assert len(received) >= 1
                    assert received[-1] == (sensor_topic, "42.0")
                finally:
                    await publisher.stop()
            finally:
                await subscriber.stop()
        finally:
            with contextlib.suppress(Exception):
                container.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FixedPortMosquitto(MosquittoContainer):
    """MosquittoContainer with a pinned host port.

    The default ``MosquittoContainer.start()`` calls
    ``with_exposed_ports()`` which sets the host port to *random*.
    This subclass uses the ``_configure`` hook (called just before
    ``docker_client.run()``) to override with a fixed port so that
    the same port survives a Docker kill + start cycle.

    Includes better error handling for port binding failures.
    """

    def __init__(self, host_port: int) -> None:
        super().__init__()
        self._host_port = host_port

    @override
    def _configure(self) -> None:
        try:
            super()._configure()
            self.ports[self.MQTT_PORT] = self._host_port
        except Exception as e:
            raise RuntimeError(
                f"Failed to configure Mosquitto container with port {self._host_port}. "
                f"Port may be in use or Docker may be unavailable: {e}"
            ) from e


def _find_free_port() -> int:
    """Find a free TCP port on localhost for fixed container bindings.

    Raises:
        OSError: If no free ports are available.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    except OSError as e:
        raise OSError(
            f"Failed to find free port for test container: {e}. "
            "Check system socket availability."
        ) from e


async def _wait_connected(*clients: MqttClient, timeout: float = 5.0) -> None:
    """Wait until all clients report connected, or raise descriptive timeout.

    Args:
        *clients: MqttClient instances to wait for
        timeout: Maximum wait time in seconds

    Raises:
        TimeoutError: If any client fails to connect within timeout
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    for i, client in enumerate(clients, 1):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out waiting for client {i}/{len(clients)} connections "
                f"after {timeout}s. Check broker availability and network connectivity."
            )

        start_time = loop.time()
        while loop.time() - start_time < remaining:
            if client.is_connected:
                break
            await asyncio.sleep(0.1)
        else:
            elapsed = loop.time() - (deadline - timeout)
            raise TimeoutError(
                f"Client {i}/{len(clients)} failed to connect within {elapsed:.1f}s. "
                f"Check MQTT broker status and client configuration."
            )
