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
import logging
from typing import Any

import pytest

from cosalette._clock import ClockPort
from cosalette._command import Command
from cosalette._context import AppContext, DeviceContext
from cosalette._context._device_context import _cancel_and_drain
from cosalette._settings import Settings
from cosalette._utils import _import_string
from cosalette.testing import FakeClock, ManualClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

type _CtxParts = dict[str, Any]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx_parts() -> _CtxParts:
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
def ctx(ctx_parts: _CtxParts) -> DeviceContext:
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

    def test_shutdown_requested_true_after_event_set(
        self, ctx_parts: _CtxParts
    ) -> None:
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

    async def test_publishes_json_to_state_topic(self, ctx_parts: _CtxParts) -> None:
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

    async def test_retain_false_override(self, ctx_parts: _CtxParts) -> None:
        """retain=False overrides the default retain=True."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        await ctx.publish_state({"status": "ok"}, retain=False)

        _, _, retain, _ = mqtt.published[0]
        assert retain is False

    async def test_payload_is_json_serialised(self, ctx_parts: _CtxParts) -> None:
        """Complex payloads are JSON-serialised correctly."""
        mqtt = ctx_parts["mqtt"]
        ctx = DeviceContext(**ctx_parts)

        payload = {"nested": {"key": [1, 2, 3]}, "flag": True}
        await ctx.publish_state(payload)  # ty: ignore[invalid-argument-type]

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

    async def test_publishes_to_channel_topic(self, ctx_parts: _CtxParts) -> None:
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

    async def test_retain_and_qos_passthrough(self, ctx_parts: _CtxParts) -> None:
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

    async def test_sleep_completes_normally(self, ctx_parts: _CtxParts) -> None:
        """sleep() delegates to clock and returns when no shutdown."""
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(5.0)

        assert ctx.clock.now() == 5.0  # FakeClock advanced

    async def test_sleep_returns_early_on_shutdown(self, ctx_parts: _CtxParts) -> None:
        """sleep() returns early when shutdown is already signalled."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(10.0)

        assert ctx.shutdown_requested
        assert ctx.clock.now() == 0.0  # clock must not advance

    async def test_sleep_does_not_raise_on_shutdown(self, ctx_parts: _CtxParts) -> None:
        """sleep() returns silently (no exception) when shutdown fires."""
        ctx_parts["shutdown_event"].set()
        ctx = DeviceContext(**ctx_parts)

        # Should return immediately without raising
        await ctx.sleep(10.0)

    async def test_sleep_advances_clock_cumulatively(
        self, ctx_parts: _CtxParts
    ) -> None:
        """Multiple sleeps advance FakeClock time cumulatively."""
        ctx = DeviceContext(**ctx_parts)

        await ctx.sleep(1.0)
        await ctx.sleep(2.5)

        assert ctx.clock.now() == 3.5

    async def test_sleep_propagates_caller_cancellation(
        self, ctx_parts: _CtxParts
    ) -> None:
        """A task parked in sleep() unwinds when cancelled from outside.

        Regression for cos-iftg: the ``FIRST_COMPLETED`` loser-cleanup
        must not swallow a cancellation delivered to the caller, or a
        device handler parked in ``sleep()`` would continue into its next
        sleep instead of shutting down.  A gating ``ManualClock`` keeps
        the sleep genuinely parked so cancellation is the only way out.
        """
        ctx_parts["clock"] = ManualClock()
        ctx = DeviceContext(**ctx_parts)
        proceeded = False

        async def handler() -> None:
            nonlocal proceeded
            await ctx.sleep(3600.0)
            proceeded = True

        task = asyncio.ensure_future(handler())
        await asyncio.sleep(0)  # park in sleep()'s internal race
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert not proceeded


# ---------------------------------------------------------------------------
# _cancel_and_drain — FIRST_COMPLETED loser cleanup
# ---------------------------------------------------------------------------


