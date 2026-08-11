"""Unit tests for cosalette.testing — public test-support utilities.

Test Techniques Used:
    - Specification-based Testing: Public API surface, ``__all__``
      completeness, factory defaults and overrides.
    - Protocol Conformance: FakeClock satisfies ClockPort via
      ``isinstance`` (PEP 544 runtime_checkable).
    - Identity Testing: Re-exported symbols are the *same* objects
      as the originals in their private modules.
    - Fixture Injection: Plugin-registered fixtures are automatically
      available without local definitions.
    - Error Guessing: inject_stream() edge cases (unknown name, empty
      items, shutdown=False).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import pytest
from pydantic import BaseModel

import cosalette._mqtt as _mqtt_mod
import cosalette.testing as testing_mod
from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._persistence._stores import DeviceStore, MemoryStore
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette._schema._consumer_gen import HaDiscoveryPayload
from cosalette._settings import MqttSettings, Settings
from cosalette.mqtt import Payload
from cosalette.testing import (
    AppHarness,
    FakeClock,
    MockMqttClient,
    NullMqttClient,
    assert_discovery_topics_published,
    make_settings,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared test types for inject_stream tests
# (module-level so get_type_hints can resolve them under PEP 563)
# ---------------------------------------------------------------------------


class _Token:
    """Minimal item type used in inject_stream tests."""


class _NoOpStreamPort:
    """Minimal StreamablePort[_Token] stub."""

    async def open(self) -> None: ...  # noqa: E704
    async def close(self) -> None: ...  # noqa: E704
    async def start_scan(self) -> None: ...  # noqa: E704
    async def stop_scan(self) -> None: ...  # noqa: E704
    def register_callback(self, cb: Any) -> None: ...  # noqa: E704


class _Tag:
    """Minimal extra-provider type used in inject_stream DI tests."""


class _FanCommand(BaseModel):
    """Test model for typed command payload tests."""

    speed: int
    timer: int | None = None


# ---------------------------------------------------------------------------
# TestPublicAPI — __all__ and importability
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """All expected symbols are importable and listed in ``__all__``."""

    EXPECTED_NAMES = {
        "AppHarness",
        "FakeClock",
        "MockMqttClient",
        "NullMqttClient",
        "StreamHandlerProxy",
        "assert_discovery_topics_published",
        "make_settings",
    }

    def test_all_contains_expected_symbols(self) -> None:
        """``__all__`` matches the documented public API.

        Technique: Specification-based — verifying module contract.
        """
        assert set(testing_mod.__all__) == self.EXPECTED_NAMES

    def test_all_symbols_importable(self) -> None:
        """Every name in ``__all__`` resolves to an attribute.

        Technique: Specification-based — importability check.
        """
        for name in testing_mod.__all__:
            assert hasattr(testing_mod, name), f"{name} not found on module"


# ---------------------------------------------------------------------------
# TestFakeClock
# ---------------------------------------------------------------------------


class TestFakeClock:
    """FakeClock: deterministic test double for ClockPort."""

    def test_default_time_is_zero(self) -> None:
        """Default-constructed FakeClock starts at 0.0.

        Technique: Specification-based — default value.
        """
        clock = FakeClock()

        assert clock.now() == 0.0

    def test_custom_initial_time(self) -> None:
        """FakeClock accepts an initial time via constructor.

        Technique: Specification-based — parameterised construction.
        """
        clock = FakeClock(42.0)

        assert clock.now() == 42.0

    def test_time_can_be_updated(self) -> None:
        """Setting ``_time`` changes the value returned by ``now()``.

        Technique: State-based — mutable test double.
        """
        clock = FakeClock()
        clock._time = 99.5

        assert clock.now() == 99.5

    def test_satisfies_clock_port(self) -> None:
        """FakeClock satisfies ClockPort protocol (PEP 544).

        Technique: Protocol Conformance — runtime_checkable isinstance.
        """
        clock = FakeClock()

        assert isinstance(clock, ClockPort)

    async def test_sleep_advances_time(self) -> None:
        """sleep() advances internal time by the requested duration.

        Technique: Specification-based — sleep contract.
        """
        clock = FakeClock()

        await clock.sleep(5.0)

        assert clock.now() == 5.0

    async def test_sleep_advances_time_cumulatively(self) -> None:
        """Multiple sleep() calls accumulate time.

        Technique: State-based — cumulative advancement.
        """
        clock = FakeClock(10.0)

        await clock.sleep(1.0)
        await clock.sleep(2.5)

        assert clock.now() == 13.5

    async def test_sleep_zero_does_not_change_time(self) -> None:
        """sleep(0) is a no-op for time advancement.

        Technique: Boundary Value Analysis — zero duration.
        """
        clock = FakeClock(42.0)

        await clock.sleep(0)

        assert clock.now() == 42.0


# ---------------------------------------------------------------------------
# TestMakeSettings
# ---------------------------------------------------------------------------


class TestMakeSettings:
    """make_settings: factory producing Settings without .env files."""

    def test_returns_settings_instance(self) -> None:
        """Factory returns a Settings object.

        Technique: Specification-based — return type.
        """
        result = make_settings()

        assert isinstance(result, Settings)

    def test_defaults_mqtt_host_localhost(self) -> None:
        """Default Settings has mqtt.host == 'localhost'.

        Technique: Specification-based — sensible defaults.
        """
        result = make_settings()

        assert result.mqtt.host == "localhost"

    def test_defaults_mqtt_port_1883(self) -> None:
        """Default Settings has mqtt.port == 1883.

        Technique: Specification-based — sensible defaults.
        """
        result = make_settings()

        assert result.mqtt.port == 1883

    def test_accepts_overrides(self) -> None:
        """Keyword overrides are forwarded to the Settings constructor.

        Technique: Specification-based — override mechanism.
        """
        custom_mqtt = MqttSettings(host="broker.test", port=8883)

        result = make_settings(mqtt=custom_mqtt)

        assert result.mqtt.host == "broker.test"
        assert result.mqtt.port == 8883

    def test_ignores_ambient_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ambient env vars like MQTT__HOST do not leak into settings.

        Technique: Fault Injection — inject a misleading env var and
        verify the factory ignores it.
        """
        monkeypatch.setenv("MQTT__HOST", "from-env.example.com")

        result = make_settings()

        assert result.mqtt.host == "localhost"

    def test_ignores_nested_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nested env vars (e.g. LOGGING__LEVEL) are also ignored.

        Technique: Fault Injection — verify all env sources are stripped.
        """
        monkeypatch.setenv("LOGGING__LEVEL", "CRITICAL")

        result = make_settings()

        assert result.logging.level == "INFO"

    def test_overrides_win_over_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit overrides take precedence even when env vars are set.

        Technique: Fault Injection + Specification-based — combines an
        ambient env var with an explicit override to verify the override
        wins and the env var is ignored.
        """
        monkeypatch.setenv("MQTT__HOST", "from-env.example.com")
        custom_mqtt = MqttSettings(host="explicit.test")

        result = make_settings(mqtt=custom_mqtt)

        assert result.mqtt.host == "explicit.test"


