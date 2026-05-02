"""Integration test fixtures — Mosquitto broker via testcontainers."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from testcontainers.mqtt import MosquittoContainer

from cosalette._mqtt._client import MqttClient
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

    Includes deterministic readiness checking to ensure broker is fully
    operational before yielding to tests.
    """
    container = None
    try:
        container = MosquittoContainer()
        container.start(configfile=str(mosquitto_config_path))
        _wait_for_broker_ready(container)
    except Exception as e:
        if container is not None:
            with contextlib.suppress(Exception):
                container.stop()
        pytest.fail(
            f"Failed to start Mosquitto container. "
            f"Check Docker daemon is running and container image is available. "
            f"Error: {e}"
        )

    try:
        yield container
    finally:
        with contextlib.suppress(Exception):
            # Suppress cleanup errors - container may already be stopped
            container.stop()


@pytest.fixture
def mqtt_settings(
    mosquitto_container: MosquittoContainer,
    request: pytest.FixtureRequest,
) -> MqttSettings:
    """Create MqttSettings pointing at the ephemeral Mosquitto broker.

    Each test gets a unique client_id to avoid MQTT session collisions.
    Uses fast reconnect intervals for responsive tests.

    Test isolation: Each test gets a unique topic namespace based on
    test name and run UUID to prevent cross-test interference.
    """
    host = mosquitto_container.get_container_host_ip()
    port = int(mosquitto_container.get_exposed_port(1883))

    # Generate unique identifiers for full test isolation
    test_uuid = uuid.uuid4().hex
    test_name = request.node.name.replace("[", "_").replace("]", "_")

    return MqttSettings(
        host=host,
        port=port,
        client_id=f"test-{test_name}-{test_uuid}",
        reconnect_interval=0.5,
        reconnect_max_interval=2.0,
        topic_prefix=f"test/{test_name}/{test_uuid[:8]}",
    )


@pytest.fixture
async def mqtt_client(mqtt_settings: MqttSettings) -> AsyncIterator[MqttClient]:
    """Create, start, and yield a real MqttClient; stop on teardown.

    Includes robust connection verification and clear failure messages.
    """
    client = MqttClient(settings=mqtt_settings)
    try:
        await client.start()
        await _wait_for_client_connected(client, mqtt_settings)
        yield client
    finally:
        await client.stop()


def _wait_for_broker_ready(
    container: MosquittoContainer,
    timeout: float = 10.0,
) -> None:
    """Wait for Mosquitto broker to be ready for connections.

    Performs port availability check and TCP connectivity validation
    to ensure broker is operational.

    Raises:
        TimeoutError: If broker doesn't become ready within timeout.
        ConnectionError: If broker fails health checks.
    """
    start_time = time.monotonic()
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(1883))

    # Phase 1: Wait for port to be available
    while time.monotonic() - start_time < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise TimeoutError(
            f"Mosquitto broker port {port} not available after {timeout}s. "
            f"Check container logs for startup errors."
        )

    # Phase 2: Brief settle time for broker initialization
    time.sleep(0.5)

    # Phase 3: Verify port is still responsive
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError as e:
        raise ConnectionError(f"Mosquitto broker failed final health check: {e}") from e


async def _wait_for_client_connected(
    client: MqttClient,
    settings: MqttSettings,
    timeout: float = 10.0,
) -> None:
    """Wait for MQTT client to establish connection with detailed error handling.

    Args:
        client: The MqttClient to check
        settings: Settings used for connection (for error context)
        timeout: Maximum wait time in seconds

    Raises:
        TimeoutError: If connection not established within timeout
    """
    start_time = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start_time < timeout:
        if client.is_connected:
            return
        await asyncio.sleep(0.1)

    raise TimeoutError(
        f"MqttClient failed to connect to {settings.host}:{settings.port} "
        f"within {timeout}s. Check broker availability and network connectivity. "
        f"Client ID: {settings.client_id}"
    )
