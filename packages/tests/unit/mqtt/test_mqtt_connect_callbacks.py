"""Unit tests for MqttClient connect-callback mechanism.

Tests :meth:`MqttClient.add_connect_callback` and
:meth:`MqttClient._run_connect_callbacks` in isolation — no broker required.

Test Techniques Used:
    - Specification-based Testing: callbacks are awaited in registration order.
    - Error Guessing: a raising callback does not stop subsequent ones.
    - Exception Safety: failures are logged at ERROR level.
"""

from __future__ import annotations

import logging

import pytest

from cosalette._mqtt import MqttClient, MqttConnectAware
from cosalette._settings import MqttSettings

pytestmark = pytest.mark.unit


@pytest.fixture
def mqtt_settings() -> MqttSettings:
    return MqttSettings()


@pytest.fixture
def client(mqtt_settings: MqttSettings) -> MqttClient:
    return MqttClient(settings=mqtt_settings)


class TestMqttConnectAwareProtocol:
    """MqttClient satisfies the MqttConnectAware protocol."""

    def test_mqtt_client_is_connect_aware(self, client: MqttClient) -> None:
        """MqttClient is an instance of MqttConnectAware."""
        assert isinstance(client, MqttConnectAware)


class TestAddConnectCallback:
    """add_connect_callback registers and invokes callbacks."""

    async def test_single_callback_is_invoked(self, client: MqttClient) -> None:
        """A registered callback is awaited by _run_connect_callbacks."""
        called: list[str] = []

        async def cb() -> None:
            called.append("cb")

        client.add_connect_callback(cb)
        await client._run_connect_callbacks()

        assert called == ["cb"]

    async def test_multiple_callbacks_invoked_in_order(
        self, client: MqttClient
    ) -> None:
        """Multiple callbacks are awaited in registration order."""
        order: list[int] = []

        async def cb1() -> None:
            order.append(1)

        async def cb2() -> None:
            order.append(2)

        client.add_connect_callback(cb1)
        client.add_connect_callback(cb2)
        await client._run_connect_callbacks()

        assert order == [1, 2]

    async def test_no_callbacks_is_a_noop(self, client: MqttClient) -> None:
        """_run_connect_callbacks with no registered callbacks does not raise."""
        await client._run_connect_callbacks()  # should not raise

    async def test_raising_callback_does_not_stop_others(
        self,
        client: MqttClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A callback that raises does not prevent subsequent callbacks from running."""
        called: list[str] = []

        async def bad_cb() -> None:
            raise RuntimeError("boom")

        async def good_cb() -> None:
            called.append("good")

        client.add_connect_callback(bad_cb)
        client.add_connect_callback(good_cb)

        with caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"):
            await client._run_connect_callbacks()

        assert called == ["good"]
        assert any("MQTT connect callback failed" in r.message for r in caplog.records)