# ---------------------------------------------------------------------------
# TestReExports — identity checks
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exported symbols are the same objects as the private originals."""

    def test_mock_mqtt_client_identity(self) -> None:
        """MockMqttClient from cosalette.testing is cosalette._mqtt.MockMqttClient.

        Technique: Identity Testing — ``is`` check.
        """
        assert MockMqttClient is _mqtt_mod.MockMqttClient

    def test_null_mqtt_client_identity(self) -> None:
        """NullMqttClient from cosalette.testing is cosalette._mqtt.NullMqttClient.

        Technique: Identity Testing — ``is`` check.
        """
        assert NullMqttClient is _mqtt_mod.NullMqttClient


# ---------------------------------------------------------------------------
# TestAppHarness
# ---------------------------------------------------------------------------


class TestAppHarness:
    """AppHarness: one-liner test setup wrapping App with test doubles."""

    def test_create_returns_harness_instance(self) -> None:
        """``create()`` returns an AppHarness instance.

        Technique: Specification-based — return type.
        """
        harness = AppHarness.create()

        assert isinstance(harness, AppHarness)

    def test_create_defaults_name_and_version(self) -> None:
        """Default harness uses name='testapp' and version='1.0.0'.

        Technique: Specification-based — default values.
        """
        harness = AppHarness.create()

        assert harness.app._name == "testapp"
        assert harness.app._version == "1.0.0"

    def test_create_custom_name_version(self) -> None:
        """Custom name and version are forwarded to App.

        Technique: Specification-based — parameterised construction.
        """
        harness = AppHarness.create(name="mybridge", version="2.3.0")

        assert harness.app._name == "mybridge"
        assert harness.app._version == "2.3.0"

    def test_create_settings_overrides(self) -> None:
        """Settings overrides are forwarded to make_settings.

        Technique: Specification-based — override mechanism.
        """
        custom_mqtt = MqttSettings(host="custom.broker", port=8883)

        harness = AppHarness.create(mqtt=custom_mqtt)

        assert harness.settings.mqtt.host == "custom.broker"
        assert harness.settings.mqtt.port == 8883

    def test_mqtt_is_mock_instance(self) -> None:
        """Harness mqtt field is a MockMqttClient.

        Technique: Specification-based — correct double type.
        """
        harness = AppHarness.create()

        assert isinstance(harness.mqtt, MockMqttClient)

    def test_clock_is_fake_instance(self) -> None:
        """Harness clock field is a FakeClock.

        Technique: Specification-based — correct double type.
        """
        harness = AppHarness.create()

        assert isinstance(harness.clock, FakeClock)

    def test_shutdown_event_initially_not_set(self) -> None:
        """Shutdown event is not set on a fresh harness.

        Technique: Specification-based — initial state.
        """
        harness = AppHarness.create()

        assert not harness.shutdown_event.is_set()

    def test_trigger_shutdown_sets_event(self) -> None:
        """``trigger_shutdown()`` sets the shutdown event.

        Technique: State-based — method side-effect.
        """
        harness = AppHarness.create()

        harness.trigger_shutdown()

        assert harness.shutdown_event.is_set()

    def test_create_dry_run_mode(self) -> None:
        """``create(dry_run=True)`` sets App dry_run flag.

        Technique: Specification-based — dry_run forwarding.
        """
        harness = AppHarness.create(dry_run=True)
        assert harness.app._dry_run is True

    async def test_run_executes_device(self) -> None:
        """``run()`` drives the App lifecycle, executing registered devices.

        Technique: Integration — verify end-to-end device execution via
        the harness's ``run()`` method.
        """
        import asyncio

        harness = AppHarness.create()
        device_called = asyncio.Event()

        @harness.app.device("probe")
        async def probe(ctx: DeviceContext) -> AsyncIterator[None]:
            device_called.set()
            harness.trigger_shutdown()
            yield

        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert device_called.is_set()


# ---------------------------------------------------------------------------
# TestPytestPlugin — plugin-registered fixtures
# ---------------------------------------------------------------------------


class TestPytestPlugin:
    """Fixtures auto-registered by cosalette.testing._plugin.

    These tests accept plugin-provided fixtures directly as parameters,
    confirming that ``conftest.py`` registration works correctly.

    Technique: Fixture Injection — verify plugin auto-registration.
    """

    def test_mock_mqtt_fixture_returns_mock(self, mock_mqtt: MockMqttClient) -> None:
        """``mock_mqtt`` fixture yields a MockMqttClient instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(mock_mqtt, MockMqttClient)

    def test_fake_clock_fixture_returns_fake(self, fake_clock: FakeClock) -> None:
        """``fake_clock`` fixture yields a FakeClock instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(fake_clock, FakeClock)

    def test_device_context_fixture_returns_context(
        self, device_context: DeviceContext
    ) -> None:
        """``device_context`` fixture yields a DeviceContext instance.

        Technique: Specification-based — return type from plugin fixture.
        """
        assert isinstance(device_context, DeviceContext)

    def test_device_context_has_test_defaults(
        self, device_context: DeviceContext
    ) -> None:
        """device_context has expected name and topic_prefix defaults.

        Technique: Specification-based — verifying default values.
        """
        assert device_context.name == "test_device"
        assert device_context._topic_prefix == "test"

    def test_device_context_uses_mock_mqtt(self, device_context: DeviceContext) -> None:
        """device_context's MQTT port is a MockMqttClient.

        Technique: Specification-based — correct double wiring.
        """
        assert isinstance(device_context._mqtt, MockMqttClient)

    def test_fixtures_are_fresh_per_test(self, mock_mqtt: MockMqttClient) -> None:
        """Each test gets a fresh MockMqttClient with empty state.

        Technique: Specification-based — per-test isolation.
        """
        assert mock_mqtt.published == []


# ---------------------------------------------------------------------------
# TestInjectStream
# ---------------------------------------------------------------------------


class TestInjectStream:
    """AppHarness.inject_stream: delivers items to stream handlers in tests."""

    async def test_injects_items_into_handler(self) -> None:
        """Items pushed via inject_stream are received by the handler.

        Technique: Specification-based — primary inject_stream contract.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        received: list[_Token] = []

        @harness.app.stream("tokens")
        async def handle(stream: Stream[_Token]) -> AsyncIterator[None]:
            async for t in stream:
                received.append(t)
                yield

        t1, t2 = _Token(), _Token()
        await harness.inject_stream("tokens", t1, t2)

        assert received == [t1, t2]

    async def test_unknown_name_raises_value_error(self) -> None:
        """ValueError is raised when no stream with the given name is registered.

        Technique: Error Guessing — unknown stream name.
        """
        harness = AppHarness.create()

        with pytest.raises(ValueError, match="No stream handler named 'missing'"):
            await harness.inject_stream("missing")

    async def test_shutdown_false_keeps_stream_open(self) -> None:
        """shutdown=False leaves the stream open; handler must exit by other means.

        Technique: Specification-based — shutdown= parameter.
        """
        import asyncio

        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        collected: list[_Token] = []

        @harness.app.stream("tok")
        async def handle(stream: Stream[_Token]) -> AsyncIterator[None]:
            async for t in stream:
                collected.append(t)
                yield

        tok = _Token()
        # shutdown=False — handler will block; use multiple yields to ensure
        # the item is consumed before cancelling
        task = asyncio.create_task(harness.inject_stream("tok", tok, shutdown=False))
        import contextlib

        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Item was still collected before cancel
        assert tok in collected

    async def test_no_items_with_shutdown_runs_handler(self) -> None:
        """inject_stream with no items + shutdown=True runs handler with empty stream.

        Technique: Boundary Value Analysis — zero items.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        ran = False

        @harness.app.stream("empty")
        async def handle(stream: Stream[_Token]) -> AsyncIterator[None]:
            nonlocal ran
            ran = True
            async for _ in stream:
                yield

        await harness.inject_stream("empty")

        assert ran


# ---------------------------------------------------------------------------
# TestInjectStreamDI — production-like DI parity
# ---------------------------------------------------------------------------


class TestInjectStreamDI:
    """inject_stream: DeviceContext, DeviceStore, adapters, and providers injection.

    Verifies that inject_stream provides production-like DI so tests can
    assert MQTT publishing and persistence without running hardware lifecycle.
    """

    async def test_device_context_injected_by_default(self) -> None:
        """inject_stream auto-provides a DeviceContext wired to harness doubles.

        Technique: Specification-based — DeviceContext default construction.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        captured: list[DeviceContext] = []

        @harness.app.stream("ctx_default")
        async def handle(stream: Stream[_Token], ctx: DeviceContext):
            captured.append(ctx)
            async for _ in stream:
                yield

        await harness.inject_stream("ctx_default", _Token())

        assert len(captured) == 1
        assert isinstance(captured[0], DeviceContext)
        assert captured[0]._mqtt is harness.mqtt

    async def test_device_context_publishes_via_harness_mqtt(self) -> None:
        """Publishing via the injected DeviceContext is observable on harness.mqtt.

        Technique: Specification-based — publish contract, observable side-effect.
        """
        harness = AppHarness.create(name="bridge")
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)

        @harness.app.stream("publisher")
        async def handle(stream: Stream[_Token], ctx: DeviceContext):
            async for _ in stream:
                await ctx.publish_state({"val": 1})
                yield

        await harness.inject_stream("publisher", _Token())

        topics = [t for t, *_ in harness.mqtt.published]
        assert any("publisher" in t for t in topics)
        payloads = [p for _, p, *_ in harness.mqtt.published]
        assert any('"val": 1' in p or '"val":1' in p for p in payloads)

    async def test_explicit_ctx_override_used(self) -> None:
        """ctx= override replaces the harness-built context.

        Technique: Specification-based — ctx= parameter forwarding.
        """
        import asyncio

        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        custom_mqtt = MockMqttClient()
        custom_ctx = DeviceContext(
            name="custom",
            settings=harness.settings,
            mqtt=custom_mqtt,
            topic_prefix="custom",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=harness.clock,
        )
        captured: list[DeviceContext] = []

        @harness.app.stream("explicit_ctx")
        async def handle(stream: Stream[_Token], ctx: DeviceContext):
            captured.append(ctx)
            async for _ in stream:
                yield

        await harness.inject_stream("explicit_ctx", _Token(), ctx=custom_ctx)

        assert captured[0] is custom_ctx

    async def test_auto_device_store_from_app_store(self) -> None:
        """DeviceStore is auto-created from app._store and saved after the handler.

        Technique: Specification-based — auto-persistence from app._store.
        """
        mem_store = MemoryStore()
        harness = AppHarness.create(store=mem_store)
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)

        @harness.app.stream("persisted")
        async def handle(stream: Stream[_Token], store: DeviceStore):
            async for _ in stream:
                store["count"] = 42
                yield

        await harness.inject_stream("persisted", _Token())

        saved = mem_store.load("persisted")
        assert saved == {"count": 42}

    async def test_explicit_store_override(self) -> None:
        """store= backend is used for DeviceStore creation, saved after handler.

        Technique: Specification-based — store= parameter forwarding.
        """
        mem_store = MemoryStore()
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)

        @harness.app.stream("explicit_store")
        async def handle(stream: Stream[_Token], store: DeviceStore):
            async for _ in stream:
                store["done"] = True
                yield

        await harness.inject_stream("explicit_store", _Token(), store=mem_store)

        saved = mem_store.load("explicit_store")
        assert saved == {"done": True}

    async def test_extra_providers_injected(self) -> None:
        """providers= dict is merged with highest priority into DI map.

        Technique: Specification-based — providers= parameter forwarding.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        tag = _Tag()
        received: list[_Tag] = []

        @harness.app.stream("tagged")
        async def handle(stream: Stream[_Token], t: _Tag) -> AsyncIterator[None]:
            received.append(t)
            async for _ in stream:
                yield

        await harness.inject_stream("tagged", _Token(), providers={_Tag: tag})

        assert received == [tag]

    async def test_adapters_injected_by_concrete_type(self) -> None:
        """adapters= injects concrete instances by type for non-lifecycle use.

        Technique: Specification-based — adapters= parameter forwarding,
        mirrors production run_stream type(_port): _port injection.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        port_instance = _NoOpStreamPort()
        received: list[_NoOpStreamPort] = []

        @harness.app.stream("adapter_test")
        async def handle(stream: Stream[_Token], port: _NoOpStreamPort):
            received.append(port)
            async for _ in stream:
                yield

        await harness.inject_stream(
            "adapter_test", _Token(), adapters={_NoOpStreamPort: port_instance}
        )

        assert received == [port_instance]

    async def test_no_app_store_no_device_store_injected(self) -> None:
        """Without app._store or store=, no DeviceStore is injected.

        Technique: Boundary Value Analysis — no-store path stays clean.
        """
        harness = AppHarness.create()
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)
        ran = False

        @harness.app.stream("no_store")
        async def handle(stream: Stream[_Token]) -> AsyncIterator[None]:
            nonlocal ran
            ran = True
            async for _ in stream:
                yield

        # Must not raise even though no DeviceStore is in providers
        await harness.inject_stream("no_store")

        assert ran

    async def test_handler_requesting_device_store_without_backend_raises(self) -> None:
        """TypeError with clear message when handler needs DeviceStore but
        none configured.

        Technique: Error Guessing — missing DeviceStore in providers map.
        """
        harness = AppHarness.create()  # no store= configured
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)

        @harness.app.stream("needs_store")
        async def handle(stream: Stream[_Token], store: DeviceStore):
            async for _ in stream:
                yield

        with pytest.raises(TypeError, match="DeviceStore"):
            await harness.inject_stream("needs_store", _Token())

    async def test_store_saved_on_handler_exception(self) -> None:
        """DeviceStore is saved in the finally block even when the handler raises.

        Technique: Error Guessing — ensures the try/finally in inject_stream
        covers the async_save_store_on_shutdown call, mirroring the equivalent
        run_stream test (test_store_saved_on_handler_exit_via_exception).
        """
        mem_store = MemoryStore()
        harness = AppHarness.create(store=mem_store)
        harness.app.adapter(StreamablePort[_Token], _NoOpStreamPort)

        @harness.app.stream("fault_stream")
        async def handle(
            stream: Stream[_Token], store: DeviceStore
        ) -> AsyncIterator[None]:
            async for _ in stream:
                store["written_before_raise"] = True
                raise RuntimeError("handler fault")
                yield  # unreachable — needed to make this an async generator

        with pytest.raises(RuntimeError, match="handler fault"):
            await harness.inject_stream("fault_stream", _Token())

        saved = mem_store.load("fault_stream")
        assert saved is not None
        assert saved.get("written_before_raise") is True