class TestCancelAndDrain:
    """Tests for the drain helper shared by sleep() and _await_command().

    Technique: Async Behaviour Testing — a self-requested cancellation is
    suppressed (normal loser cleanup) while one delivered to the caller
    from outside propagates.  Regression guard for cos-iftg, where the
    cleanup blanket-suppressed every ``CancelledError``.
    """

    async def test_suppresses_self_requested_cancellation(self) -> None:
        """Draining a task we cancel ourselves does not raise."""
        parked = asyncio.ensure_future(asyncio.Event().wait())
        await asyncio.sleep(0)  # let it park

        await _cancel_and_drain([parked])  # must not raise

        assert parked.cancelled()

    async def test_reraises_externally_delivered_cancellation(self) -> None:
        """A cancellation delivered to the caller during the drain propagates."""
        parked = asyncio.ensure_future(asyncio.Event().wait())
        await asyncio.sleep(0)

        async def caller() -> None:
            await _cancel_and_drain([parked])

        caller_task = asyncio.ensure_future(caller())
        await asyncio.sleep(0)  # caller reaches `await parked` inside the drain
        caller_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await caller_task
        assert caller_task.cancelled()


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
        ctx_parts: _CtxParts,
    ) -> None:
        """@ctx.on_command('calibrate') stores handler under key 'calibrate'."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        ctx.on_command("calibrate")(handler)
        assert ctx.get_command_handler("calibrate") is handler

    async def test_sub_topic_decorator_returns_handler(
        self,
        ctx_parts: _CtxParts,
    ) -> None:
        """Sub-topic decorator returns the handler unchanged."""
        ctx = DeviceContext(**ctx_parts)

        async def handler(topic: str, payload: str) -> None:
            pass

        result = ctx.on_command("calibrate")(handler)
        assert result is handler

    async def test_sub_topic_rejects_slash(
        self,
        ctx_parts: _CtxParts,
    ) -> None:
        """on_command('a/b') raises ValueError for multi-level sub-topic."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            ctx.on_command("a/b")

    @pytest.mark.parametrize("bad", ["+", "#", "cal+ibrate", "reset#"])
    async def test_sub_topic_rejects_mqtt_wildcards(
        self,
        ctx_parts: _CtxParts,
        bad: str,
    ) -> None:
        """on_command rejects MQTT wildcard characters + and #."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            ctx.on_command(bad)

    async def test_sub_topic_rejects_empty_string(
        self,
        ctx_parts: _CtxParts,
    ) -> None:
        """on_command('') raises ValueError for empty sub-topic."""
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ValueError, match="must not be empty"):
            ctx.on_command("")

    async def test_sub_topic_duplicate_raises(
        self,
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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
        ctx_parts: _CtxParts,
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

    async def test_commands_shutdown_responsive_with_large_timeout(
        self,
        ctx_parts: _CtxParts,
    ) -> None:
        """Shutdown is detected within 1s even with a large timeout.

        Technique: Non-functional Requirement — verifying shutdown
        latency is bounded independently of timeout value.
        """
        ctx = DeviceContext(**ctx_parts)

        async def shutdown_soon() -> None:
            await asyncio.sleep(0.05)
            ctx_parts["shutdown_event"].set()

        asyncio.create_task(shutdown_soon())

        loop = asyncio.get_running_loop()
        start = loop.time()
        results: list[Command | None] = []
        async for c in ctx.commands(timeout=60):
            results.append(c)
        elapsed = loop.time() - start

        # Should exit almost immediately (≤1s), not wait 60s
        assert elapsed < 1.0
        # No commands were queued → no yields before shutdown
        assert results == []


# ---------------------------------------------------------------------------
# DeviceContext — adapter
# ---------------------------------------------------------------------------


class TestAdapter:
    """Tests for DeviceContext.adapter() port resolution.

    Technique: Protocol Conformance — resolving typed adapters
    from the registry.
    """

    def test_resolves_registered_adapter(self, ctx_parts: _CtxParts) -> None:
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

    def test_generic_return_type(self, ctx_parts: _CtxParts) -> None:
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

    async def test_publish_state_root_device(self, ctx_parts: _CtxParts) -> None:
        """Root device publishes to {prefix}/state."""
        ctx = DeviceContext(**ctx_parts, is_root=True)
        await ctx.publish_state({"temp": 21.5})

        topic, payload, retain, qos = ctx_parts["mqtt"].published[0]
        assert topic == "myapp/state"

    async def test_publish_state_named_device_unchanged(
        self, ctx_parts: _CtxParts
    ) -> None:
        """Named device still publishes to {prefix}/{device}/state."""
        ctx = DeviceContext(**ctx_parts, is_root=False)
        await ctx.publish_state({"temp": 21.5})

        topic, _, _, _ = ctx_parts["mqtt"].published[0]
        assert topic == "myapp/blind/state"

    async def test_publish_channel_root_device(self, ctx_parts: _CtxParts) -> None:
        """Root device publishes to {prefix}/{channel}."""
        ctx = DeviceContext(**ctx_parts, is_root=True)
        await ctx.publish("debug", "hello")

        topic = ctx_parts["mqtt"].published[0][0]
        assert topic == "myapp/debug"

    async def test_publish_channel_named_device_unchanged(
        self,
        ctx_parts: _CtxParts,
    ) -> None:
        """Named device still publishes to {prefix}/{device}/{channel}."""
        ctx = DeviceContext(**ctx_parts, is_root=False)
        await ctx.publish("debug", "hello")

        topic = ctx_parts["mqtt"].published[0][0]
        assert topic == "myapp/blind/debug"


# ---------------------------------------------------------------------------
# SubEntityContext — Name Validation
# ---------------------------------------------------------------------------


class TestSubEntityNameValidation:
    """Tests for sub-entity name validation rules.

    Technique: Equivalence Partitioning — valid names, invalid MQTT chars,
    reserved names, empty strings, concurrent duplicates, same-as-device.
    """

    async def test_rejects_empty_name(self, ctx: DeviceContext) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            async with ctx.sub_entity(""):
                pass

    @pytest.mark.parametrize("char", ["/", "+", "#"])
    async def test_rejects_invalid_mqtt_characters(
        self, ctx: DeviceContext, char: str
    ) -> None:
        """Names containing MQTT wildcards or separator are rejected."""
        with pytest.raises(ValueError, match="invalid MQTT characters"):
            async with ctx.sub_entity(f"foo{char}bar"):
                pass

    @pytest.mark.parametrize(
        "reserved",
        [
            "state",
            "set",
            "availability",
            "status",
            "error",
            "config",
            "attributes",
            "json_attributes",
            "diagnostic",
            "firmware",
        ],
    )
    async def test_rejects_reserved_names(
        self, ctx: DeviceContext, reserved: str
    ) -> None:
        """Reserved topic names are rejected."""
        with pytest.raises(ValueError, match="reserved"):
            async with ctx.sub_entity(reserved):
                pass

    async def test_rejects_concurrent_duplicate(self, ctx: DeviceContext) -> None:
        """Same sub-entity name cannot be active twice concurrently."""
        async with ctx.sub_entity("cal"):
            with pytest.raises(ValueError, match="already active"):
                async with ctx.sub_entity("cal"):
                    pass

    async def test_allows_reuse_after_exit(self, ctx: DeviceContext) -> None:
        """Name can be reused after the previous context manager exits."""
        async with ctx.sub_entity("cal"):
            pass
        async with ctx.sub_entity("cal"):
            pass

    async def test_warns_when_name_matches_device(
        self, ctx: DeviceContext, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Logs WARNING when sub-entity name matches device name."""
        with caplog.at_level(logging.WARNING, logger="cosalette._context"):
            async with ctx.sub_entity("blind"):
                pass
        assert "matches device name" in caplog.text

    async def test_accepts_valid_name(self, ctx: DeviceContext) -> None:
        """Normal alphanumeric names are accepted."""
        async with ctx.sub_entity("calibrate") as sub:
            assert sub.name == "calibrate"


