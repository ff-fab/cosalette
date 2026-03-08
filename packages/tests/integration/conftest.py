"""Integration test fixtures — Mosquitto broker via testcontainers."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from testcontainers.mqtt import MosquittoContainer

from cosalette._mqtt_client import MqttClient
from cosalette._settings import MqttSettings

_MOSQUITTO_CONF = """\
listener 1883
protocol mqtt
allow_anonymous true
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information
log_timestamp_format %Y-%m-%d %H:%M:%S
persistence false
"""


@pytest.fixture(scope="session")
def mosquitto_config_path(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Provide a reusable Mosquitto config file.

    Session-scoped so that both module-scoped containers and ad-hoc
    containers (e.g. reconnection tests) share the same config.
    """
    config_path = tmp_path_factory.mktemp("mqtt-config") / "mosquitto.conf"
    config_path.write_text(_MOSQUITTO_CONF)
    return config_path


@pytest.fixture(scope="module")
def mosquitto_container(
    mosquitto_config_path: Path,
) -> Iterator[MosquittoContainer]:
    """Start a Mosquitto MQTT broker container for the test module.

    Module-scoped to avoid per-test container overhead — the broker
    persists for all tests in the module and is torn down afterward.

    Uses a custom config that disables persistence to avoid the default
    config writing to a non-existent /data/ directory.
    """
    container = MosquittoContainer()
    container.start(configfile=str(mosquitto_config_path))
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def mqtt_settings(mosquitto_container: MosquittoContainer) -> MqttSettings:
    """Create MqttSettings pointing at the ephemeral Mosquitto broker.

    Each test gets a unique client_id to avoid MQTT session collisions.
    Uses fast reconnect intervals for responsive tests.
    """
    host = mosquitto_container.get_container_host_ip()
    port = int(mosquitto_container.get_exposed_port(1883))
    return MqttSettings(
        host=host,
        port=port,
        client_id=f"test-{uuid.uuid4().hex[:8]}",
        reconnect_interval=0.5,
        reconnect_max_interval=2.0,
        topic_prefix="test",
    )


@pytest.fixture
async def mqtt_client(mqtt_settings: MqttSettings) -> AsyncIterator[MqttClient]:
    """Create, start, and yield a real MqttClient; stop on teardown."""
    client = MqttClient(settings=mqtt_settings)
    try:
        await client.start()
        for _ in range(50):  # 5 second timeout
            if client.is_connected:
                break
            await asyncio.sleep(0.1)
        assert client.is_connected, "MqttClient failed to connect within timeout"
        yield client
    finally:
        await client.stop()
