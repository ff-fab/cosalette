"""Unit tests for MqttClient MQTT 5 retained-message expiry and refresh ledger.

Covers the ADR-078 surface added to :class:`~cosalette._mqtt._client.MqttClient`:
protocol selection, MQTT 5 PUBLISH/WILLMESSAGE properties, the retained-publish
ledger (add/overwrite/clear), the periodic refresh loop, the post-reconnect
ledger refresh, and the MQTT-5-connection-failure diagnostic hint.

Test Techniques Used:
    - Decision Table Testing: protocol_version x retain gates on properties/ledger
    - State Transition Testing: ledger add/overwrite/clear, connected/disconnected
    - Boundary Value Testing: ledger size warning at the 1000-entry threshold
    - Mock-based Isolation: aiomqtt patched via sys.modules for MqttClient
    - Error Guessing: failure paths around the ledger write / wire publish boundary
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cosalette._mqtt import MqttClient, WillConfig
from cosalette._mqtt._client import _RetainedEntry
from cosalette._settings import MqttSettings
from cosalette.testing import FakeClock

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mqtt_settings() -> MqttSettings:
    """Plain-TCP 3.1.1 test fixture: localhost:1883, no auth, TLS disabled."""
    return MqttSettings(tls=False)


@pytest.fixture
def mqtt_settings_v5() -> MqttSettings:
    """MQTT 5 test fixture — short expiry interval keeps the refresh period small."""
    return MqttSettings(tls=False, protocol_version="5", message_expiry_interval=9)


@pytest.fixture
def mock_aiomqtt():
    """Mock aiomqtt module for testing MqttClient internals.

    Patches ``sys.modules`` so the lazy ``import aiomqtt`` inside
    ``_connection_loop()`` resolves to a controllable mock. Mirrors the
    fixture in ``test_mqtt.py`` (no shared conftest for this package).
    """
    mock_module = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.__aenter__ = AsyncMock(
        return_value=mock_client_instance,
    )
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

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


def _client_with_mock_inner(settings: MqttSettings) -> tuple[MqttClient, AsyncMock]:
    """Build a client wired directly to a mock inner aiomqtt client."""
    client = MqttClient(settings=settings)
    mock_inner = AsyncMock()
    client._client = mock_inner  # noqa: SLF001
    return client, mock_inner


async def wait_for_condition(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.01,
) -> None:
    """Wait until *predicate* returns True or timeout elapses."""

    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(poll_interval)

    await asyncio.wait_for(_poll(), timeout=timeout)


# ---------------------------------------------------------------------------
# aiomqtt.Client() kwargs — protocol selection
# ---------------------------------------------------------------------------


class TestClientProtocolKwargs:
    """aiomqtt.Client() only receives protocol= under MQTT 5.

    Technique: Decision Table Testing — protocol_version selects the kwarg.
    """

    async def test_protocol_311_omits_protocol_kwarg(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """3.1.1 stays byte-identical: no protocol= kwarg at all."""
        mock_module, _mock_client = mock_aiomqtt
        client = MqttClient(settings=mqtt_settings)

        await client.start()
        await asyncio.sleep(0.05)

        call_kwargs = mock_module.Client.call_args.kwargs
        assert "protocol" not in call_kwargs
        await client.stop()

    async def test_protocol_v5_passes_protocol_version_kwarg(
        self,
        mqtt_settings_v5: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """protocol_version='5' passes protocol=ProtocolVersion.V5."""
        mock_module, _mock_client = mock_aiomqtt
        client = MqttClient(settings=mqtt_settings_v5)

        await client.start()
        await asyncio.sleep(0.05)

        call_kwargs = mock_module.Client.call_args.kwargs
        assert call_kwargs["protocol"] is mock_module.ProtocolVersion.V5
        await client.stop()


# ---------------------------------------------------------------------------
# _publish_raw — MQTT 5 PUBLISH properties
# ---------------------------------------------------------------------------


class TestPublishRawProperties:
    """_publish_raw only attaches properties= on retained publishes under v5.

    Technique: Decision Table Testing — protocol_version x retain.
    """

    async def test_retained_publish_under_v5_includes_properties(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """Retained publish under v5 carries a MessageExpiryInterval property."""
        client, mock_inner = _client_with_mock_inner(mqtt_settings_v5)

        await client._publish_raw("t/1", "payload", retain=True, qos=1)  # noqa: SLF001

        call_kwargs = mock_inner.publish.call_args.kwargs
        assert "properties" in call_kwargs
        assert call_kwargs["properties"].MessageExpiryInterval == 9

    async def test_non_retained_publish_under_v5_excludes_properties(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """A non-retained v5 publish is byte-identical to 3.1.1 (no properties)."""
        client, mock_inner = _client_with_mock_inner(mqtt_settings_v5)

        await client._publish_raw("t/1", "payload", retain=False, qos=1)  # noqa: SLF001

        assert "properties" not in mock_inner.publish.call_args.kwargs

    async def test_retained_publish_under_311_excludes_properties(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Retained publishes under 3.1.1 never carry a properties= kwarg."""
        client, mock_inner = _client_with_mock_inner(mqtt_settings)

        await client._publish_raw("t/1", "payload", retain=True, qos=1)  # noqa: SLF001

        assert "properties" not in mock_inner.publish.call_args.kwargs