# ---------------------------------------------------------------------------
# SubEntityContext — Lifecycle
# ---------------------------------------------------------------------------


class TestSubEntityLifecycle:
    """Tests for sub-entity availability lifecycle.

    Technique: State Transition Testing — online on enter,
    offline on exit, state cleared on exit.
    """

    async def test_publishes_online_on_enter(self, ctx_parts: _CtxParts) -> None:
        """Entering publishes 'online' to availability topic (retained)."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal"):
            topic, payload, retain, qos = ctx_parts["mqtt"].published[0]
            assert topic == "myapp/blind/cal/availability"
            assert payload == "online"
            assert retain is True
            assert qos == 1

    async def test_publishes_offline_on_exit(self, ctx_parts: _CtxParts) -> None:
        """Exiting publishes 'offline' to availability topic (retained)."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal"):
            pass

        msgs = ctx_parts["mqtt"].published
        # Last message is offline availability
        topic, payload, retain, qos = msgs[-1]
        assert topic == "myapp/blind/cal/availability"
        assert payload == "offline"
        assert retain is True

    async def test_clears_retained_state_on_exit(self, ctx_parts: _CtxParts) -> None:
        """Exiting publishes empty payload to state topic to clear retained."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal") as sub:
            await sub.publish_state({"step": "measuring"})

        msgs = ctx_parts["mqtt"].published
        # Second-to-last message clears state (empty payload)
        state_clear = msgs[-2]
        assert state_clear[0] == "myapp/blind/cal/state"
        assert state_clear[1] == ""
        assert state_clear[2] is True  # retain

    async def test_tracks_active_sub_entities(self, ctx: DeviceContext) -> None:
        """Sub-entity name is tracked while active and removed on exit."""
        assert "cal" not in ctx._active_sub_entities
        async with ctx.sub_entity("cal"):
            assert "cal" in ctx._active_sub_entities
        assert "cal" not in ctx._active_sub_entities

    async def test_cleanup_on_exception(self, ctx_parts: _CtxParts) -> None:
        """Cleanup runs even when the with-block raises."""
        ctx = DeviceContext(**ctx_parts)
        with pytest.raises(RuntimeError, match="boom"):
            async with ctx.sub_entity("cal"):
                raise RuntimeError("boom")

        assert "cal" not in ctx._active_sub_entities
        msgs = ctx_parts["mqtt"].published
        topics = [m[0] for m in msgs]
        assert "myapp/blind/cal/availability" in topics
        # Offline was published
        offline_msgs = [
            m for m in msgs if m[0].endswith("/availability") and m[1] == "offline"
        ]
        assert len(offline_msgs) == 1

    async def test_entry_publish_failure_cleans_up_name(
        self, ctx_parts: _CtxParts
    ) -> None:
        """If the online publish fails, the name is removed from active set."""
        mqtt = MockMqttClient(raise_on_publish=ConnectionError("broker down"))
        ctx_parts["mqtt"] = mqtt
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ConnectionError, match="broker down"):
            async with ctx.sub_entity("cal"):
                pass  # pragma: no cover — never reached

        assert "cal" not in ctx._active_sub_entities

    async def test_exit_publish_failure_cleans_up_name(
        self, ctx_parts: _CtxParts
    ) -> None:
        """If exit publish fails, the name is still removed from active set."""
        mqtt = MockMqttClient()
        ctx_parts["mqtt"] = mqtt
        ctx = DeviceContext(**ctx_parts)

        with pytest.raises(ConnectionError, match="broker down"):
            async with ctx.sub_entity("cal"):
                # Break publish after the online message succeeded
                mqtt.raise_on_publish = ConnectionError("broker down")

        assert "cal" not in ctx._active_sub_entities


# ---------------------------------------------------------------------------
# SubEntityContext — Publish State
# ---------------------------------------------------------------------------


class TestSubEntityPublishState:
    """Tests for SubEntityContext.publish_state().

    Technique: Specification-based Testing — topic structure,
    JSON serialisation, retain flag.
    """

    async def test_publishes_to_sub_entity_topic(self, ctx_parts: _CtxParts) -> None:
        """State is published to {prefix}/{device}/{sub}/state."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal") as sub:
            await sub.publish_state({"step": 1})

        state_msgs = [
            m
            for m in ctx_parts["mqtt"].published
            if m[0] == "myapp/blind/cal/state" and m[1] != ""
        ]
        assert len(state_msgs) == 1
        topic, payload, retain, qos = state_msgs[0]
        assert json.loads(payload) == {"step": 1}
        assert retain is True
        assert qos == 1

    async def test_publish_state_not_retained(self, ctx_parts: _CtxParts) -> None:
        """retain=False is forwarded to MQTT publish."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal") as sub:
            await sub.publish_state({"step": 1}, retain=False)

        state_msgs = [
            m
            for m in ctx_parts["mqtt"].published
            if m[0] == "myapp/blind/cal/state" and m[1] != ""
        ]
        assert state_msgs[0][2] is False

    async def test_root_device_sub_entity_topic(self, ctx_parts: _CtxParts) -> None:
        """Root device sub-entity publishes to {prefix}/{sub}/state."""
        ctx = DeviceContext(**ctx_parts, is_root=True)
        async with ctx.sub_entity("cal") as sub:
            await sub.publish_state({"step": 1})

        state_msgs = [
            m
            for m in ctx_parts["mqtt"].published
            if m[0] == "myapp/cal/state" and m[1] != ""
        ]
        assert len(state_msgs) == 1


# ---------------------------------------------------------------------------
# SubEntityContext — Command Registration
# ---------------------------------------------------------------------------


class TestSubEntityOnCommand:
    """Tests for SubEntityContext.on_command() delegation.

    Technique: Specification-based Testing — handler delegation
    to parent's sub-topic routing.
    """

    async def test_registers_handler_on_parent(self, ctx: DeviceContext) -> None:
        """on_command delegates to parent.on_command(sub_name)."""
        async with ctx.sub_entity("cal") as sub:

            @sub.on_command
            async def handle(cmd: Command) -> None:
                pass

            handler = ctx.get_command_handler("cal")
            assert handler is handle

    async def test_duplicate_handler_raises(self, ctx: DeviceContext) -> None:
        """Registering a second handler for same sub-topic raises."""
        async with ctx.sub_entity("cal") as sub:

            @sub.on_command
            async def handle1(cmd: Command) -> None:
                pass

            with pytest.raises(RuntimeError, match="already registered"):

                @sub.on_command
                async def handle2(cmd: Command) -> None:
                    pass

    async def test_multiple_concurrent_sub_entities(self, ctx_parts: _CtxParts) -> None:
        """Two sub-entities with different names can be active simultaneously."""
        ctx = DeviceContext(**ctx_parts)
        async with ctx.sub_entity("cal") as cal, ctx.sub_entity("diag") as diag:
            assert cal.name == "cal"
            assert diag.name == "diag"
            assert ctx._active_sub_entities == {"cal", "diag"}

            await cal.publish_state({"step": 1})
            await diag.publish_state({"ok": True})

        assert ctx._active_sub_entities == set()
