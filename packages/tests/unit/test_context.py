"""Unit tests for cosalette._context — device and application contexts.

Test Techniques Used:
    - Specification-based Testing: Property accessors, publish topics,
      JSON serialisation, adapter resolution
    - State-based Testing: MockMqttClient records publish calls
    - Async Behaviour Testing: Shutdown-aware sleep, early return
    - Protocol Conformance: Adapter resolution with typed protocols
    - Error Condition Testing: Duplicate handler, missing adapter,
      malformed import paths
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cosalette._clock import ClockPort
from cosalette._context import AppContext, DeviceContext, _import_string
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx_parts() -> dict[str, Any]:
    """Common parts for building a DeviceContext."""
    return {
        "name": "blind",
        "settings": make_settings(),
        "mqtt": MockMqttClient(),
        "topic_prefix": "myapp",
        "shutdown_event": asyncio.Event(),
        "adapters": {},
        "clock": FakeClock(),
    }


@pytest.fixture
def ctx(ctx_parts: dict[str, Any]) -> DeviceContext:
    """DeviceContext with standard test configuration."""
    return DeviceContext(**ctx_parts)


# ---------------------------------------------------------------------------
# DeviceContext — Properties
# ---------------------------------------------------------------------------


class TestDeviceContextProperties:
    """Tests for DeviceContext read-only properties.

    Technique: Specification-based Testing — verifying public
    contract of property accessors.
    """

    def test_name_returns_registered_name(self, ctx: DeviceContext) -> None:
        """name property returns the device name passed at construction."""
        assert ctx.name == "blind"

    def test_settings_returns_injected_settings(self, ctx: DeviceContext) -> None:
        """settings property returns the Settings instance."""
        assert isinstance(ctx.settings, Settings)

    def test_clock_returns_injected_clock(self, ctx: DeviceContext) -> None:
        """clock property returns the injected ClockPort."""
        clock = ctx.clock
        assert isinstance(clock, ClockPort)
        assert clock.now() == 0.0

    def test_shutdown_requested_false_initially(self, ctx: DeviceContext) -> None:
        """shutdown_requested is False when event has not been set."""
        assert ctx.shutdown_requested is False

    def test_shutdown_requested_true_after_event_set(self, ctx_parts: dict) -> None:
        """shutdown_requested is True after shutdown event is set."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)
        assert ctx.shutdown_requested is True

    def test_command_handler_none_initially(self, ctx: DeviceContext) -> None:
        """command_handler is None before any handler is registered."""
        assert ctx.command_handler is None


# ---------------------------------------------------------------------------
# DeviceContext — publish_state
# ---------------------------------------------------------------------------


class TestPublishState:
    """Tests for DeviceContext.publish_state().

    Technique: State-based Testing — MockMqttClient records
    published messages for assertion.
    """

    async def test_publishes_json_to_state_topic(self, ctx_parts: dict) -> None:
        """publish_state() sends JSON to {prefix}/{device}/state."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        await ctx.publish_state({"temperature": 22.5})

        assert len(mqtt.published) == 1
        topic, payload, retain, qos = mqtt.published[0]
        assert topic == "myapp/blind/state"
        assert json.loads(payload) == {"temperature": 22.5}
        assert retain is True
        assert qos == 1

    async def test_retain_false_override(self, ctx_parts: dict) -> None:
        """retain=False overrides the default retain=True."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        await ctx.publish_state({"status": "ok"}, retain=False)

        _, _, retain, _ = mqtt.published[0]
        assert retain is False

    async def test_payload_is_json_serialised(self, ctx_parts: dict) -> None:
        """Complex payloads are JSON-serialised correctly."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        payload = {"nested": {"key": [1, 2, 3]}, "flag": True}
        await ctx.publish_state(payload)

        _, raw, _, _ = mqtt.published[0]
        assert json.loads(raw) == payload


# ---------------------------------------------------------------------------
# DeviceContext — publish (arbitrary channel)
# ---------------------------------------------------------------------------


class TestPublish:
    """Tests for DeviceContext.publish() arbitrary channel method.

    Technique: Specification-based Testing — verifying topic
    construction, retain, and QoS pass-through.
    """

    async def test_publishes_to_channel_topic(self, ctx_parts: dict) -> None:
        """publish() sends to {prefix}/{device}/{channel}."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        await ctx.publish("debug", "hello")

        assert len(mqtt.published) == 1
        topic, payload, retain, qos = mqtt.published[0]
        assert topic == "myapp/blind/debug"
        assert payload == "hello"
        assert retain is False
        assert qos == 1

    async def test_retain_and_qos_passthrough(self, ctx_parts: dict) -> None:
        """Custom retain and qos values are forwarded to the MQTT port."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        await ctx.publish("status", "online", retain=True, qos=0)

        _, _, retain, qos = mqtt.published[0]
        assert retain is True
        assert qos == 0


# ---------------------------------------------------------------------------
# DeviceContext — sleep
# ---------------------------------------------------------------------------


class TestSleep:
    """Tests for DeviceContext.sleep() shutdown-aware sleeping.

    Technique: Async Behaviour Testing — verifying both normal
    completion and early return on shutdown.
    """

    async def test_sleep_completes_normally(self, ctx_parts: dict) -> None:
        """sleep() returns after the specified duration when no shutdown."""
        ctx = DeviceContext(**ctx_parts)

        start = asyncio.get_event_loop().time()
        await ctx.sleep(0.05)
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed >= 0.04  # Allow slight timing tolerance

    async def test_sleep_returns_early_on_shutdown(self, ctx_parts: dict) -> None:
        """sleep() returns before the full duration when shutdown is signalled."""
        shutdown_event = ctx_parts["shutdown_event"]
        ctx = DeviceContext(**ctx_parts)

        async def set_shutdown():
            await asyncio.sleep(0.01)
            shutdown_event.set()

        asyncio.create_task(set_shutdown())
        start = asyncio.get_event_loop().time()
        await ctx.sleep(10.0)  # Should return WAY before 10s
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0
        assert ctx.shutdown_requested

    async def test_sleep_does_not_raise_on_shutdown(self, ctx_parts: dict) -> None:
        """sleep() returns silently (no exception) when shutdown fires."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        # Should return immediately without raising
        await ctx.sleep(10.0)