# ---------------------------------------------------------------------------
# _build_will — WILLMESSAGE properties
# ---------------------------------------------------------------------------


class TestBuildWillProperties:
    """_build_will only forwards properties= when expiry is active.

    Technique: Specification-based Testing.
    """

    def test_will_properties_passed_when_provided(self) -> None:
        """A non-None properties object is forwarded to aiomqtt.Will."""
        mock_mod = MagicMock()
        will_cfg = WillConfig(topic="a/avail", payload="off")
        props = MagicMock()

        MqttClient._build_will(mock_mod, will_cfg, properties=props)  # noqa: SLF001

        mock_mod.Will.assert_called_once_with(
            topic="a/avail",
            payload="off",
            qos=1,
            retain=True,
            properties=props,
        )

    def test_will_properties_omitted_under_311(self) -> None:
        """properties=None (the 3.1.1 path) stays byte-identical: no kwarg at all."""
        mock_mod = MagicMock()
        will_cfg = WillConfig(topic="a/avail", payload="off")

        MqttClient._build_will(mock_mod, will_cfg, properties=None)  # noqa: SLF001

        mock_mod.Will.assert_called_once_with(
            topic="a/avail",
            payload="off",
            qos=1,
            retain=True,
        )

    async def test_end_to_end_will_carries_message_expiry_under_v5(
        self,
        mqtt_settings_v5: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
    ) -> None:
        """Wired through start(): the WILLMESSAGE properties carry the setting."""
        mock_module, _mock_client = mock_aiomqtt
        will = WillConfig(topic="test/avail", payload="off")
        client = MqttClient(settings=mqtt_settings_v5, will=will)

        await client.start()
        await asyncio.sleep(0.05)

        call_kwargs = mock_module.Will.call_args.kwargs
        assert call_kwargs["properties"].MessageExpiryInterval == 9
        await client.stop()


# ---------------------------------------------------------------------------
# Retained-publish ledger — add / overwrite / clear
# ---------------------------------------------------------------------------


