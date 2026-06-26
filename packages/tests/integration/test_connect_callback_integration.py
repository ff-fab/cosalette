"""Integration tests for MqttClient connect-callback mechanism.

Tests that connect callbacks fire on initial connection and on reconnect,
using a real Mosquitto broker via testcontainers.

Test Techniques Used:
    - Integration Wiring: real MqttClient against Mosquitto testcontainer.
    - State Transition Testing: callback fires on connect and reconnect.
    - Round-trip Testing: retained availability visible to late subscriber.

See Also:
    ADR-012 — Health and availability reporting.
    ADR-016 — Adapter lifecycle protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path

import pytest
from testcontainers.mqtt import MosquittoContainer

from cosalette._mqtt._client import MqttClient
from cosalette._settings import MqttSettings

pytestmark = [pytest.mark.integration, pytest.mark.mqtt]


# ---------------------------------------------------------------------------
# Connect-callback integration tests
# ---------------------------------------------------------------------------


class TestConnectCallbackIntegration:
    """Verify that connect callbacks fire on initial connect and on reconnect."""

    async def test_connect_callback_fires_on_initial_connect(
        self,
        mosquitto_config_path: Path,
    ) -> None:
        """Callback fires exactly once on the first broker connection.

        Technique: register a callback that appends to a list; start the
        client; assert the list has one entry after the initial connect.
        """
        port = _find_free_port()
        container = _FixedPortMosquitto(host_port=port)

        try:
            container.start(configfile=str(mosquitto_config_path))
            host = container.get_container_host_ip()

            test_id = uuid.uuid4().hex
            settings = MqttSettings(
                host=host,
                port=port,
                client_id=f"test-cb-initial-{test_id}",
                reconnect_interval=0.3,
                reconnect_max_interval=1.0,
                topic_prefix=f"test/cb/{test_id[:8]}",
            )

            client = MqttClient(settings=settings)
            connect_count: list[int] = []

            async def on_connect() -> None:
                connect_count.append(1)

            client.add_connect_callback(on_connect)

            try:
                await client.start()
                await _wait_connected(client)

                # Give callback time to fire
                for _ in range(20):
                    if connect_count:
                        break
                    await asyncio.sleep(0.1)

                assert len(connect_count) == 1, (
                    "Connect callback should fire once on initial connect"
                )
            finally:
                await client.stop()
        finally:
            with contextlib.suppress(Exception):
                container.stop()

    async def test_connect_callback_fires_on_reconnect_and_retained_seen(
        self,
        mosquitto_config_path: Path,
    ) -> None:
        """Callback fires on reconnect; retained 'online' visible to late subscriber.

        Technique: register a callback that publishes retained 'online' to an
        availability topic; kill + restart broker; assert callback fires again
        and a late subscriber sees 'online' on the availability topic.
        """
        port = _find_free_port()
        container = _FixedPortMosquitto(host_port=port)

        try:
            container.start(configfile=str(mosquitto_config_path))
            host = container.get_container_host_ip()

            test_id = uuid.uuid4().hex
            prefix = f"test/cb-reconnect/{test_id[:8]}"
            avail_topic = f"{prefix}/device/availability"

            settings = MqttSettings(
                host=host,
                port=port,
                client_id=f"test-cb-reconnect-{test_id}",
                reconnect_interval=0.3,
                reconnect_max_interval=1.0,
                topic_prefix=prefix,
            )
            client = MqttClient(settings=settings)
            connect_events: list[str] = []

            async def on_connect() -> None:
                connect_events.append("connected")
                await client.publish(avail_topic, "online", retain=True, qos=1)

            client.add_connect_callback(on_connect)

            try:
                # Phase 1: initial connect
                await client.start()
                await _wait_connected(client)

                for _ in range(20):
                    if connect_events:
                        break
                    await asyncio.sleep(0.1)
                assert len(connect_events) == 1, "Callback must fire on first connect"

                # Phase 2: kill the broker
                docker_container = container.get_wrapped_container()
                docker_container.kill()

                for _ in range(50):
                    if not client.is_connected:
                        break
                    await asyncio.sleep(0.1)
                assert not client.is_connected, "Client should lose connection"

                # Phase 3: restart broker
                docker_container.start()
                await asyncio.sleep(1)

                await _wait_connected(client, timeout=10.0)

                for _ in range(20):
                    if len(connect_events) >= 2:
                        break
                    await asyncio.sleep(0.1)
                assert len(connect_events) >= 2, (
                    "Callback must fire again after reconnect"
                )

                # Phase 4: late subscriber sees retained 'online'
                received: list[str] = []
                sub_event = asyncio.Event()

                async def on_msg(topic: str, payload: str) -> None:
                    if topic == avail_topic:
                        received.append(payload)
                        sub_event.set()

                sub_settings = settings.model_copy(
                    update={"client_id": f"test-cb-sub-{test_id}"},
                )
                subscriber = MqttClient(settings=sub_settings)
                subscriber.on_message(on_msg)
                try:
                    await subscriber.start()
                    await _wait_connected(subscriber)
                    await subscriber.subscribe(avail_topic)

                    await asyncio.wait_for(sub_event.wait(), timeout=5.0)
                    assert received[-1] == "online", (
                        "Late subscriber must see retained 'online'"
                    )
                finally:
                    await subscriber.stop()
            finally:
                await client.stop()
        finally:
            with contextlib.suppress(Exception):
                container.stop()


# ---------------------------------------------------------------------------
# Helpers (local copies — each integration module is self-contained)
# ---------------------------------------------------------------------------


class _FixedPortMosquitto(MosquittoContainer):
    """MosquittoContainer with a pinned host port for reconnection tests."""

    def __init__(self, host_port: int) -> None:
        super().__init__()
        self._host_port = host_port

    def _configure(self) -> None:
        super()._configure()
        self.ports[self.MQTT_PORT] = self._host_port


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_connected(*clients: MqttClient, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    for client in clients:
        while loop.time() < deadline:
            if client.is_connected:
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError("Client failed to connect within timeout")
