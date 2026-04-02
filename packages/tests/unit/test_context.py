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
from cosalette._command import Command
from cosalette._context import AppContext, DeviceContext
from cosalette._settings import Settings
from cosalette._utils import _import_string
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

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

    def test_command_queue_exists(self, ctx: DeviceContext) -> None:
        """DeviceContext has internal command queue infrastructure.

        Technique: Specification-based Testing — verifying internal
        infrastructure exists for command dispatch.
        """
        assert isinstance(ctx._command_queue, asyncio.Queue)
        assert ctx._commands_consumed is False


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
        """sleep() delegates to clock and returns when no shutdown."""
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(5.0)

        assert ctx.clock.now() == 5.0  # FakeClock advanced

    async def test_sleep_returns_early_on_shutdown(self, ctx_parts: dict) -> None:
        """sleep() returns early when shutdown is already signalled."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(10.0)

        assert ctx.shutdown_requested
        assert ctx.clock.now() == 0.0  # clock must not advance

    async def test_sleep_does_not_raise_on_shutdown(self, ctx_parts: dict) -> None:
        """sleep() returns silently (no exception) when shutdown fires."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        # Should return immediately without raising
        await ctx.sleep(10.0)

    async def test_sleep_advances_clock_cumulatively(self, ctx_parts: dict) -> None:
        """Multiple sleeps advance FakeClock time cumulatively."""
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(1.0)
        await ctx.sleep(2.5)

        assert ctx.clock.now() == 3.5


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
# DeviceContext — on_command sub-topic support
# ---------------------------------------------------------------------------


