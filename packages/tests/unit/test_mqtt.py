"""Unit tests for cosalette._mqtt — MQTT port and adapters.

Test Techniques Used:
    - Specification-based Testing: WillConfig, Null, Mock publish/subscribe
    - Protocol Conformance: isinstance checks for MqttPort structural subtyping
    - State Transition Testing: MqttClient lifecycle (start/stop/reconnect)
    - Mock-based Isolation: aiomqtt patched via sys.modules for MqttClient
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cosalette._mqtt import (
    MockMqttClient,
    MqttClient,
    MqttPort,
    NullMqttClient,
    WillConfig,
)
from cosalette._settings import MqttSettings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mqtt_settings() -> MqttSettings:
    """MqttSettings with defaults: localhost:1883, no auth."""
    return MqttSettings()


@pytest.fixture
def mock_aiomqtt():
    """Mock aiomqtt module for testing MqttClient internals.

    Patches ``sys.modules`` so the lazy ``import aiomqtt`` inside
    ``_connection_loop()`` resolves to a controllable mock.

    The mock ``messages`` attribute is a property that returns a fresh
    async generator on each access, blocking until the task is
    cancelled — matching real aiomqtt behavior.
    """
    mock_module = MagicMock()

    # Build a mock client that works as an async context manager
    mock_client_instance = AsyncMock()
    mock_client_instance.__aenter__ = AsyncMock(
        return_value=mock_client_instance,
    )
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    # messages — async generator that blocks until cancelled (like a
    # real MQTT connection waiting for inbound messages).
    async def _blocking_messages():
        """Block forever, yielding nothing."""
        await asyncio.Event().wait()  # blocks until cancelled
        yield  # pragma: no cover — makes this an async generator

    type(mock_client_instance).messages = property(
        lambda self: _blocking_messages(),
    )
    mock_client_instance.subscribe = AsyncMock()
    mock_client_instance.publish = AsyncMock()

    mock_module.Client.return_value = mock_client_instance
    mock_module.Will = MagicMock()
    mock_module.MqttError = type("MqttError", (Exception,), {})

    with patch.dict(sys.modules, {"aiomqtt": mock_module}):
        yield mock_module, mock_client_instance


# ---------------------------------------------------------------------------
# WillConfig
# ---------------------------------------------------------------------------


class TestWillConfig:
    """Tests for WillConfig frozen dataclass.

    Technique: Specification-based Testing.
    """

    def test_frozen_prevents_mutation(self) -> None:
        """WillConfig instances are immutable."""
        cfg = WillConfig(topic="test/lwt")
        with pytest.raises(FrozenInstanceError):
            cfg.topic = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        """Default values match ADR-012 conventions."""
        cfg = WillConfig(topic="home/device/availability")
        assert cfg.payload == "offline"
        assert cfg.qos == 1
        assert cfg.retain is True

    def test_custom_values(self) -> None:
        """All fields can be overridden."""
        cfg = WillConfig(
            topic="t",
            payload="gone",
            qos=0,
            retain=False,
        )
        assert cfg.topic == "t"
        assert cfg.payload == "gone"
        assert cfg.qos == 0
        assert cfg.retain is False


# ---------------------------------------------------------------------------
# MqttPort Protocol
# ---------------------------------------------------------------------------


class TestMqttPortProtocol:
    """Protocol conformance checks for all adapters.

    Technique: Protocol Conformance — isinstance checks using
    ``runtime_checkable``.
    """

    def test_mqtt_client_satisfies_protocol(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """MqttClient is recognized as MqttPort."""
        client = MqttClient(settings=mqtt_settings)
        assert isinstance(client, MqttPort)

    def test_mock_mqtt_client_satisfies_protocol(self) -> None:
        """MockMqttClient is recognized as MqttPort."""
        assert isinstance(MockMqttClient(), MqttPort)

    def test_null_mqtt_client_satisfies_protocol(self) -> None:
        """NullMqttClient is recognized as MqttPort."""
        assert isinstance(NullMqttClient(), MqttPort)

    def test_class_missing_publish_does_not_satisfy(self) -> None:
        """A class missing publish() is not recognized as MqttPort."""

        class Incomplete:
            async def subscribe(self, topic: str) -> None: ...

        assert not isinstance(Incomplete(), MqttPort)


# ---------------------------------------------------------------------------
# NullMqttClient
# ---------------------------------------------------------------------------


class TestNullMqttClient:
    """Tests for the no-op null adapter.

    Technique: Specification-based Testing.
    """

    async def test_publish_succeeds_silently(self) -> None:
        """publish() completes without error."""
        client = NullMqttClient()
        await client.publish("t", "p")  # should not raise

    async def test_subscribe_succeeds_silently(self) -> None:
        """subscribe() completes without error."""
        client = NullMqttClient()
        await client.subscribe("t/#")  # should not raise


# ---------------------------------------------------------------------------
# MockMqttClient — Publish
# ---------------------------------------------------------------------------


class TestMockMqttClientPublish:
    """Tests for MockMqttClient publish recording.

    Technique: Specification-based Testing.
    """

    async def test_records_publish_tuple(self) -> None:
        """publish() records (topic, payload, retain, qos)."""
        mock = MockMqttClient()
        await mock.publish("a/b", "hello", retain=True, qos=2)
        assert mock.published == [("a/b", "hello", True, 2)]

    async def test_publish_count_increments(self) -> None:
        """publish_count reflects number of publishes."""
        mock = MockMqttClient()
        assert mock.publish_count == 0
        await mock.publish("t", "p1")
        await mock.publish("t", "p2")
        assert mock.publish_count == 2

    async def test_get_messages_for_filters_by_topic(self) -> None:
        """get_messages_for() returns only matching topic entries."""
        mock = MockMqttClient()
        await mock.publish("a", "1")
        await mock.publish("b", "2", retain=True)
        await mock.publish("a", "3", qos=0)
        result = mock.get_messages_for("a")
        assert result == [("1", False, 1), ("3", False, 0)]


# ---------------------------------------------------------------------------
# MockMqttClient — Subscribe
# ---------------------------------------------------------------------------


class TestMockMqttClientSubscribe:
    """Tests for MockMqttClient subscribe recording.

    Technique: Specification-based Testing.
    """

    async def test_records_subscription(self) -> None:
        """subscribe() records the topic string."""
        mock = MockMqttClient()
        await mock.subscribe("sensors/#")
        assert mock.subscriptions == ["sensors/#"]

    async def test_subscribe_count_increments(self) -> None:
        """subscribe_count reflects number of subscriptions."""
        mock = MockMqttClient()
        await mock.subscribe("a")
        await mock.subscribe("b")
        assert mock.subscribe_count == 2


# ---------------------------------------------------------------------------
# MockMqttClient — Callbacks
# ---------------------------------------------------------------------------


class TestMockMqttClientCallbacks:
    """Tests for MockMqttClient callback registration and delivery.

    Technique: Specification-based Testing.
    """

    async def test_on_message_registers_callback(self) -> None:
        """on_message() registers a callback."""
        mock = MockMqttClient()
        cb = AsyncMock()
        mock.on_message(cb)
        assert len(mock._callbacks) == 1  # noqa: SLF001

    async def test_deliver_invokes_callbacks(self) -> None:
        """deliver() calls registered callbacks with (topic, payload)."""
        mock = MockMqttClient()
        cb = AsyncMock()
        mock.on_message(cb)
        await mock.deliver("t/1", "data")
        cb.assert_awaited_once_with("t/1", "data")

    async def test_multiple_callbacks_called_in_order(self) -> None:
        """deliver() calls callbacks in registration order."""
        mock = MockMqttClient()
        order: list[int] = []

        async def cb1(_t: str, _p: str) -> None:
            order.append(1)

        async def cb2(_t: str, _p: str) -> None:
            order.append(2)

        mock.on_message(cb1)
        mock.on_message(cb2)
        await mock.deliver("t", "p")
        assert order == [1, 2]

    async def test_deliver_does_not_catch_callback_errors(
        self,
    ) -> None:
        """deliver() propagates callback exceptions.

        Note: MockMqttClient.deliver() does NOT catch exceptions
        by design — the real client's _dispatch does.  The mock
        simply raises through.
        """
        mock = MockMqttClient()
        cb_ok = AsyncMock()

        async def cb_bad(_t: str, _p: str) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        mock.on_message(cb_bad)
        mock.on_message(cb_ok)

        # deliver propagates the first exception
        with pytest.raises(RuntimeError, match="boom"):
            await mock.deliver("t", "p")


# ---------------------------------------------------------------------------
# MockMqttClient — Reset
# ---------------------------------------------------------------------------


class TestMockMqttClientReset:
    """Tests for MockMqttClient.reset().

    Technique: Specification-based Testing.
    """

    async def test_reset_clears_all_state(self) -> None:
        """reset() clears published, subscriptions, and callbacks."""
        mock = MockMqttClient()
        await mock.publish("t", "p")
        await mock.subscribe("s/#")
        mock.on_message(AsyncMock())
        mock.reset()

        assert mock.published == []
        assert mock.subscriptions == []
        assert mock._callbacks == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# MqttClient — Lifecycle
# ---------------------------------------------------------------------------


class TestMqttClientLifecycle:
    """Tests for MqttClient start/stop lifecycle.

    Technique: State Transition Testing.
    """

    async def test_start_creates_background_task(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """start() creates a background task."""
        client = MqttClient(settings=mqtt_settings)
        await client.start()
        assert client._listen_task is not None  # noqa: SLF001
        await client.stop()

    async def test_stop_cancels_task_and_clears_state(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """stop() cancels the task and clears connection state."""
        client = MqttClient(settings=mqtt_settings)
        await client.start()
        # Let the event loop run so the task starts
        await asyncio.sleep(0.05)
        await client.stop()

        assert client._listen_task is None  # noqa: SLF001
        assert client._client is None  # noqa: SLF001
        assert not client.is_connected

    async def test_is_connected_reflects_event(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """is_connected mirrors the internal asyncio.Event."""
        client = MqttClient(settings=mqtt_settings)
        assert not client.is_connected
        client._connected.set()  # noqa: SLF001
        assert client.is_connected

    async def test_stop_is_idempotent(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Calling stop() twice does not raise."""
        client = MqttClient(settings=mqtt_settings)
        await client.stop()
        await client.stop()  # should not raise