# ---------------------------------------------------------------------------
# TestAppHarnessConvenience testing API additions
# ---------------------------------------------------------------------------


class TestAppHarnessConvenience:
    """AppHarness convenience methods for publish assertions and commands."""

    async def test_published_returns_mqtt_list(self) -> None:
        """published() returns a snapshot of the MockMqttClient.published list.

        Technique: Specification-based — convenience accessor returns same content.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"value": 42})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Framework publishes availability, registry, status, and device state
        snapshot = harness.published()
        assert snapshot == harness.mqtt.published
        assert len(snapshot) > 0

    async def test_messages_for_filters_by_topic(self) -> None:
        """messages_for(topic) returns (payload, retain, qos) tuples.

        Technique: Specification-based — delegates to MockMqttClient.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"a": 1})
            await ctx.publish_state({"b": 2})
            harness.trigger_shutdown()
            yield

        await harness.run()

        messages = harness.messages_for("testapp/sensor/state")
        assert len(messages) == 2
        payload1, retain1, qos1 = messages[0]
        assert "a" in payload1
        assert retain1 is True  # State messages are retained
        assert qos1 == 1

    async def test_last_published_returns_most_recent(self) -> None:
        """last_published() returns the final tuple or None.

        Technique: Specification-based — most recent publish.
        """
        harness = AppHarness.create()

        assert harness.last_published() is None

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"seq": 1})
            await ctx.publish_state({"seq": 2})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Last message is the final framework status or sensor state
        last = harness.last_published()
        assert last is not None
        topic, payload, retain, qos = last
        assert topic.startswith("testapp/")

    async def test_assert_published_raises_when_no_messages(self) -> None:
        """assert_published() raises when topic has zero messages.

        Technique: Specification-based — assertion helper validation.
        """
        harness = AppHarness.create()

        with pytest.raises(AssertionError, match="No messages published"):
            harness.assert_published("nonexistent/topic")

    async def test_assert_published_validates_count(self) -> None:
        """assert_published(count=N) raises if message count != N.

        Technique: Specification-based — count validation.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"value": 42})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Correct count passes
        harness.assert_published("testapp/sensor/state", count=1)

        # Wrong count raises
        with pytest.raises(AssertionError, match="Expected 2 message"):
            harness.assert_published("testapp/sensor/state", count=2)

    async def test_assert_published_validates_contains(self) -> None:
        """assert_published(contains=...) raises if substring not found.

        Technique: Specification-based — substring validation.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temp": 22.5})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Found substring passes
        harness.assert_published("testapp/sensor/state", contains="temp")

        # Missing substring raises
        with pytest.raises(AssertionError, match="contains"):
            harness.assert_published("testapp/sensor/state", contains="humidity")

    async def test_inject_command_calls_mqtt_deliver(self) -> None:
        """inject_command() constructs /set topic and calls mqtt.deliver().

        Technique: Specification-based — command topic construction and delivery.
        """
        harness = AppHarness.create()
        command_callbacks: list[tuple[str, str]] = []

        # Register callback to capture the deliver call
        async def on_msg(topic: str, payload: str) -> None:
            command_callbacks.append((topic, payload))

        harness.mqtt.on_message(on_msg)

        await harness.inject_command("fan", "ON")

        assert command_callbacks == [("testapp/fan/set", "ON")]

    async def test_inject_command_root_device(self) -> None:
        """inject_command(None, payload) constructs {prefix}/set for root commands.

        Technique: Specification-based — root command topic construction.
        """
        harness = AppHarness.create()
        command_callbacks: list[tuple[str, str]] = []

        async def on_msg(topic: str, payload: str) -> None:
            command_callbacks.append((topic, payload))

        harness.mqtt.on_message(on_msg)

        await harness.inject_command(None, '{"action": "reboot"}')

        assert command_callbacks == [("testapp/set", '{"action": "reboot"}')]

    async def test_inject_command_explicit_topic(self) -> None:
        """inject_command(topic=...) uses the explicit topic override.

        Technique: Specification-based — topic override parameter.
        """
        harness = AppHarness.create()
        command_callbacks: list[tuple[str, str]] = []

        async def on_msg(topic: str, payload: str) -> None:
            command_callbacks.append((topic, payload))

        harness.mqtt.on_message(on_msg)

        await harness.inject_command("fan", "OFF", topic="custom/topic/set")

        assert command_callbacks == [("custom/topic/set", "OFF")]

    async def test_call_command_invokes_handler(self) -> None:
        """call_command() directly invokes the registered handler.

        Technique: Specification-based — direct handler invocation.
        """
        harness = AppHarness.create()
        handler_called = False
        received_payload: str | None = None

        @harness.app.command("light")
        async def light_cmd(payload: str) -> dict[str, object]:
            nonlocal handler_called, received_payload
            handler_called = True
            received_payload = payload
            return {"state": "on"}

        await harness.call_command("light", '{"brightness": 75}')

        assert handler_called
        assert received_payload == '{"brightness": 75}'

    async def test_call_command_publishes_returned_state(self) -> None:
        """call_command() publishes the handler's return value to mqtt.

        Technique: Specification-based — state publishing after command.
        """
        harness = AppHarness.create()

        @harness.app.command("fan")
        async def fan_cmd(payload: str) -> dict[str, object]:
            return {"speed": 3, "mode": "auto"}

        await harness.call_command("fan", '{"action": "speed_up"}')

        messages = harness.messages_for("testapp/fan/state")
        assert len(messages) > 0
        payload_str, _, _ = messages[0]

        payload_dict = json.loads(payload_str)
        assert payload_dict["speed"] == 3
        assert payload_dict["mode"] == "auto"

    async def test_call_command_dict_payload(self) -> None:
        """call_command() accepts dict payloads and serializes them.

        Technique: Specification-based — dict payload convenience.
        """
        harness = AppHarness.create()
        received_payload: str | None = None

        @harness.app.command("sensor")
        async def sensor_cmd(payload: str) -> None:
            nonlocal received_payload
            received_payload = payload

        await harness.call_command("sensor", {"calibrate": True, "offset": 2.5})

        assert received_payload is not None
        parsed = json.loads(received_payload)
        assert parsed["calibrate"] is True
        assert parsed["offset"] == 2.5

    async def test_call_command_typed_pydantic_payload(self) -> None:
        """call_command() works with typed Pydantic payloads.

        Uses ``Annotated[T, Payload()]`` for type-safe command binding.

        Technique: Specification-based — typed command handler binding.
        """
        harness = AppHarness.create()
        received_cmd: _FanCommand | None = None

        @harness.app.command("fan")
        async def fan_cmd(cmd: Annotated[_FanCommand, Payload()]) -> dict[str, object]:
            nonlocal received_cmd
            received_cmd = cmd
            return {
                "speed": cmd.speed,
                "timer": cmd.timer,
            }

        await harness.call_command("fan", {"speed": 5, "timer": 60})

        assert received_cmd is not None
        assert isinstance(received_cmd, _FanCommand)
        assert received_cmd.speed == 5
        assert received_cmd.timer == 60

        messages = harness.messages_for("testapp/fan/state")

        payload_str, _, _ = messages[0]
        payload_dict = json.loads(payload_str)
        assert payload_dict["speed"] == 5
        assert payload_dict["timer"] == 60

    async def test_call_command_unknown_name_raises(self) -> None:
        """call_command() raises ValueError for unknown command names.

        Technique: Error Guessing — unknown command name.
        """
        harness = AppHarness.create()

        with pytest.raises(ValueError, match="No command handler named 'missing'"):
            await harness.call_command("missing", "{}")

    async def test_call_command_root_device(self) -> None:
        """call_command() invokes root command handlers.

        Technique: Specification-based — root command support.
        """
        harness = AppHarness.create()
        root_called = False

        @harness.app.command(None)  # Use None for root, not empty string
        async def root_cmd(payload: str) -> dict[str, object]:
            nonlocal root_called
            root_called = True
            return {"status": "rebooting"}

        # Pass the function name for root commands
        await harness.call_command("root_cmd", '{"action": "reboot"}')

        assert root_called
        messages = harness.messages_for("testapp/state")
        assert len(messages) > 0

    async def test_advance_time_delegates_to_clock(self) -> None:
        """advance_time() is convenience wrapper over clock.sleep().

        Technique: Specification-based — clock wrapper.
        """
        harness = AppHarness.create()

        assert harness.clock.now() == 0.0

        await harness.advance_time(5.0)

        assert harness.clock.now() == 5.0

    async def test_advance_time_cumulative(self) -> None:
        """advance_time() can be called multiple times to accumulate time.

        Technique: Specification-based — cumulative time advancement.
        """
        harness = AppHarness.create()

        await harness.advance_time(10.0)
        await harness.advance_time(5.0)

        assert harness.clock.now() == 15.0

    def test_messages_for_unknown_topic_returns_empty_list(self) -> None:
        """messages_for() returns empty list for a topic with no publishes.

        Technique: Boundary Value Analysis — zero-message edge case.
        """
        harness = AppHarness.create()

        result = harness.messages_for("never/published")

        assert result == []

    async def test_assert_published_count_and_contains_combined(self) -> None:
        """assert_published() validates count and contains simultaneously.

        Technique: Specification-based — combined validator behaviour.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temp": 22.5})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Both validators pass
        harness.assert_published("testapp/sensor/state", count=1, contains="temp")

        # Correct count but wrong contains raises
        with pytest.raises(AssertionError, match="contains"):
            harness.assert_published(
                "testapp/sensor/state", count=1, contains="humidity"
            )

        # Wrong count but correct contains raises
        with pytest.raises(AssertionError, match="Expected 2"):
            harness.assert_published("testapp/sensor/state", count=2, contains="temp")

    async def test_call_command_handler_exception_publishes_error(self) -> None:
        """call_command() catches handler exceptions and publishes them as errors.

        CommandRunner catches exceptions internally and publishes an error
        payload to the MQTT broker; exceptions are not re-raised to callers.

        Technique: Error Guessing — exception handling path.
        """
        harness = AppHarness.create()

        @harness.app.command("boom")
        async def boom_cmd(payload: str) -> None:
            raise RuntimeError("handler blew up")

        # Should not raise — CommandRunner handles the exception internally
        await harness.call_command("boom", "{}")

        # Error is published to the error topic
        error_msgs = [
            t for t, *_ in harness.mqtt.published if "error" in t or "boom" in t
        ]
        assert len(error_msgs) > 0

    async def test_call_command_router_prefixed_name(self) -> None:
        """call_command() resolves router-prefixed command names correctly.

        A router included with a prefix creates command registrations whose
        ``name`` is ``"{prefix}/{cmd_name}"``.  ``call_command`` must find
        the registration by this combined name.

        Technique: Specification-based — router-prefixed command support.
        """
        import cosalette

        harness = AppHarness.create(name="testapp")
        router = cosalette.Router(prefix="env")
        handler_called = False

        @router.command("fan")
        async def fan_cmd(payload: str) -> dict[str, object]:
            nonlocal handler_called
            handler_called = True
            return {"state": "on"}

        harness.app.include_router(router)

        await harness.call_command("env/fan", '{"speed": 3}')

        assert handler_called
        harness.assert_published("testapp/env/fan/state", contains="on")

    def test_published_returns_snapshot_not_alias(self) -> None:
        """published() returns a copy; mutating it does not affect mqtt state.

        Technique: Specification-based — snapshot semantics of published().
        """
        harness = AppHarness.create()
        snapshot = harness.published()

        # Mutating the snapshot must not corrupt the MockMqttClient list
        snapshot.append(("fake/topic", "fake", False, 0))  # type: ignore[arg-type]

        assert len(harness.mqtt.published) == 0


# ---------------------------------------------------------------------------
# TestAssertState
# ---------------------------------------------------------------------------


class TestAssertState:
    """AppHarness.assert_state: retained JSON subset assertions.

    Test Techniques Used:
        - Specification-based Testing: assert_state contract and semantics.
        - Equivalence Partitioning: retained vs non-retained, match vs no-match.
        - Decision Table: combinations of subset match and retain flag.
        - Boundary Value Analysis: empty expected dict, exact key mismatch.
        - Error Guessing: no messages, wrong count, non-retained match.
    """

    async def test_passes_on_retained_json_message_matching_subset(self) -> None:
        """assert_state passes when retained message payload contains expected keys.

        Technique: Specification-based — primary happy-path contract.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temp": 22, "humidity": 60})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Should not raise
        harness.assert_state("testapp/sensor/state", {"temp": 22})

    async def test_passes_with_empty_expected_on_any_retained_json(self) -> None:
        """assert_state({}) passes on any retained JSON message.

        Technique: Boundary Value Analysis — empty expected dict.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"x": 1})
            harness.trigger_shutdown()
            yield

        await harness.run()

        harness.assert_state("testapp/sensor/state", {})

    def test_fails_when_no_messages(self) -> None:
        """assert_state raises AssertionError when topic has no messages.

        Technique: Error Guessing — no-message edge case.
        """
        harness = AppHarness.create()

        with pytest.raises(AssertionError, match="No messages published"):
            harness.assert_state("nonexistent/topic", {})

    async def test_fails_when_key_missing(self) -> None:
        """assert_state raises when expected key is not in any payload.

        Technique: Equivalence Partitioning — missing key.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temp": 22})
            harness.trigger_shutdown()
            yield

        await harness.run()

        with pytest.raises(AssertionError, match="No message on"):
            harness.assert_state("testapp/sensor/state", {"humidity": 60})

    async def test_fails_when_value_mismatch(self) -> None:
        """assert_state raises when expected value differs from actual.

        Technique: Equivalence Partitioning — value mismatch.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"x": 1})
            harness.trigger_shutdown()
            yield

        await harness.run()

        with pytest.raises(AssertionError, match="No message on"):
            harness.assert_state("testapp/sensor/state", {"x": 99})

    async def test_fails_when_matching_message_not_retained(self) -> None:
        """assert_state raises when subset matches but message is not retained.

        Technique: Decision Table — subset match + not retained → fail.
        """
        harness = AppHarness.create()
        # Publish a non-retained message directly via mqtt
        await harness.mqtt.publish("test/topic", '{"key": "value"}', retain=False)

        with pytest.raises(AssertionError, match="not retained"):
            harness.assert_state("test/topic", {"key": "value"})

    async def test_deep_subset_matches_nested_dict(self) -> None:
        """assert_state passes when nested expected dict is a subset of actual.

        Technique: Specification-based — deep recursive subset matching.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"a": {"b": 2, "c": 3}})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Deep subset: {a: {b: 2}} is a subset of {a: {b: 2, c: 3}}
        harness.assert_state("testapp/sensor/state", {"a": {"b": 2}})

    async def test_deep_subset_fails_on_nested_value_mismatch(self) -> None:
        """assert_state fails when nested expected value does not match.

        Technique: Specification-based — deep recursive subset, wrong value.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"a": {"b": 2, "c": 3}})
            harness.trigger_shutdown()
            yield

        await harness.run()

        with pytest.raises(AssertionError, match="No message on"):
            harness.assert_state("testapp/sensor/state", {"a": {"b": 99}})

    async def test_count_param_enforces_exact_message_count(self) -> None:
        """assert_state(count=N) raises when message count != N.

        Technique: Boundary Value Analysis — exact count enforcement.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"v": 1})
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Correct count passes
        harness.assert_state("testapp/sensor/state", {"v": 1}, count=1)

        # Wrong count raises
        with pytest.raises(AssertionError, match="Expected 2 message"):
            harness.assert_state("testapp/sensor/state", {"v": 1}, count=2)

    async def test_retained_after_non_retained_same_topic_passes(self) -> None:
        """Non-retained then retained matching message: assert_state passes.

        A non-retained message on the same topic must not cause a false
        failure when a retained match follows.

        Technique: Decision Table — non-retained match precedes retained match.
        """
        harness = AppHarness.create()
        await harness.mqtt.publish("test/topic", '{"x": 1}', retain=False)
        await harness.mqtt.publish("test/topic", '{"x": 1}', retain=True)

        harness.assert_state("test/topic", {"x": 1})
        harness.assert_state("test/topic", {})

    async def test_only_non_retained_matching_raises_not_retained(self) -> None:
        """Only a non-retained subset match → AssertionError mentions 'not retained'.

        Technique: Decision Table — subset match exists but no retained match.
        """
        harness = AppHarness.create()
        await harness.mqtt.publish("test/topic", '{"x": 1}', retain=False)

        with pytest.raises(AssertionError, match="not retained"):
            harness.assert_state("test/topic", {"x": 1})

    async def test_non_json_payload_skipped_valid_match_found(self) -> None:
        """Non-JSON retained payload is skipped; valid retained JSON match succeeds.

        Technique: Error Guessing — undecodable payload on same topic as valid match.
        """
        harness = AppHarness.create()
        await harness.mqtt.publish("test/topic", "not json", retain=True)
        await harness.mqtt.publish("test/topic", '{"z": 99}', retain=True)

        harness.assert_state("test/topic", {"z": 99})

    async def test_non_dict_json_payload_raises_no_message_error(self) -> None:
        """Topic with only a JSON array raises 'no message' error, not TypeError.

        Technique: Error Guessing — JSON array is not a dict; must not crash.
        """
        harness = AppHarness.create()
        await harness.mqtt.publish("test/topic", "[1, 2, 3]", retain=True)

        with pytest.raises(AssertionError, match="No message on"):
            harness.assert_state("test/topic", {})

    async def test_all_skipped_payloads_error_differs_from_empty_topic(
        self,
    ) -> None:
        """assert_state error differs when payloads skipped vs no messages.

        Technique: Error Guessing — diagnostic message accuracy when all published
        payloads fail the JSON-object guard (non-JSON text, JSON arrays, JSON scalars).
        """
        harness = AppHarness.create()
        await harness.mqtt.publish("testapp/probe/state", "online", retain=True, qos=1)
        await harness.mqtt.publish(
            "testapp/probe/state", "[1, 2, 3]", retain=True, qos=1
        )
        await harness.mqtt.publish(
            "testapp/probe/state", "not json", retain=True, qos=1
        )

        with pytest.raises(AssertionError) as exc_info:
            harness.assert_state("testapp/probe/state", {})

        msg = str(exc_info.value)
        assert "No messages published to" not in msg, (
            "Error should not claim topic is empty when messages exist"
        )
        assert "skipped" in msg, "Error should mention skipped non-JSON-object messages"

    async def test_all_non_json_object_payloads_error_mentions_skip_count(
        self,
    ) -> None:
        """assert_state error mentions skipped count for non-JSON-object payloads.

        Disambiguates 'no messages' from 'messages exist but none parse
        as JSON objects'.

        Technique: Error Guessing — see above.
        """
        harness = AppHarness.create()

        # Publish 3 plain-text messages (not valid JSON objects) — retained
        await harness.mqtt.publish("testapp/sensor/raw", "online", retain=True, qos=0)
        await harness.mqtt.publish("testapp/sensor/raw", "offline", retain=True, qos=0)
        await harness.mqtt.publish(
            "testapp/sensor/raw", "[1, 2, 3]", retain=True, qos=0
        )

        with pytest.raises(AssertionError) as exc_info:
            harness.assert_state("testapp/sensor/raw", {})

        error_msg = str(exc_info.value)
        # Must NOT say "No messages published" (messages DO exist)
        assert "No messages published" not in error_msg
        # Must mention that messages were skipped
        assert "skipped" in error_msg