class TestOnCommandSubTopic:
    """Tests for on_command() sub-topic registration.

    Technique: Specification-based Testing — sub-topic routing,
    validation, exclusivity, and coexistence with commands().
    """

    async def test_sub_topic_registers_handler(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """@ctx.on_command('calibrate') stores handler under key 'calibrate'."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        ctx.on_command("calibrate")(handler)
        assert ctx.get_command_handler("calibrate") is handler

    async def test_sub_topic_decorator_returns_handler(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Sub-topic decorator returns the handler unchanged."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        result = ctx.on_command("calibrate")(handler)
        assert result is handler

    async def test_sub_topic_rejects_slash(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """on_command('a/b') raises ValueError for multi-level sub-topic."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            ctx.on_command("a/b")

    @pytest.mark.parametrize("bad", ["+", "#", "cal+ibrate", "reset#"])
    async def test_sub_topic_rejects_mqtt_wildcards(
        self,
        ctx_parts: dict[str, Any],
        bad: str,
    ) -> None:
        """on_command rejects MQTT wildcard characters + and #."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            ctx.on_command(bad)

    async def test_sub_topic_rejects_empty_string(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """on_command('') raises ValueError for empty sub-topic."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="must not be empty"):
            ctx.on_command("")

    async def test_sub_topic_duplicate_raises(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Registering same sub-topic twice raises RuntimeError."""
        ctx = DeviceContext(**ctx_parts)

        async def h1(topic: str, payload: str) -> None:
            pass

        async def h2(topic: str, payload: str) -> None:
            pass

        ctx.on_command("calibrate")(h1)
        with pytest.raises(RuntimeError, match="already registered"):
            ctx.on_command("calibrate")(h2)

    async def test_root_and_sub_topic_coexist(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Root handler + sub-topic handler on same device works."""
        ctx = DeviceContext(**ctx_parts)

        async def root_handler(topic: str, payload: str) -> None:
            pass

        async def sub_handler(topic: str, payload: str) -> None:
            pass

        ctx.on_command(root_handler)
        ctx.on_command("calibrate")(sub_handler)

        assert ctx.command_handler is root_handler
        assert ctx.get_command_handler("calibrate") is sub_handler

    async def test_commands_and_sub_topic_handler_coexist(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """commands() + on_command('calibrate') on same device works."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        async def sub_handler(topic: str, payload: str) -> None:
            pass

        # Consume commands (root)
        async for _ in ctx.commands():
            pass

        # Sub-topic handler should still be allowed
        ctx.on_command("calibrate")(sub_handler)
        assert ctx.get_command_handler("calibrate") is sub_handler

    async def test_commands_blocks_root_on_command_not_sub_topic(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """After commands(), root on_command() fails but on_command('sub') succeeds."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        async for _ in ctx.commands():
            pass

        async def handler(topic: str, payload: str) -> None:
            pass

        # Root should be blocked
        with pytest.raises(RuntimeError, match="commands\\(\\) iterator already"):
            ctx.on_command(handler)

        # Sub-topic should still work
        ctx.on_command("sub")(handler)
        assert ctx.get_command_handler("sub") is handler

    async def test_explicit_no_arg_call(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """ctx.on_command()(handler) works as root decorator."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        result = ctx.on_command()(handler)
        assert result is handler
        assert ctx.command_handler is handler


# ---------------------------------------------------------------------------
# DeviceContext — commands() async iterator
# ---------------------------------------------------------------------------


class TestCommands:
    """Tests for DeviceContext.commands() async iterator.

    Technique: Async Behaviour Testing — verifying queue consumption,
    shutdown termination, timeout yields, and exclusivity guards.
    """

    async def test_commands_yields_queued_command(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """commands() yields Command objects put into the internal queue."""
        ctx = DeviceContext(**ctx_parts)
        cmd = Command(topic="myapp/blind/set", payload="OPEN")
        await ctx._command_queue.put(cmd)
        ctx_parts["shutdown_event"].set()  # ensure iterator terminates

        results: list[Command | None] = []
        async for c in ctx.commands():
            results.append(c)

        assert results == [cmd]

    async def test_commands_terminates_on_shutdown(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Iterator terminates when shutdown_requested becomes True."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        results: list[Command | None] = []
        async for c in ctx.commands():
            results.append(c)

        assert results == []

    async def test_commands_fifo_order(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Multiple commands are yielded in FIFO order."""
        ctx = DeviceContext(**ctx_parts)
        cmds = [
            Command(topic="t", payload="first"),
            Command(topic="t", payload="second"),
            Command(topic="t", payload="third"),
        ]
        for cmd in cmds:
            await ctx._command_queue.put(cmd)
        ctx_parts["shutdown_event"].set()

        results: list[Command | None] = []
        async for c in ctx.commands():
            results.append(c)

        assert results == cmds

    async def test_commands_raises_on_second_call(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Calling commands() twice raises RuntimeError."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        # First call — exhaust immediately (shutdown is set)
        async for _ in ctx.commands():
            pass

        with pytest.raises(RuntimeError, match="already active"):
            async for _ in ctx.commands():
                pass

    async def test_commands_raises_when_on_command_registered(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """commands() raises RuntimeError if on_command handler exists."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        ctx.on_command(handler)

        with pytest.raises(RuntimeError, match="on_command handler already"):
            async for _ in ctx.commands():
                pass

    async def test_on_command_raises_when_commands_consumed(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """on_command() raises RuntimeError if commands() is already active."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        async for _ in ctx.commands():
            pass

        async def handler(topic: str, payload: str) -> None:
            pass

        with pytest.raises(RuntimeError, match="commands\\(\\) iterator already"):
            ctx.on_command(handler)

    async def test_on_command_blocked_at_commands_call_time(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """on_command() raises immediately after commands(), before iteration."""
        ctx = DeviceContext(**ctx_parts)
        _ = ctx.commands()  # not iterated yet

        async def handler(topic: str, payload: str) -> None:
            pass

        with pytest.raises(RuntimeError, match="commands\\(\\) iterator already"):
            ctx.on_command(handler)

    async def test_commands_yields_none_on_timeout(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """With timeout, yields None when no commands arrive.

        Technique: Boundary Value Analysis — timeout expiry path.
        """
        ctx = DeviceContext(**ctx_parts)

        results: list[Command | None] = []
        async for c in ctx.commands(timeout=0.01):
            results.append(c)
            if len(results) >= 2:
                ctx_parts["shutdown_event"].set()

        assert results == [None, None]

    async def test_commands_no_none_without_timeout(
        self,
        ctx_parts: dict[str, Any],
    ) -> None:
        """Without timeout, does NOT yield None — only real commands.

        Technique: Specification-based Testing — internal poll does
        not leak to consumer.
        """
        ctx = DeviceContext(**ctx_parts)
        cmd = Command(topic="t", payload="p")
        await ctx._command_queue.put(cmd)

        # Schedule shutdown after a short delay so internal poll cycles
        async def shutdown_later() -> None:
            await asyncio.sleep(0.05)
            ctx_parts["shutdown_event"].set()

        asyncio.create_task(shutdown_later())

        results: list[Command | None] = []
        async for c in ctx.commands():
            results.append(c)

        assert results == [cmd]
        assert None not in results


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


# ---------------------------------------------------------------------------
# TestRootDeviceTopics — root-level (unnamed) device topic construction
# ---------------------------------------------------------------------------


class TestRootDeviceTopics:
    """Tests for root-level (unnamed) device topic construction.

    When ``is_root=True``, topics omit the device name segment:
    ``{prefix}/state`` instead of ``{prefix}/{device}/state``.

    Technique: State-based Testing — MockMqttClient records
    published topics for assertion.
    """

    async def test_publish_state_root_device(self, ctx_parts: dict) -> None:
        """Root device publishes to {prefix}/state."""
        ctx = DeviceContext(**ctx_parts, is_root=True)
        await ctx.publish_state({"temp": 21.5})

        topic, payload, retain, qos = ctx_parts["mqtt"].published[0]
        assert topic == "myapp/state"

    async def test_publish_state_named_device_unchanged(self, ctx_parts: dict) -> None:
        """Named device still publishes to {prefix}/{device}/state."""
        ctx = DeviceContext(**ctx_parts, is_root=False)
        await ctx.publish_state({"temp": 21.5})

        topic, _, _, _ = ctx_parts["mqtt"].published[0]
        assert topic == "myapp/blind/state"

    async def test_publish_channel_root_device(self, ctx_parts: dict) -> None:
        """Root device publishes to {prefix}/{channel}."""
        ctx = DeviceContext(**ctx_parts, is_root=True)
        await ctx.publish("debug", "hello")

        topic = ctx_parts["mqtt"].published[0][0]
        assert topic == "myapp/debug"

    async def test_publish_channel_named_device_unchanged(
        self,
        ctx_parts: dict,
    ) -> None:
        """Named device still publishes to {prefix}/{device}/{channel}."""
        ctx = DeviceContext(**ctx_parts, is_root=False)
        await ctx.publish("debug", "hello")

        topic = ctx_parts["mqtt"].published[0][0]
        assert topic == "myapp/blind/debug"