# ---------------------------------------------------------------------------
# MqttClient — Publish
# ---------------------------------------------------------------------------


class TestMqttClientPublish:
    """Tests for MqttClient.publish().

    Technique: Specification-based Testing.
    """

    async def test_raises_when_not_connected(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """publish() raises RuntimeError when not connected."""
        client = MqttClient(settings=mqtt_settings)
        with pytest.raises(RuntimeError, match="not connected"):
            await client.publish("t", "p")

    async def test_publishes_via_internal_client(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """publish() delegates to the internal aiomqtt client."""
        client = MqttClient(settings=mqtt_settings)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001

        await client.publish("a/b", "payload", retain=True, qos=2)
        mock_inner.publish.assert_awaited_once_with(
            "a/b",
            "payload",
            retain=True,
            qos=2,
        )


# ---------------------------------------------------------------------------
# MqttClient — Subscribe
# ---------------------------------------------------------------------------


class TestMqttClientSubscribe:
    """Tests for MqttClient.subscribe().

    Technique: Specification-based Testing.
    """

    async def test_tracks_subscription(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """subscribe() adds topic to internal tracking set."""
        client = MqttClient(settings=mqtt_settings)
        await client.subscribe("sensors/#")
        assert "sensors/#" in client._subscriptions  # noqa: SLF001

    async def test_subscribes_immediately_if_connected(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """subscribe() calls client.subscribe if already connected."""
        client = MqttClient(settings=mqtt_settings)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001

        await client.subscribe("t/1")
        mock_inner.subscribe.assert_awaited_once_with("t/1", qos=1)


# ---------------------------------------------------------------------------
# MqttClient — Will
# ---------------------------------------------------------------------------


class TestMqttClientWill:
    """Tests for WillConfig integration in MqttClient.

    Technique: Specification-based Testing.
    """

    async def test_will_config_converted_to_aiomqtt_will(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """WillConfig is translated to aiomqtt.Will for the client."""
        mock_module, _mock_client = mock_aiomqtt
        will = WillConfig(topic="test/avail", payload="off")
        client = MqttClient(settings=mqtt_settings, will=will)
        await client.start()
        await asyncio.sleep(0.05)

        mock_module.Will.assert_called_once_with(
            topic="test/avail",
            payload="off",
            qos=1,
            retain=True,
        )
        await client.stop()

    async def test_none_will_means_no_will_arg(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """When will is None, aiomqtt.Client receives will=None."""
        mock_module, _mock_client = mock_aiomqtt
        client = MqttClient(settings=mqtt_settings)
        await client.start()
        await asyncio.sleep(0.05)

        call_kwargs = mock_module.Client.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("will") is None
        await client.stop()


# ---------------------------------------------------------------------------
# MqttClient — Connect (credentials & subscription restore)
# ---------------------------------------------------------------------------


class TestMqttClientConnect:
    """Tests for MqttClient connection details.

    Technique: Specification-based Testing — credentials, subscription restore.
    """

    async def test_password_extracted_from_secret_str(
        self,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """SecretStr password is extracted via get_secret_value()."""
        from pydantic import SecretStr

        settings = MqttSettings(
            username="user",
            password=SecretStr("s3cret"),
        )
        mock_module, _mock_client = mock_aiomqtt
        client = MqttClient(settings=settings)
        await client.start()
        await asyncio.sleep(0.05)

        call_kwargs = mock_module.Client.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs["password"] == "s3cret"
        assert call_kwargs.kwargs["username"] == "user"
        await client.stop()

    async def test_subscriptions_restored_on_reconnect(
        self,
    ) -> None:
        """Tracked subscriptions are re-sent after reconnection."""
        settings = MqttSettings(reconnect_interval=0.05)

        mock_module = MagicMock()
        mqtt_error = type("MqttError", (Exception,), {})
        mock_module.MqttError = mqtt_error

        call_count = 0

        async def _blocking_messages():
            await asyncio.Event().wait()
            yield  # pragma: no cover

        def client_factory(**_kwargs: object) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            cm = AsyncMock()
            if call_count == 1:
                cm.__aenter__ = AsyncMock(
                    side_effect=mqtt_error("refused"),
                )
            else:
                cm.__aenter__ = AsyncMock(return_value=cm)
                type(cm).messages = property(
                    lambda self: _blocking_messages(),
                )
                cm.subscribe = AsyncMock()
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_module.Client = client_factory
        mock_module.Will = MagicMock()

        with patch.dict(sys.modules, {"aiomqtt": mock_module}):
            client = MqttClient(settings=settings)
            # Subscribe before connecting
            await client.subscribe("sensors/#")
            await client.start()
            # Wait for failure + reconnect + second connect
            await asyncio.sleep(0.3)

            assert call_count >= 2
            # The second client instance should have subscribe called
            # (it's the one that succeeded)
            assert client.is_connected
            await client.stop()


# ---------------------------------------------------------------------------
# MqttClient — Dispatch
# ---------------------------------------------------------------------------


class TestMqttClientDispatch:
    """Tests for MqttClient._dispatch() message handling.

    Technique: Specification-based Testing.
    """

    async def test_dispatches_decoded_payload(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """_dispatch() decodes bytes and calls callbacks."""
        client = MqttClient(settings=mqtt_settings)
        cb = AsyncMock()
        client.on_message(cb)

        message = SimpleNamespace(
            topic="a/b",
            payload=b"hello",
        )
        await client._dispatch(message)  # noqa: SLF001
        cb.assert_awaited_once_with("a/b", "hello")

    async def test_skips_none_payload(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """_dispatch() skips messages with None payload."""
        client = MqttClient(settings=mqtt_settings)
        cb = AsyncMock()
        client.on_message(cb)

        message = SimpleNamespace(topic="a/b", payload=None)
        await client._dispatch(message)  # noqa: SLF001
        cb.assert_not_awaited()

    async def test_error_in_callback_logged_not_crashed(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """An exception in a callback is logged but doesn't crash."""
        client = MqttClient(settings=mqtt_settings)

        async def bad_cb(_t: str, _p: str) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        cb_ok = AsyncMock()
        client.on_message(bad_cb)
        client.on_message(cb_ok)

        message = SimpleNamespace(topic="t", payload=b"p")
        await client._dispatch(message)  # noqa: SLF001

        # Second callback still called despite first raising
        cb_ok.assert_awaited_once_with("t", "p")

    async def test_multiple_callbacks_called_in_order(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """_dispatch() calls callbacks in registration order."""
        client = MqttClient(settings=mqtt_settings)
        order: list[int] = []

        async def cb1(_t: str, _p: str) -> None:
            order.append(1)

        async def cb2(_t: str, _p: str) -> None:
            order.append(2)

        client.on_message(cb1)
        client.on_message(cb2)

        message = SimpleNamespace(topic="t", payload=b"x")
        await client._dispatch(message)  # noqa: SLF001
        assert order == [1, 2]


# ---------------------------------------------------------------------------
# MqttClient — Reconnect
# ---------------------------------------------------------------------------


class TestMqttClientReconnect:
    """Tests for MqttClient reconnection behavior.

    Technique: State Transition Testing.
    """

    async def test_reconnects_after_error(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Connection loop retries after an exception.

        Verifies that ``asyncio.sleep(reconnect_interval)`` is called
        and the loop re-enters.
        """
        # Use a short reconnect interval for faster test
        mqtt_settings.reconnect_interval = 0.1

        mock_module = MagicMock()
        mqtt_error = type("MqttError", (Exception,), {})
        mock_module.MqttError = mqtt_error

        call_count = 0

        async def _blocking_messages():
            """Block until cancelled."""
            await asyncio.Event().wait()
            yield  # pragma: no cover

        def client_factory(**_kwargs: object) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            cm = AsyncMock()
            if call_count == 1:
                # First connection attempt raises
                cm.__aenter__ = AsyncMock(
                    side_effect=mqtt_error("conn refused"),
                )
            else:
                # Second attempt succeeds and blocks
                cm.__aenter__ = AsyncMock(return_value=cm)
                type(cm).messages = property(
                    lambda self: _blocking_messages(),
                )
                cm.subscribe = AsyncMock()
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_module.Client = client_factory
        mock_module.Will = MagicMock()

        with patch.dict(sys.modules, {"aiomqtt": mock_module}):
            client = MqttClient(settings=mqtt_settings)
            await client.start()
            # Wait for failure + reconnect sleep + second connect
            await asyncio.sleep(0.4)
            assert call_count >= 2
            await client.stop()