# ---------------------------------------------------------------------------
# TestAssertSubscribed
# ---------------------------------------------------------------------------


class TestAssertSubscribed:
    """AppHarness.assert_subscribed: subscription presence assertions.

    Test Techniques Used:
        - Specification-based Testing: passes when subscribed, fails when not.
        - Error Guessing: clear AssertionError message listing actual subscriptions.
    """

    async def test_passes_when_topic_subscribed(self) -> None:
        """assert_subscribed passes when the app subscribes to the expected topic.

        Technique: Specification-based — primary happy-path; asserts on a deterministic
        topic derived from a registered command rather than on subscriptions[0].
        """
        harness = AppHarness.create()

        @harness.app.command("probe")
        async def probe_cmd(payload: str) -> None:
            pass  # handler not invoked; we only need the subscription registered

        @harness.app.device("_trigger")
        async def _trigger_shutdown(ctx: DeviceContext) -> AsyncIterator[None]:
            harness.trigger_shutdown()
            yield

        await harness.run()

        # Should not raise — "testapp/probe/set" is deterministically registered
        harness.assert_subscribed("testapp/probe/set")

    def test_fails_when_topic_not_subscribed(self) -> None:
        """assert_subscribed raises AssertionError for unknown topics.

        Technique: Error Guessing — clear error with actual subscriptions listed.
        """
        harness = AppHarness.create()

        with pytest.raises(AssertionError, match="not subscribed"):
            harness.assert_subscribed("never/subscribed/topic")

    def test_fail_message_lists_actual_subscriptions(self) -> None:
        """AssertionError message includes the actual subscriptions list.

        Technique: Specification-based — error message content.
        """
        harness = AppHarness.create()
        # Pre-populate a subscription so the list is non-empty in the error
        harness.mqtt.subscriptions.append("some/topic")

        with pytest.raises(AssertionError, match="some/topic"):
            harness.assert_subscribed("other/topic")