# ---------------------------------------------------------------------------
# DeviceContext — on_command
# ---------------------------------------------------------------------------


class TestOnCommand:
    """Tests for DeviceContext.on_command() handler registration.

    Technique: Specification-based Testing — registration, decorator
    pattern, and duplicate detection.
    """

    async def test_registers_handler(self, ctx: DeviceContext) -> None:
        """on_command() stores the handler for later retrieval."""

        async def handler(topic: str, payload: str) -> None:
            pass

        ctx.on_command(handler)
        assert ctx.command_handler is handler

    async def test_returns_handler_for_decorator_use(self, ctx: DeviceContext) -> None:
        """on_command() returns the handler unchanged (decorator pattern)."""

        async def handler(topic: str, payload: str) -> None:
            pass

        result = ctx.on_command(handler)
        assert result is handler

    async def test_decorator_syntax(self, ctx: DeviceContext) -> None:
        """on_command() works as a decorator."""

        @ctx.on_command
        async def handler(topic: str, payload: str) -> None:
            pass

        assert ctx.command_handler is handler

    async def test_raises_on_duplicate_registration(self, ctx: DeviceContext) -> None:
        """on_command() raises RuntimeError if already registered."""

        async def handler1(topic: str, payload: str) -> None:
            pass

        async def handler2(topic: str, payload: str) -> None:
            pass

        ctx.on_command(handler1)
        with pytest.raises(RuntimeError, match="already registered"):
            ctx.on_command(handler2)


# ---------------------------------------------------------------------------
# DeviceContext — adapter
# ---------------------------------------------------------------------------


class TestAdapter:
    """Tests for DeviceContext.adapter() port resolution.

    Technique: Protocol Conformance — resolving typed adapters
    from the registry.
    """

    def test_resolves_registered_adapter(self, ctx_parts: dict) -> None:
        """adapter() returns the instance registered for a port type."""
        clock = FakeClock(42.0)
        ctx_parts["adapters"] = {ClockPort: clock}
        ctx = DeviceContext(**ctx_parts)

        resolved = ctx.adapter(ClockPort)
        assert resolved is clock

    def test_raises_lookup_error_for_unregistered(self, ctx: DeviceContext) -> None:
        """adapter() raises LookupError for an unknown port type."""
        with pytest.raises(LookupError, match="No adapter registered"):
            ctx.adapter(ClockPort)

    def test_generic_return_type(self, ctx_parts: dict) -> None:
        """adapter() return type matches the requested port type."""
        clock = FakeClock(1.0)
        ctx_parts["adapters"] = {ClockPort: clock}
        ctx = DeviceContext(**ctx_parts)

        result = ctx.adapter(ClockPort)
        # Verify structural compatibility — the result has now()
        assert result.now() == 1.0


# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------


class TestAppContext:
    """Tests for AppContext lifecycle-hook context.

    Technique: Specification-based Testing — settings access and
    adapter resolution (subset of DeviceContext).
    """

    def test_settings_property(self) -> None:
        """settings property returns the injected Settings instance."""
        settings = make_settings()
        app_ctx = AppContext(settings=settings, adapters={})
        assert app_ctx.settings is settings

    def test_adapter_resolves_correctly(self) -> None:
        """adapter() resolves a registered port type."""
        clock = FakeClock(99.0)
        app_ctx = AppContext(settings=make_settings(), adapters={ClockPort: clock})

        resolved = app_ctx.adapter(ClockPort)
        assert resolved is clock

    def test_adapter_raises_for_missing_port(self) -> None:
        """adapter() raises LookupError for an unregistered port type."""
        app_ctx = AppContext(settings=make_settings(), adapters={})
        with pytest.raises(LookupError, match="No adapter registered"):
            app_ctx.adapter(ClockPort)


# ---------------------------------------------------------------------------
# _import_string
# ---------------------------------------------------------------------------


class TestImportString:
    """Tests for _import_string() lazy import utility.

    Technique: Specification-based Testing — correct imports,
    and Error Condition Testing — malformed paths.
    """

    def test_imports_stdlib_class(self) -> None:
        """Successfully imports a well-known stdlib class."""
        cls = _import_string("collections:OrderedDict")
        from collections import OrderedDict

        assert cls is OrderedDict

    def test_raises_value_error_for_missing_colon(self) -> None:
        """Raises ValueError when path has no ':' separator."""
        with pytest.raises(ValueError, match="Expected"):
            _import_string("collections.OrderedDict")

    def test_raises_value_error_for_multiple_colons(self) -> None:
        """Raises ValueError when path has more than one ':'."""
        with pytest.raises(ValueError, match="Expected"):
            _import_string("a:b:c")

    def test_raises_import_error_for_nonexistent_module(self) -> None:
        """Raises ImportError for a module that does not exist."""
        with pytest.raises(ModuleNotFoundError):
            _import_string("nonexistent_module_xyz:Foo")

    def test_raises_attribute_error_for_nonexistent_class(self) -> None:
        """Raises AttributeError for a class missing from the module."""
        with pytest.raises(AttributeError):
            _import_string("collections:NonExistentClass")