class TestRetainedLedger:
    """publish() maintains the retained-message ledger under v5 only.

    Technique: State Transition Testing — add, overwrite, clear, untouched.
    """

    async def test_publish_retain_true_adds_ledger_entry(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """A retained publish under v5 writes a _RetainedEntry."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)

        await client.publish("t/1", "hello", retain=True, qos=1)

        entry = client._retained["t/1"]  # noqa: SLF001
        assert entry.payload == "hello"
        assert entry.qos == 1

    async def test_publish_overwrites_existing_entry(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """Publishing again to the same topic overwrites the ledger entry."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)

        await client.publish("t/1", "first", retain=True)
        await client.publish("t/1", "second", retain=True)

        assert client._retained["t/1"].payload == "second"  # noqa: SLF001
        assert len(client._retained) == 1  # noqa: SLF001

    async def test_publish_empty_payload_clears_entry(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """An empty-string retained publish pops the ledger entry."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)
        await client.publish("t/1", "first", retain=True)

        await client.publish("t/1", "", retain=True)

        assert "t/1" not in client._retained  # noqa: SLF001

    async def test_publish_non_retained_leaves_entry_untouched(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """publish(retain=False) on a ledgered topic does not touch the entry."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)
        await client.publish("t/1", "first", retain=True)

        await client.publish("t/1", "ignored", retain=False)

        assert client._retained["t/1"].payload == "first"  # noqa: SLF001

    async def test_publish_retain_true_under_311_never_touches_ledger(
        self,
        mqtt_settings: MqttSettings,
    ) -> None:
        """Under 3.1.1 the ledger stays empty regardless of retain=True."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings)

        await client.publish("t/1", "hello", retain=True)

        assert client._retained == {}  # noqa: SLF001

    async def test_dict_payload_mutated_after_publish_keeps_original_serialisation(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """The ledger holds the serialisation taken *before* a later mutation."""
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)
        payload = {"count": 1}

        await client.publish("t/1", payload, retain=True)
        payload["count"] = 2  # mutate the dict after publish() has returned

        assert client._retained["t/1"].payload == '{"count":1}'  # noqa: SLF001

    async def test_publish_raises_before_connect_records_nothing(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """publish() on an unconnected client raises and never touches the ledger."""
        client = MqttClient(settings=mqtt_settings_v5)

        with pytest.raises(RuntimeError, match="not connected"):
            await client.publish("t/1", "hello", retain=True)

        assert client._retained == {}  # noqa: SLF001

    async def test_publish_error_after_enqueue_leaves_ledger_entry_recorded(
        self,
        mqtt_settings_v5: MqttSettings,
    ) -> None:
        """A wire-level failure after the ledger write does not roll it back.

        The ledger write happens synchronously before the (single) await
        inside ``_publish_raw``, matching wire enqueue order — so a
        downstream MqttError still leaves the entry recorded.
        """
        client, mock_inner = _client_with_mock_inner(mqtt_settings_v5)
        mqtt_error = type("MqttError", (Exception,), {})
        mock_inner.publish = AsyncMock(side_effect=mqtt_error("broker gone"))

        with pytest.raises(mqtt_error, match="broker gone"):
            await client.publish("t/1", "hello", retain=True)

        assert client._retained["t/1"].payload == "hello"  # noqa: SLF001

    async def test_ledger_over_1000_entries_logs_warning_once(
        self,
        mqtt_settings_v5: MqttSettings,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Crossing the 1000-entry boundary logs one WARNING, not one per publish.

        Technique: Boundary Value Testing.
        """
        client, _mock_inner = _client_with_mock_inner(mqtt_settings_v5)
        client._retained.update(  # noqa: SLF001
            {f"t/{i}": _RetainedEntry("x", 1, 0.0) for i in range(1000)}
        )

        with caplog.at_level(logging.WARNING, logger="cosalette._mqtt._client"):
            await client.publish("t/1000", "x", retain=True)
            await client.publish("t/1001", "x", retain=True)

        assert caplog.text.count("more than 1000 entries") == 1


# ---------------------------------------------------------------------------
# Refresh loop
# ---------------------------------------------------------------------------


class TestRefreshLoop:
    """_refresh_loop republishes the ledger on a fixed cadence.

    Technique: State Transition Testing — connected vs disconnected.
    """

    async def test_refresh_loop_republishes_all_ledger_entries(
        self,
        mqtt_settings_v5: MqttSettings,
        fake_clock: FakeClock,
    ) -> None:
        """One tick republishes every entry currently in the ledger."""
        client = MqttClient(settings=mqtt_settings_v5, clock=fake_clock)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001
        client._connected.set()  # noqa: SLF001
        client._retained["t/1"] = _RetainedEntry("a", 1, 0.0)  # noqa: SLF001
        client._retained["t/2"] = _RetainedEntry("b", 1, 0.0)  # noqa: SLF001

        refresh_task = asyncio.create_task(client._refresh_loop())  # noqa: SLF001
        try:
            await wait_for_condition(
                lambda: mock_inner.publish.call_count >= 2,
                timeout=2.0,
            )
        finally:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task

        published_topics = {c.args[0] for c in mock_inner.publish.call_args_list}
        assert published_topics == {"t/1", "t/2"}

    async def test_refresh_loop_still_republishes_after_skipping_a_tick(
        self,
        mqtt_settings_v5: MqttSettings,
        fake_clock: FakeClock,
    ) -> None:
        """A tick the test does not observe does not stop the next one firing."""
        client = MqttClient(settings=mqtt_settings_v5, clock=fake_clock)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001
        client._connected.set()  # noqa: SLF001
        client._retained["t/1"] = _RetainedEntry("a", 1, 0.0)  # noqa: SLF001

        refresh_task = asyncio.create_task(client._refresh_loop())  # noqa: SLF001
        try:
            # Let (at least) one tick pass without checking on it ...
            await wait_for_condition(
                lambda: mock_inner.publish.call_count >= 1,
                timeout=2.0,
            )
            calls_before = mock_inner.publish.call_count
            # ... a further tick still republishes the same entry.
            await wait_for_condition(
                lambda: mock_inner.publish.call_count > calls_before,
                timeout=2.0,
            )
        finally:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task

        assert all(c.args[0] == "t/1" for c in mock_inner.publish.call_args_list)

    async def test_refresh_loop_skips_while_disconnected(
        self,
        mqtt_settings_v5: MqttSettings,
        fake_clock: FakeClock,
    ) -> None:
        """No publish happens while `_connected` is not set."""
        client = MqttClient(settings=mqtt_settings_v5, clock=fake_clock)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001
        # _connected left unset — never connected.
        client._retained["t/1"] = _RetainedEntry("a", 1, 0.0)  # noqa: SLF001

        refresh_task = asyncio.create_task(client._refresh_loop())  # noqa: SLF001
        try:
            for _ in range(10):
                await asyncio.sleep(0)
        finally:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task

        mock_inner.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Reconnect refresh
# ---------------------------------------------------------------------------


class TestReconnectRefresh:
    """_run_connect_callbacks refreshes only entries older than the connect instant.

    Technique: Specification-based Testing — a connect callback's own fresher
    reannounce publish must never be double-published by the trailing refresh.
    """

    async def test_refresh_touches_only_pre_connect_entries(
        self,
        mqtt_settings_v5: MqttSettings,
        fake_clock: FakeClock,
    ) -> None:
        """An entry published by a connect callback (same/later instant) is skipped."""
        client = MqttClient(settings=mqtt_settings_v5, clock=fake_clock)
        mock_inner = AsyncMock()
        client._client = mock_inner  # noqa: SLF001

        fake_clock.advance(10.0)
        client._retained["stale"] = _RetainedEntry(  # noqa: SLF001
            "stale-payload", 1, fake_clock.now()
        )
        fake_clock.advance(5.0)  # this instant becomes connected_at == 15.0

        async def reannounce() -> None:
            """Simulate a connect callback republishing its own fresher state."""
            client._retained["fresh"] = _RetainedEntry(  # noqa: SLF001
                "fresh-payload", 1, fake_clock.now()
            )

        client.add_connect_callback(reannounce)

        await client._run_connect_callbacks()  # noqa: SLF001

        published_topics = {c.args[0] for c in mock_inner.publish.call_args_list}
        assert published_topics == {"stale"}


# ---------------------------------------------------------------------------
# MQTT 5 startup logging
# ---------------------------------------------------------------------------


class TestV5StartupLogging:
    """start() logs an INFO line describing MQTT 5 expiry only when active.

    Technique: Equivalence Partitioning — v5 vs 3.1.1.
    """

    async def test_start_logs_info_when_v5_enabled(
        self,
        mqtt_settings_v5: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """protocol_version='5' logs an INFO line naming the expiry/refresh config."""
        client = MqttClient(settings=mqtt_settings_v5)

        with caplog.at_level(logging.INFO, logger="cosalette._mqtt._client"):
            await client.start()
            await asyncio.sleep(0.05)

        assert "MQTT 5 enabled" in caplog.text
        await client.stop()

    async def test_start_does_not_log_v5_info_under_311(
        self,
        mqtt_settings: MqttSettings,
        mock_aiomqtt: tuple[MagicMock, AsyncMock],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """3.1.1 stays quiet — no MQTT 5 startup line."""
        client = MqttClient(settings=mqtt_settings)

        with caplog.at_level(logging.INFO, logger="cosalette._mqtt._client"):
            await client.start()
            await asyncio.sleep(0.05)

        assert "MQTT 5 enabled" not in caplog.text
        await client.stop()


# ---------------------------------------------------------------------------
# MQTT 5 connection-failure diagnostic hint
# ---------------------------------------------------------------------------


class TestV5ConnectionHint:
    """Tests for the MQTT-5-against-3.1.1-only-broker hint.

    Mirrors ``TestTlsMismatchDiagnostic`` in ``test_mqtt.py`` for
    ``_log_v5_connection_hint``.

    Technique: Decision Table Testing — the v5 / ever-connected / already-logged
    gates.
    """

    def test_hint_logged_for_v5_connection_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The hint names the broker and the opt-out setting."""
        client = MqttClient(
            settings=MqttSettings(host="mqtt.example", protocol_version="5")
        )

        with caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"):
            client._log_v5_connection_hint(Exception("connection refused"))  # noqa: SLF001

        assert "MQTT__PROTOCOL_VERSION=3.1.1" in caplog.text
        assert "mqtt.example" in caplog.text

    def test_hint_logged_only_once_per_run(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Exponential backoff must not turn the hint into log spam."""
        client = MqttClient(settings=MqttSettings(protocol_version="5"))

        with caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"):
            for _ in range(3):
                client._log_v5_connection_hint(Exception("refused"))  # noqa: SLF001

        assert caplog.text.count("MQTT__PROTOCOL_VERSION=3.1.1") == 1

    def test_no_hint_once_a_connection_has_succeeded(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An established deployment stays quiet on an ordinary reconnect."""
        client = MqttClient(settings=MqttSettings(protocol_version="5"))
        client._ever_connected = True  # noqa: SLF001

        with caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"):
            client._log_v5_connection_hint(Exception("refused"))  # noqa: SLF001

        assert caplog.text == ""

    def test_no_hint_under_311(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The hint never fires when expiry is not active."""
        client = MqttClient(settings=MqttSettings())

        with caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"):
            client._log_v5_connection_hint(Exception("refused"))  # noqa: SLF001

        assert caplog.text == ""

    async def test_first_connection_failure_under_v5_emits_hint_through_loop(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """End-to-end: the reconnect loop's first v5 failure logs the hint.

        Technique: Specification-based Testing — wiring through
        ``_connection_loop`` with a broker that refuses the v5 CONNECT.
        """
        settings = MqttSettings(
            protocol_version="5",
            reconnect_interval=1.0,
            tls=False,
        )

        mock_module = MagicMock()
        mqtt_error = type("MqttError", (Exception,), {})
        mock_module.MqttError = mqtt_error

        def client_factory(**_kwargs: object) -> AsyncMock:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(
                side_effect=mqtt_error("protocol not supported"),
            )
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        mock_module.Client = client_factory
        mock_module.Will = MagicMock()

        async def stop_after_sleep(_seconds: float) -> None:
            client._stopping = True  # noqa: SLF001

        with (
            patch.dict(sys.modules, {"aiomqtt": mock_module}),
            patch("cosalette._mqtt._client.random.uniform", return_value=1.0),
            patch("asyncio.sleep", side_effect=stop_after_sleep),
            caplog.at_level(logging.ERROR, logger="cosalette._mqtt._client"),
        ):
            client = MqttClient(settings=settings)
            await client._connection_loop()  # noqa: SLF001

        assert "MQTT__PROTOCOL_VERSION=3.1.1" in caplog.text