# ---------------------------------------------------------------------------
# TestInjectCommandDict
# ---------------------------------------------------------------------------


class TestInjectCommandDict:
    """AppHarness.inject_command: accepts str | dict payload.

    Test Techniques Used:
        - Specification-based Testing: dict payload serialized to JSON string.
        - Equivalence Partitioning: dict vs str payload paths.
        - Round-trip Testing: inject_command(dict) → assert_state matches fields.
    """

    async def test_dict_payload_delivered_as_json_string(self) -> None:
        """inject_command with dict payload serializes to JSON before delivery.

        Technique: Specification-based — dict path.
        """
        harness = AppHarness.create()
        received: list[str] = []

        async def on_msg(topic: str, payload: str) -> None:
            received.append(payload)

        harness.mqtt.on_message(on_msg)

        await harness.inject_command("fan", {"state": "on", "speed": 3})

        assert len(received) == 1
        parsed = json.loads(received[0])
        assert parsed["state"] == "on"
        assert parsed["speed"] == 3

    async def test_str_payload_path_unchanged(self) -> None:
        """inject_command with str payload delivers the string as-is.

        Technique: Equivalence Partitioning — str path unchanged.
        """
        harness = AppHarness.create()
        received: list[str] = []

        async def on_msg(topic: str, payload: str) -> None:
            received.append(payload)

        harness.mqtt.on_message(on_msg)

        await harness.inject_command("fan", "plain_string")

        assert received == ["plain_string"]

    async def test_round_trip_inject_command_dict_assert_state(self) -> None:
        """inject_command(dict) round-trips through the app to assert_state.

        Technique: Round-trip Testing — dict serialized, delivered, processed,
        and state published can be verified with assert_state.
        """
        harness = AppHarness.create()
        published_state: dict[str, Any] = {}

        @harness.app.command("lamp")
        async def lamp_cmd(payload: str) -> dict[str, object]:
            cmd = json.loads(payload)
            published_state.update(cmd)
            return {"brightness": cmd.get("brightness", 0), "on": True}

        await harness.call_command("lamp", {"brightness": 80})

        harness.assert_state("testapp/lamp/state", {"brightness": 80, "on": True})


# ---------------------------------------------------------------------------
# TestAssertDiscoveryTopicsPublished
# ---------------------------------------------------------------------------


class TestAssertDiscoveryTopicsPublished:
    """assert_discovery_topics_published: discovery↔runtime topic cross-check.

    Extracted per Proposal F23 from the test five of cosalette-apps' apps
    hand-rolled independently to catch the velux2mqtt phantom-entity class
    (ADR-051) — a well-formed discovery payload whose ``state_topic`` no
    runtime publish ever uses.

    Test Techniques Used:
        - Specification-based Testing: primary contract (pass/fail).
        - Equivalence Partitioning: state_topic present vs. absent (command-only).
        - Boundary Value Analysis: empty payload list.
        - Error Guessing: multiple missing topics, message content.
    """

    async def test_passes_when_all_state_topics_published(self) -> None:
        """No error when every payload's state_topic was actually published.

        Technique: Specification-based — primary happy-path contract.
        """
        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temp": 22})
            harness.trigger_shutdown()
            yield

        await harness.run()

        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/sensor/testapp/temp/config",
                config={"state_topic": "testapp/sensor/state"},
            )
        ]

        assert_discovery_topics_published(harness, payloads)

    def test_raises_when_state_topic_never_published(self) -> None:
        """AssertionError when a payload's state_topic has no runtime publish.

        This is the velux2mqtt phantom-entity class (ADR-051): a well-formed
        payload pointing at a topic nothing ever publishes.

        Technique: Specification-based — primary failure contract.
        """
        harness = AppHarness.create()
        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/cover/testapp/position/config",
                config={"state_topic": "testapp/cover_device/state"},
            )
        ]

        with pytest.raises(AssertionError, match="never published at runtime"):
            assert_discovery_topics_published(harness, payloads)

    def test_error_message_lists_missing_and_published_topics(self) -> None:
        """AssertionError message names the missing topic and what was published.

        Technique: Error Guessing — diagnostic message content.
        """
        harness = AppHarness.create()
        harness.mqtt.published.append(("testapp/sensor/state", '{"temp": 1}', True, 1))
        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/sensor/testapp/missing/config",
                config={"state_topic": "testapp/missing/state"},
            )
        ]

        with pytest.raises(AssertionError) as exc_info:
            assert_discovery_topics_published(harness, payloads)

        msg = str(exc_info.value)
        assert "testapp/missing/state" in msg
        assert "testapp/sensor/state" in msg

    def test_payload_without_state_topic_is_skipped(self) -> None:
        """Command-only payloads (no state_topic) are not cross-checked.

        A receive-only channel emits a payload with only command_topic —
        there is nothing to cross-check against runtime publishes.

        Technique: Equivalence Partitioning — state_topic absent.
        """
        harness = AppHarness.create()
        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/switch/testapp/relay/config",
                config={"command_topic": "testapp/relay/set"},
            )
        ]

        assert_discovery_topics_published(harness, payloads)

    def test_empty_payload_list_passes(self) -> None:
        """No payloads means nothing to check — never raises.

        Technique: Boundary Value Analysis — empty input.
        """
        harness = AppHarness.create()

        assert_discovery_topics_published(harness, [])

    def test_multiple_missing_topics_all_reported(self) -> None:
        """Every missing state_topic is named, not just the first.

        Technique: Error Guessing — multiple simultaneous failures.
        """
        harness = AppHarness.create()
        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/sensor/testapp/a/config",
                config={"state_topic": "testapp/a/state"},
            ),
            HaDiscoveryPayload(
                topic="homeassistant/sensor/testapp/b/config",
                config={"state_topic": "testapp/b/state"},
            ),
        ]

        with pytest.raises(AssertionError) as exc_info:
            assert_discovery_topics_published(harness, payloads)

        msg = str(exc_info.value)
        assert "testapp/a/state" in msg
        assert "testapp/b/state" in msg
