"""Integration tests — full lifecycle validation.

Validates the complete cosalette application lifecycle: create app →
register device with adapter → start → publish state → receive
command → respond → shutdown.  Follows the gas2mqtt pattern from the
framework proposal (§8).

Test Techniques Used:
    - Integration Testing: end-to-end lifecycle via AppHarness.
    - State-based Testing: verify published messages and hook execution.
    - Protocol Conformance: adapter stubs satisfy port protocols.
    - Fault Injection: proxy lifecycle method blocking (stream proxy tests).

See Also:
    ADR-007 — Testing strategy (integration layer).
    ADR-044 — Public Router and composition API.
    ADR-045 — Stateful stream receiver semantics.
    ADR-046 — Typed handler contracts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Protocol, runtime_checkable

import pytest
from pydantic import BaseModel

import cosalette
from cosalette._command import Command
from cosalette._context import AppContext, DeviceContext
from cosalette._stream import Stream, StreamablePort
from cosalette.mqtt import Payload
from cosalette.testing import AppHarness, StreamHandlerProxy

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Adapter stubs — simple protocols and implementations for testing
# ---------------------------------------------------------------------------


@runtime_checkable
class SensorPort(Protocol):
    """Port protocol for a sensor that returns a reading."""

    def read(self) -> dict[str, object]: ...


class FakeSensor:
    """Concrete adapter stub returning a fixed reading."""

    def read(self) -> dict[str, object]:
        return {"count": 42, "trigger": "CLOSED"}


class FakeSensorDryRun:
    """Dry-run adapter stub returning zeroed data."""

    def read(self) -> dict[str, object]:
        return {"count": 0, "trigger": "DRY_RUN"}


# ---------------------------------------------------------------------------
# TestFullLifecycle
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """Full-lifecycle integration tests via AppHarness.

    Each test wires an App with devices, hooks, and adapters,
    then runs the lifecycle to completion, asserting observable
    side-effects (published messages, hook execution order).

    Technique: Integration Testing — exercises the real App
    orchestrator with injected test doubles (MockMqttClient,
    FakeClock) to avoid real I/O.

    See Also:
        ADR-007 — Testing strategy (integration layer).
    """

    async def test_device_publishes_state(self) -> None:
        """Device publishes state; message appears in MockMqttClient.

        Technique: State-based Testing — register device, run lifecycle,
        inspect MockMqttClient.published for the expected topic and payload.
        """
        harness = AppHarness.create()
        device_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"temperature": 22.5})
            device_done.set()
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(messages) >= 1
        payload = json.loads(messages[0][0])
        assert payload == {"temperature": 22.5}

    async def test_device_receives_command(self) -> None:
        """Device receives inbound command via on_command + deliver.

        Technique: State-based Testing — register command handler, deliver
        a simulated MQTT message, verify the callback fires with the
        correct payload.
        """
        harness = AppHarness.create()
        received_payloads: list[str] = []
        command_received = asyncio.Event()

        handler_registered = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command
            async def handle(sub_topic: str | None, payload: str) -> None:
                received_payloads.append(payload)
                command_received.set()

            handler_registered.set()

            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handler_registered.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await command_received.wait()
            harness.trigger_shutdown()

        _simulate_task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert received_payloads == ["OPEN"]

    async def test_telemetry_publishes(self) -> None:
        """Telemetry publishes state on the correct topic.

        Technique: State-based Testing — register a telemetry function,
        run lifecycle, verify published messages on {prefix}/{name}/state.
        """
        harness = AppHarness.create()
        publish_done = asyncio.Event()
        original_publish = harness.mqtt.publish

        async def _tracking_publish(
            topic: str,
            payload: str,
            *,
            retain: bool = False,
            qos: int = 1,
        ) -> None:
            await original_publish(topic, payload, retain=retain, qos=qos)
            if topic == "testapp/temp/state":
                publish_done.set()

        harness.mqtt.publish = _tracking_publish  # ty: ignore[invalid-assignment]

        @harness.app.telemetry("temp", interval=0.01)
        async def temp(ctx: DeviceContext) -> dict[str, object]:
            return {"celsius": 21.0}

        async def _shutdown() -> None:
            await publish_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        messages = harness.mqtt.get_messages_for("testapp/temp/state")
        assert len(messages) >= 1
        payload = json.loads(messages[0][0])
        assert payload == {"celsius": 21.0}

    async def test_startup_hook_runs(self) -> None:
        """Startup hook runs before device functions start.

        Technique: State-based Testing — startup hook records a timestamp
        marker; device records another. Verify startup ran first.
        """
        execution_order: list[str] = []
        device_done = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            execution_order.append("startup")
            yield

        harness = AppHarness.create(lifespan=lifespan)

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            execution_order.append("device")
            device_done.set()
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert "startup" in execution_order
        assert "device" in execution_order
        assert execution_order.index("startup") < execution_order.index("device")

    async def test_shutdown_hook_runs(self) -> None:
        """Shutdown hook runs during shutdown phase.

        Technique: State-based Testing — trigger immediate shutdown,
        verify the shutdown hook was invoked.
        """
        hook_called = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield
            hook_called.set()

        harness = AppHarness.create(lifespan=lifespan)

        # Trigger shutdown immediately
        harness.trigger_shutdown()
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert hook_called.is_set()

    async def test_adapter_resolution_in_lifecycle(self) -> None:
        """Adapter registered via app.adapter() is resolvable in device context.

        Technique: Protocol Conformance — register a Protocol-typed
        adapter factory, verify ctx.adapter(PortType) returns the
        correct instance during device execution.
        """
        harness = AppHarness.create()
        resolved: list[object] = []
        device_done = asyncio.Event()

        harness.app.adapter(SensorPort, FakeSensor)

        @harness.app.device("reader")
        async def reader(ctx: DeviceContext) -> AsyncIterator[None]:
            adapter = ctx.adapter(SensorPort)
            resolved.append(adapter)
            device_done.set()
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(resolved) == 1
        assert isinstance(resolved[0], SensorPort)
        assert isinstance(resolved[0], FakeSensor)
        assert resolved[0].read() == {"count": 42, "trigger": "CLOSED"}

    async def test_dry_run_adapter_swap(self) -> None:
        """App with dry_run=True resolves the dry-run adapter variant.

        Technique: Protocol Conformance — register adapter with a
        dry_run variant, create App with dry_run=True via harness,
        verify the dry-run instance is used.
        """
        harness = AppHarness.create(dry_run=True)
        resolved: list[object] = []
        device_done = asyncio.Event()

        harness.app.adapter(SensorPort, FakeSensor, dry_run=FakeSensorDryRun)

        @harness.app.device("reader")
        async def reader(ctx: DeviceContext) -> AsyncIterator[None]:
            adapter = ctx.adapter(SensorPort)
            resolved.append(adapter)
            device_done.set()
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(resolved) == 1
        assert isinstance(resolved[0], FakeSensorDryRun)
        assert resolved[0].read() == {"count": 0, "trigger": "DRY_RUN"}

    async def test_full_lifecycle_gas2mqtt_pattern(self) -> None:
        """End-to-end gas2mqtt-style lifecycle with full orchestration.

        Validates the canonical cosalette usage pattern from the framework
        proposal §8: create App, register adapter, register device that
        reads from adapter and publishes state, receive command, shut down
        cleanly with lifespan.

        Technique: Integration Testing — exercises the full App
        orchestrator with protocol-conforming adapter stubs.
        """
        execution_log: list[str] = []
        device_published = asyncio.Event()
        command_received = asyncio.Event()

        @asynccontextmanager
        async def lifecycle(ctx: AppContext) -> AsyncIterator[None]:
            execution_log.append("startup")
            yield
            execution_log.append("shutdown")

        harness = AppHarness.create(lifespan=lifecycle)

        # --- Register adapter ---
        harness.app.adapter(SensorPort, FakeSensor)

        # --- Device "counter" (gas2mqtt pattern) ---
        @harness.app.device("counter")
        async def counter(ctx: DeviceContext) -> AsyncIterator[None]:
            # Resolve adapter (like Hmc5883Adapter in gas2mqtt)
            sensor = ctx.adapter(SensorPort)
            reading = sensor.read()

            # Publish state (like gas2mqtt counter publishing)
            await ctx.publish_state(reading)
            execution_log.append("published")
            device_published.set()

            # Listen for commands
            @ctx.on_command
            async def handle_command(sub_topic: str | None, payload: str) -> None:
                execution_log.append(f"command:{payload}")
                command_received.set()

            handler_registered.set()

            # Wait for shutdown
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        handler_registered = asyncio.Event()

        # --- Orchestrate test lifecycle ---
        async def _orchestrate() -> None:
            # Wait for device to publish and register handler
            await device_published.wait()
            await handler_registered.wait()

            # Simulate an inbound command
            await harness.mqtt.deliver("testapp/counter/set", "RESET")
            await command_received.wait()

            # Shutdown
            harness.trigger_shutdown()

        _orchestrate_task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # --- Assertions ---
        # 1. Lifespan startup ran before device
        assert "startup" in execution_log
        assert execution_log.index("startup") < execution_log.index("published")

        # 2. Device published correct state from adapter
        messages = harness.mqtt.get_messages_for("testapp/counter/state")
        assert len(messages) >= 1
        payload = json.loads(messages[0][0])
        assert payload == {"count": 42, "trigger": "CLOSED"}

        # 3. Command was received
        assert "command:RESET" in execution_log

        # 4. Lifespan teardown ran
        assert "shutdown" in execution_log

        # 5. Ordering: startup → publish → command → shutdown
        assert execution_log.index("startup") < execution_log.index("published")
        assert execution_log.index("published") < execution_log.index("command:RESET")
        assert execution_log.index("command:RESET") < execution_log.index("shutdown")


# ---------------------------------------------------------------------------
# TestCommandHandler — @app.command() integration tests
# ---------------------------------------------------------------------------


class TestCommandHandler:
    """Integration tests for @app.command() in a full lifecycle.

    Validates that the declarative command handler decorator works
    end-to-end: registration → MQTT subscription → dispatch → state
    publication → shutdown.

    Technique: Integration Testing — exercises the real App orchestrator
    with ``AppHarness`` test doubles (``MockMqttClient``, ``FakeClock``).
    """

    async def test_command_handler_receives_message(self) -> None:
        """@app.command handler receives message and publishes state.

        Technique: State-based Testing — register a command handler,
        simulate an MQTT message, verify the handler is called and
        state is published to ``{prefix}/{name}/state``.
        """
        harness = AppHarness.create()
        received: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        @harness.app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            received.append((topic, payload))
            command_done.set()
            return {"state": payload, "brightness": 100}

        async def _orchestrate() -> None:
            await asyncio.sleep(0.05)
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await command_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Handler was called with correct arguments
        assert received == [("testapp/light/set", "ON")]

        # State was auto-published from the returned dict
        messages = harness.mqtt.get_messages_for("testapp/light/state")
        assert len(messages) >= 1
        payload_data = json.loads(messages[0][0])
        assert payload_data == {"state": "ON", "brightness": 100}

    async def test_command_handler_with_adapter(self) -> None:
        """@app.command handler receives injected adapter via DI.

        Technique: Protocol Conformance — register a Protocol-typed
        adapter, verify the command handler receives it via type
        annotation and can call adapter methods.
        """
        harness = AppHarness.create()
        adapter_calls: list[str] = []
        command_done = asyncio.Event()

        harness.app.adapter(SensorPort, FakeSensor)

        @harness.app.command("valve")
        async def handle_valve(
            topic: str, payload: str, sensor: SensorPort
        ) -> dict[str, object]:
            reading = sensor.read()
            adapter_calls.append(f"read:{reading}")
            command_done.set()
            return {"reading": reading, "command": payload}

        async def _orchestrate() -> None:
            await asyncio.sleep(0.05)
            await harness.mqtt.deliver("testapp/valve/set", "READ")
            await command_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Adapter was called
        assert len(adapter_calls) == 1
        assert "count" in adapter_calls[0]

        # State was published
        messages = harness.mqtt.get_messages_for("testapp/valve/state")
        assert len(messages) >= 1
        payload_data = json.loads(messages[0][0])
        assert payload_data["command"] == "READ"
        assert payload_data["reading"] == {"count": 42, "trigger": "CLOSED"}

    async def test_command_coexists_with_device_and_telemetry(self) -> None:
        """@app.command, @app.device, and @app.telemetry all coexist.

        Technique: Integration Testing — register all three handler
        types in one app, verify they each process independently
        without interfering.
        """
        harness = AppHarness.create()
        results: dict[str, bool] = {
            "device": False,
            "telemetry": False,
            "command": False,
        }
        device_ran = asyncio.Event()
        telemetry_published = asyncio.Event()
        command_done = asyncio.Event()

        # Track telemetry publishes
        original_publish = harness.mqtt.publish

        async def _tracking_publish(
            topic: str,
            payload: str,
            *,
            retain: bool = False,
            qos: int = 1,
        ) -> None:
            await original_publish(topic, payload, retain=retain, qos=qos)
            if topic == "testapp/temp/state":
                telemetry_published.set()

        harness.mqtt.publish = _tracking_publish  # ty: ignore[invalid-assignment]

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            results["device"] = True
            device_ran.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        @harness.app.telemetry("temp", interval=0.01)
        async def temp(ctx: DeviceContext) -> dict[str, object]:
            results["telemetry"] = True
            return {"celsius": 21.0}

        @harness.app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            results["command"] = True
            command_done.set()
            return {"state": payload}

        async def _orchestrate() -> None:
            # Wait for device to start
            await device_ran.wait()
            # Wait for telemetry to publish
            await telemetry_published.wait()
            # Simulate a command
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await command_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # All three handler types ran
        assert results == {"device": True, "telemetry": True, "command": True}

        # Telemetry published
        temp_msgs = harness.mqtt.get_messages_for("testapp/temp/state")
        assert len(temp_msgs) >= 1

        # Command published state
        light_msgs = harness.mqtt.get_messages_for("testapp/light/state")
        assert len(light_msgs) >= 1
        assert json.loads(light_msgs[0][0]) == {"state": "ON"}

    async def test_shared_telemetry_command_name_lifecycle(self) -> None:
        """Telemetry and command sharing a name both function correctly.

        Technique: Integration Testing — register a telemetry handler
        and a command handler under the same name ("hot_water"), run
        the full lifecycle, and verify that telemetry publishes to
        the ``/state`` topic while the command handler receives
        messages on the ``/set`` topic.  Both share a single
        :class:`DeviceContext` under the hood.
        """
        harness = AppHarness.create()
        telemetry_published = asyncio.Event()
        command_received = asyncio.Event()
        command_payload: dict[str, str] = {}

        # Track telemetry publishes
        original_publish = harness.mqtt.publish

        async def _tracking_publish(
            topic: str,
            payload: str,
            *,
            retain: bool = False,
            qos: int = 1,
        ) -> None:
            await original_publish(topic, payload, retain=retain, qos=qos)
            if topic == "testapp/hot_water/state":
                telemetry_published.set()

        harness.mqtt.publish = _tracking_publish  # ty: ignore[invalid-assignment]

        @harness.app.telemetry("hot_water", interval=0.01)
        async def hot_water_telem(ctx: DeviceContext) -> dict[str, object]:
            return {"temp": 55.0}

        @harness.app.command("hot_water")
        async def hot_water_cmd(topic: str, payload: str) -> dict[str, object]:
            command_payload["value"] = payload
            command_received.set()
            return {"set": payload}

        async def _orchestrate() -> None:
            await telemetry_published.wait()
            await harness.mqtt.deliver("testapp/hot_water/set", "60")
            await command_received.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Telemetry published on the shared name's /state topic
        state_msgs = harness.mqtt.get_messages_for("testapp/hot_water/state")
        assert len(state_msgs) >= 1
        assert json.loads(state_msgs[0][0])["temp"] == 55.0

        # Command handler received the message
        assert command_received.is_set()
        assert command_payload["value"] == "60"

        # Command response published on the shared name's /state topic
        # (command handlers publish their return value to state)
        found_cmd_response = any(
            json.loads(msg[0]).get("set") == "60" for msg in state_msgs
        )
        assert found_cmd_response


# ---------------------------------------------------------------------------
# Coalescing Groups Integration
# ---------------------------------------------------------------------------


class TestCoalescingGroupsIntegration:
    """Integration tests for telemetry coalescing groups.

    Technique: Integration Testing — exercises grouped telemetry
    handlers via AppHarness to verify correct batching, MQTT
    publishing, and coexistence with ungrouped handlers.

    See Also:
        ADR-018 — Coalescing Groups.
    """

    async def test_grouped_telemetry_publishes_via_harness(self) -> None:
        """Grouped telemetry fires and publishes MQTT messages via AppHarness.

        Technique: State-based Testing — register grouped handler, run
        lifecycle, inspect MockMqttClient for expected MQTT messages.
        """
        harness = AppHarness.create()
        called = asyncio.Event()

        @harness.app.telemetry(name="sensor", interval=0.01, group="bus")
        async def sensor() -> dict[str, object]:
            called.set()
            return {"value": 1}

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert called.is_set()
        msgs = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(msgs) >= 1

    async def test_grouped_and_ungrouped_coexist_via_harness(self) -> None:
        """Grouped and ungrouped handlers coexist in the same app.

        Technique: State-based Testing — register grouped and ungrouped
        handlers, run lifecycle, verify both publish independently.
        """
        harness = AppHarness.create()
        grouped_called = asyncio.Event()
        ungrouped_called = asyncio.Event()

        @harness.app.telemetry(name="grouped_sensor", interval=0.01, group="bus")
        async def grouped_sensor() -> dict[str, object]:
            grouped_called.set()
            return {"g": 1}

        @harness.app.telemetry(name="solo_sensor", interval=0.01)
        async def solo_sensor() -> dict[str, object]:
            ungrouped_called.set()
            return {"u": 2}

        async def trigger_shutdown() -> None:
            await grouped_called.wait()
            await ungrouped_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert grouped_called.is_set()
        assert ungrouped_called.is_set()
        grouped_msgs = harness.mqtt.get_messages_for("testapp/grouped_sensor/state")
        assert len(grouped_msgs) >= 1
        solo_msgs = harness.mqtt.get_messages_for("testapp/solo_sensor/state")
        assert len(solo_msgs) >= 1


# ---------------------------------------------------------------------------
# TestCommandsIterator — ctx.commands() integration tests
# ---------------------------------------------------------------------------


class TestCommandsIterator:
    """Integration tests for ``ctx.commands()`` async iterator.

    Validates that devices using the pull-based ``async for cmd in
    ctx.commands():`` pattern receive routed MQTT commands via the
    device proxy, including multi-device routing, drain-on-shutdown,
    and high-throughput FIFO ordering.

    Technique: Integration Testing — exercises the real App
    orchestrator with ``AppHarness`` test doubles.
    """

    async def test_device_receives_command_via_commands_iterator(self) -> None:
        """Device using ctx.commands() receives MQTT command via deliver().

        Technique: State-based Testing — register device with
        ctx.commands(), deliver a message, verify the Command object
        has correct topic, payload, and a positive timestamp.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handler_registered = asyncio.Event()
        command_received = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)  # ty: ignore[invalid-argument-type]
                command_received.set()
                yield
                break

        async def _simulate() -> None:
            await handler_registered.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await command_received.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == 1
        assert received[0].topic == "testapp/blind/set"
        assert received[0].payload == "OPEN"
        assert received[0].timestamp > 0

    async def test_multiple_devices_commands_routed_correctly(self) -> None:
        """Two devices with ctx.commands() receive only their own messages.

        Technique: State-based Testing — register two devices, deliver
        one message to each, verify no cross-routing.
        """
        harness = AppHarness.create()
        blind_cmds: list[Command] = []
        light_cmds: list[Command] = []
        blind_ready = asyncio.Event()
        light_ready = asyncio.Event()
        blind_done = asyncio.Event()
        light_done = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                blind_cmds.append(cmd)  # ty: ignore[invalid-argument-type]
                blind_done.set()
                yield
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            light_ready.set()
            async for cmd in cmds:
                light_cmds.append(cmd)  # ty: ignore[invalid-argument-type]
                light_done.set()
                yield
                break

        async def _simulate() -> None:
            await blind_ready.wait()
            await light_ready.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await blind_done.wait()
            await light_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(blind_cmds) == 1
        assert blind_cmds[0].payload == "OPEN"
        assert len(light_cmds) == 1
        assert light_cmds[0].payload == "ON"

    async def test_commands_and_on_command_coexist(self) -> None:
        """commands() on one device and on_command on another both work.

        Technique: Integration Testing — verify the proxy dispatches
        via _command_queue for commands() devices and via callback
        for on_command devices simultaneously.
        """
        harness = AppHarness.create()
        iter_received: list[Command] = []
        callback_received: list[str] = []
        blind_ready = asyncio.Event()
        light_ready = asyncio.Event()
        blind_done = asyncio.Event()
        light_done = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                iter_received.append(cmd)  # ty: ignore[invalid-argument-type]
                blind_done.set()
                yield
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command
            async def handle(sub_topic: str | None, payload: str) -> None:
                callback_received.append(payload)
                light_done.set()

            light_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await blind_ready.wait()
            await light_ready.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await blind_done.wait()
            await light_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(iter_received) == 1
        assert iter_received[0].payload == "OPEN"
        assert callback_received == ["ON"]

    async def test_commands_high_throughput_fifo(self) -> None:
        """100 commands queued rapidly are all consumed in FIFO order.

        Technique: State-based Testing — deliver 100 sequential
        commands, verify all received in order.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handler_registered = asyncio.Event()
        all_received = asyncio.Event()
        count = 100

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)  # ty: ignore[invalid-argument-type]
                if len(received) >= count:
                    all_received.set()
                yield

        async def _simulate() -> None:
            await handler_registered.wait()
            for i in range(count):
                await harness.mqtt.deliver("testapp/blind/set", str(i))
            await all_received.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == count
        assert [c.payload for c in received] == [str(i) for i in range(count)]


# ---------------------------------------------------------------------------
# TestSubTopicRouting — sub-topic command routing integration tests
# ---------------------------------------------------------------------------


class TestSubTopicRouting:
    """Integration tests for sub-topic command routing.

    Validates the end-to-end flow: AppHarness → Router → CommandRunner →
    device handler for commands delivered to ``{prefix}/{device}/{sub}/set``.

    Technique: Integration Testing — exercises the real orchestrator with
    ``AppHarness`` test doubles. Sub-topic routing follows ADR-025.

    See Also:
        ADR-025 — Command channel and sub-topic routing.
    """

    async def test_subtopic_routes_to_correct_handler(self) -> None:
        """Distinct sub-topic handlers each receive only their commands.

        Registers ``calibrate`` and ``reset`` sub-topic handlers, delivers
        a command to each, and verifies correct ``(sub_topic, payload)``
        dispatch.
        """
        harness = AppHarness.create()
        cal_payloads: list[tuple[str | None, str]] = []
        rst_payloads: list[tuple[str | None, str]] = []
        handlers_ready = asyncio.Event()
        cal_done = asyncio.Event()
        rst_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            @ctx.on_command("reset")
            async def handle_rst(sub_topic: str | None, payload: str) -> None:
                rst_payloads.append((sub_topic, payload))
                rst_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await harness.mqtt.deliver("testapp/sensor/reset/set", "SOFT")
            await cal_done.wait()
            await rst_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert cal_payloads == [("calibrate", "HIGH")]
        assert rst_payloads == [("reset", "SOFT")]

    async def test_root_and_subtopic_coexist(self) -> None:
        """Root and sub-topic handlers on the same device dispatch correctly.

        Registers a root handler and a ``calibrate`` sub-topic handler,
        delivers one command to each, and verifies independent dispatch.
        """
        harness = AppHarness.create()
        root_payloads: list[tuple[str | None, str]] = []
        cal_payloads: list[tuple[str | None, str]] = []
        handlers_ready = asyncio.Event()
        root_done = asyncio.Event()
        cal_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command
            async def handle_root(sub_topic: str | None, payload: str) -> None:
                root_payloads.append((sub_topic, payload))
                root_done.set()

            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/set", "STOP")
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await root_done.wait()
            await cal_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert root_payloads == [(None, "STOP")]
        assert cal_payloads == [("calibrate", "HIGH")]

    async def test_command_type_handler_with_subtopic(self) -> None:
        """New-style Command-annotated handler receives sub-topic commands.

        Registers a handler typed with ``Command``, delivers a sub-topic
        command, and verifies the ``Command`` object carries the correct
        ``sub_topic`` and ``payload``.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handlers_ready = asyncio.Event()
        cmd_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(cmd: Command) -> None:
                received.append(cmd)
                cmd_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await cmd_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == 1
        assert received[0].sub_topic == "calibrate"
        assert received[0].payload == "HIGH"
        assert received[0].topic == "testapp/sensor/calibrate/set"
        assert received[0].timestamp > 0

    async def test_commands_iterator_receives_only_root(self) -> None:
        """``commands()`` yields only root commands; sub-topic handler gets its own.

        Registers ``commands()`` (root queue) and ``on_command("calibrate")``.
        Delivers both a root and a sub-topic command. Verifies ``commands()``
        only yields the root command while the sub-topic handler fires
        independently.
        """
        harness = AppHarness.create()
        iter_received: list[Command] = []
        cal_payloads: list[tuple[str | None, str]] = []
        ready = asyncio.Event()
        root_done = asyncio.Event()
        cal_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            cmds = ctx.commands()
            ready.set()
            async for cmd in cmds:
                iter_received.append(cmd)  # ty: ignore[invalid-argument-type]
                root_done.set()
                yield
                break

        async def _simulate() -> None:
            await ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await harness.mqtt.deliver("testapp/sensor/set", "STOP")
            await cal_done.wait()
            await root_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # commands() only got the root command
        assert len(iter_received) == 1
        assert iter_received[0].payload == "STOP"
        assert iter_received[0].sub_topic is None

        # sub-topic handler got its command
        assert cal_payloads == [("calibrate", "HIGH")]


# ---------------------------------------------------------------------------
# Archetype-Focused Integration Tests
# ---------------------------------------------------------------------------


class TestTelemetryArchetype:
    """Comprehensive telemetry archetype integration tests.

    Validates the polling telemetry archetype end-to-end: registration,
    execution patterns, MQTT publishing behavior, grouped vs ungrouped
    execution, and integration with other framework features.

    Technique: Integration Testing — exercises the complete telemetry
    polling pipeline via AppHarness with state-based assertions on
    published MQTT messages and execution sequencing.

    See Also:
        ADR-010 — Device archetypes (telemetry archetype definition).
        ADR-018 — Coalescing groups (grouped execution behavior).
    """

    async def test_basic_telemetry_polling_cycle(self) -> None:
        """Telemetry device polls at interval and publishes state.

        Technique: State-based Testing — register telemetry with short
        interval, verify multiple poll cycles execute and publish to
        the correct MQTT topic with expected JSON payload structure.
        """
        harness = AppHarness.create()
        poll_count = 0
        target_polls = 3
        polls_done = asyncio.Event()

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor() -> dict[str, object]:
            nonlocal poll_count
            poll_count += 1
            if poll_count >= target_polls:
                polls_done.set()
            return {"reading": poll_count, "timestamp": harness.clock.now()}

        async def _shutdown() -> None:
            await polls_done.wait()
            await asyncio.sleep(0.01)  # Allow final publish
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify multiple polling cycles executed
        assert poll_count >= target_polls

        # Verify MQTT publication behavior
        messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(messages) >= target_polls

        # Verify JSON structure and progressive readings
        # Note: strict ordering assertion is safe because the harness is fresh,
        # so the first N topic messages are the first N poll publishes.
        for i, (payload_str, retain, qos) in enumerate(messages[:target_polls]):
            payload = json.loads(payload_str)
            assert payload["reading"] == i + 1
            assert retain is True  # Default retain behavior
            assert qos == 1

    async def test_ungrouped_telemetry_independent_execution(self) -> None:
        """Multiple ungrouped telemetry devices run independently.

        Technique: State-based Testing — register multiple telemetry
        devices with different intervals, verify they execute independently
        with no coordination or batching behavior.
        """
        harness = AppHarness.create()
        execution_log: list[tuple[str, float]] = []
        fast_polls = 0
        slow_polls = 0
        enough_data = asyncio.Event()

        @harness.app.telemetry("fast", interval=0.01)
        async def fast_sensor() -> dict[str, object]:
            nonlocal fast_polls
            fast_polls += 1
            execution_log.append(("fast", harness.clock.now()))
            if fast_polls >= 5 and slow_polls >= 2:
                enough_data.set()
            return {"value": fast_polls}

        @harness.app.telemetry("slow", interval=0.03)
        async def slow_sensor() -> dict[str, object]:
            nonlocal slow_polls
            slow_polls += 1
            execution_log.append(("slow", harness.clock.now()))
            return {"value": slow_polls}

        async def _shutdown() -> None:
            await enough_data.wait()
            await asyncio.sleep(0.02)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify independent execution - fast should execute more frequently
        assert fast_polls >= 5
        assert slow_polls >= 2
        # Allow for timing variations - fast should generally execute more
        assert fast_polls >= slow_polls

        # Verify both devices published independently
        fast_messages = harness.mqtt.get_messages_for("testapp/fast/state")
        slow_messages = harness.mqtt.get_messages_for("testapp/slow/state")
        assert len(fast_messages) >= fast_polls
        assert len(slow_messages) >= slow_polls

    async def test_grouped_telemetry_coalescing(self) -> None:
        """Grouped telemetry devices execute in coordinated batches.

        Technique: State-based Testing — register multiple telemetry
        devices in the same coalescing group, verify they execute in
        the same time window when intervals coincide.
        """
        harness = AppHarness.create()
        execution_log: list[tuple[str, float]] = []
        temp_polls = 0
        humidity_polls = 0
        batch_ready = asyncio.Event()

        @harness.app.telemetry("temp", interval=0.02, group="sensors")
        async def temp_sensor() -> dict[str, object]:
            nonlocal temp_polls
            temp_polls += 1
            execution_log.append(("temp", harness.clock.now()))
            if temp_polls >= 3 and humidity_polls >= 3:
                batch_ready.set()
            return {"celsius": 20 + temp_polls}

        @harness.app.telemetry("humidity", interval=0.02, group="sensors")
        async def humidity_sensor() -> dict[str, object]:
            nonlocal humidity_polls
            humidity_polls += 1
            execution_log.append(("humidity", harness.clock.now()))
            return {"percent": 50 + humidity_polls}

        async def _shutdown() -> None:
            await batch_ready.wait()
            await asyncio.sleep(0.02)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify both devices executed sufficient cycles
        assert temp_polls >= 3
        assert humidity_polls >= 3

        # Verify grouped execution - should have similar poll counts
        # due to coordinated scheduling
        assert abs(temp_polls - humidity_polls) <= 1

        # Verify MQTT publishing for both devices
        temp_messages = harness.mqtt.get_messages_for("testapp/temp/state")
        humidity_messages = harness.mqtt.get_messages_for("testapp/humidity/state")
        assert len(temp_messages) >= 3
        assert len(humidity_messages) >= 3

        # Strengthen core assertion: verify temp/humidity grouped executions
        # happen in the same scheduler window for at least three batches
        temp_timestamps = [ts for name, ts in execution_log if name == "temp"][:3]
        humidity_timestamps = [ts for name, ts in execution_log if name == "humidity"][
            :3
        ]

        for i in range(3):
            # Grouped executions should occur at the same time (FakeClock precision)
            assert abs(temp_timestamps[i] - humidity_timestamps[i]) <= 0.001

    async def test_telemetry_with_adapter_injection(self) -> None:
        """Telemetry device receives injected adapter via DI.

        Technique: Protocol Conformance — register a Protocol-typed
        adapter, verify the telemetry handler receives it correctly
        and can call adapter methods to generate state data.
        """
        harness = AppHarness.create()
        adapter_calls: list[str] = []
        polls_done = asyncio.Event()
        poll_count = 0

        harness.app.adapter(SensorPort, FakeSensor)

        @harness.app.telemetry("reader", interval=0.01)
        async def reader(sensor: SensorPort) -> dict[str, object]:
            nonlocal poll_count
            poll_count += 1
            reading = sensor.read()
            adapter_calls.append(f"read_{poll_count}")
            if poll_count >= 3:
                polls_done.set()
            return {"data": reading, "poll": poll_count}

        async def _shutdown() -> None:
            await polls_done.wait()
            await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify adapter was called
        assert len(adapter_calls) >= 3
        assert poll_count >= 3

        # Verify published state contains adapter data
        messages = harness.mqtt.get_messages_for("testapp/reader/state")
        assert len(messages) >= 3

        payload = json.loads(messages[0][0])
        assert payload["data"] == {"count": 42, "trigger": "CLOSED"}
        assert payload["poll"] == 1

    async def test_telemetry_error_isolation_and_recovery(self) -> None:
        """Telemetry continues polling after handler exceptions.

        Technique: State-based Testing — telemetry handler that fails
        on certain cycles, verify the polling loop continues and
        recovery is possible without stopping the device.
        """
        harness = AppHarness.create()
        poll_count = 0
        successful_polls = 0
        enough_polls = asyncio.Event()

        @harness.app.telemetry("flaky", interval=0.01)
        async def flaky_sensor() -> dict[str, object]:
            nonlocal poll_count, successful_polls
            poll_count += 1

            # Fail on polls 2 and 4, succeed on others
            if poll_count in (2, 4):
                raise RuntimeError(f"Simulated failure on poll {poll_count}")

            successful_polls += 1
            if poll_count >= 6:
                enough_polls.set()

            return {"reading": poll_count, "success": True}

        async def _shutdown() -> None:
            await enough_polls.wait()
            await asyncio.sleep(0.02)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify polling continued despite failures
        assert poll_count >= 6
        assert successful_polls >= 4  # Should succeed on polls 1, 3, 5, 6+

        # Verify only successful polls published state
        messages = harness.mqtt.get_messages_for("testapp/flaky/state")
        assert len(messages) >= 4

        # Verify no messages from failed polls (2, 4)
        published_readings = [json.loads(msg[0])["reading"] for msg in messages]
        assert 2 not in published_readings
        assert 4 not in published_readings
        assert 1 in published_readings
        assert 3 in published_readings


class TestCommandArchetype:
    """Comprehensive command archetype integration tests.

    Validates the reactive command handling archetype: MQTT subscription,
    command dispatch, handler execution with dependency injection,
    automatic state publishing, and integration with other archetypes.

    Technique: Integration Testing — exercises the complete command
    processing pipeline via AppHarness with simulated MQTT message
    delivery and state-based assertions.

    See Also:
        ADR-010 — Device archetypes (command archetype definition).
        ADR-025 — Command topic routing and dispatch patterns.
    """

    async def test_command_reactive_handling_and_state_publish(self) -> None:
        """Command handler reacts to MQTT and publishes state automatically.

        Technique: State-based Testing — register command handler,
        deliver MQTT command message, verify handler executes and
        framework auto-publishes the returned state dict.
        """
        harness = AppHarness.create()
        commands_received: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        @harness.app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            commands_received.append((topic, payload))
            command_done.set()
            # Return state dict - framework should auto-publish this
            return {"state": payload, "timestamp": harness.clock.now()}

        async def _orchestrate() -> None:
            await asyncio.sleep(0.02)  # Let handler registration complete
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await command_done.wait()
            await asyncio.sleep(0.01)  # Allow auto-publish
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify command was received by handler
        assert len(commands_received) == 1
        assert commands_received[0] == ("testapp/light/set", "ON")

        # Verify framework auto-published the returned state
        state_messages = harness.mqtt.get_messages_for("testapp/light/state")
        assert len(state_messages) >= 1

        payload = json.loads(state_messages[0][0])
        assert payload["state"] == "ON"
        assert "timestamp" in payload

    async def test_command_with_adapter_dependency_injection(self) -> None:
        """Command handler receives adapters via dependency injection.

        Technique: Protocol Conformance — register adapter, command
        handler declares adapter parameter, verify adapter is injected
        and can be used to generate command response state.
        """
        harness = AppHarness.create()
        adapter_operations: list[str] = []
        command_done = asyncio.Event()

        harness.app.adapter(SensorPort, FakeSensor)

        @harness.app.command("valve")
        async def handle_valve(
            topic: str, payload: str, sensor: SensorPort
        ) -> dict[str, object]:
            # Use injected adapter
            reading = sensor.read()
            adapter_operations.append(f"command_read:{payload}")
            command_done.set()

            return {
                "command": payload,
                "sensor_data": reading,
                "processed": True,
            }

        async def _orchestrate() -> None:
            await asyncio.sleep(0.02)
            await harness.mqtt.deliver("testapp/valve/set", "READ_STATUS")
            await command_done.wait()
            await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify adapter was called
        assert len(adapter_operations) == 1
        assert adapter_operations[0] == "command_read:READ_STATUS"

        # Verify state includes adapter data
        state_messages = harness.mqtt.get_messages_for("testapp/valve/state")
        assert len(state_messages) >= 1

        payload = json.loads(state_messages[0][0])
        assert payload["command"] == "READ_STATUS"
        assert payload["sensor_data"] == {"count": 42, "trigger": "CLOSED"}
        assert payload["processed"] is True

    async def test_multiple_commands_sequential_processing(self) -> None:
        """Multiple commands are processed sequentially in order.

        Technique: State-based Testing — deliver multiple MQTT commands
        rapidly, verify they are processed in order and each generates
        the expected state publication.
        """
        harness = AppHarness.create()
        commands_processed: list[str] = []
        all_commands_done = asyncio.Event()

        @harness.app.command("device")
        async def handle_device(topic: str, payload: str) -> dict[str, object]:
            commands_processed.append(payload)
            if len(commands_processed) >= 3:
                all_commands_done.set()

            return {
                "last_command": payload,
                "sequence": len(commands_processed),
            }

        async def _orchestrate() -> None:
            await asyncio.sleep(0.02)

            # Deliver multiple commands rapidly
            await harness.mqtt.deliver("testapp/device/set", "CMD1")
            await harness.mqtt.deliver("testapp/device/set", "CMD2")
            await harness.mqtt.deliver("testapp/device/set", "CMD3")

            await all_commands_done.wait()
            await asyncio.sleep(0.02)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify all commands were processed in order
        assert commands_processed == ["CMD1", "CMD2", "CMD3"]

        # Verify state was published for each command
        state_messages = harness.mqtt.get_messages_for("testapp/device/state")
        assert len(state_messages) >= 3

        # Verify sequence numbers in published states
        for i, (payload_str, _, _) in enumerate(state_messages[:3]):
            payload = json.loads(payload_str)
            assert payload["sequence"] == i + 1
            assert payload["last_command"] == f"CMD{i + 1}"

    async def test_command_coexistence_with_telemetry(self) -> None:
        """Command and telemetry archetypes coexist without interference.

        Technique: Integration Testing — register both command and
        telemetry devices, verify they operate independently and
        both can publish to their respective topics.
        """
        harness = AppHarness.create()
        telemetry_polls = 0
        command_received = False
        both_active = asyncio.Event()

        @harness.app.telemetry("sensor", interval=0.02)
        async def sensor() -> dict[str, object]:
            nonlocal telemetry_polls
            telemetry_polls += 1
            if telemetry_polls >= 2 and command_received:
                both_active.set()
            return {"reading": telemetry_polls}

        @harness.app.command("actuator")
        async def handle_actuator(topic: str, payload: str) -> dict[str, object]:
            nonlocal command_received
            command_received = True
            return {"actuator_state": payload}

        async def _orchestrate() -> None:
            await asyncio.sleep(0.03)  # Let telemetry start
            await harness.mqtt.deliver("testapp/actuator/set", "ACTIVATE")
            await both_active.wait()
            await asyncio.sleep(0.02)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify both archetypes operated
        assert telemetry_polls >= 2
        assert command_received

        # Verify independent MQTT publishing
        sensor_messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        actuator_messages = harness.mqtt.get_messages_for("testapp/actuator/state")

        assert len(sensor_messages) >= 2
        assert len(actuator_messages) >= 1

        # Verify message content
        sensor_payload = json.loads(sensor_messages[0][0])
        actuator_payload = json.loads(actuator_messages[0][0])

        assert "reading" in sensor_payload
        assert actuator_payload["actuator_state"] == "ACTIVATE"


class TestManualDeviceArchetype:
    """Comprehensive manual device archetype integration tests.

    Validates the manually managed device archetype: startup sequencing,
    manual loop control with shutdown awareness, command integration,
    lifecycle management, and graceful shutdown behavior.

    Technique: Integration Testing — exercises complete device lifecycle
    via AppHarness with careful sequencing and state-based verification
    of startup, execution, and shutdown phases.

    See Also:
        ADR-010 — Device archetypes (manual device definition).
        ADR-016 — Adapter lifecycle protocol integration.
    """

    async def test_device_manual_lifecycle_startup_and_shutdown(self) -> None:
        """Device completes manual startup and loop execution with graceful shutdown.

        Technique: State-based Testing — register device with manual
        loop, verify startup sequence and loop execution. Focus on
        the manually controlled aspects rather than shutdown timing.
        """
        harness = AppHarness.create()
        lifecycle_events: list[tuple[str, float]] = []
        device_ready = asyncio.Event()
        loop_iterations_done = asyncio.Event()

        @harness.app.device("controller")
        async def controller(ctx: DeviceContext) -> AsyncIterator[None]:
            lifecycle_events.append(("startup", ctx.clock.now()))

            # Simulate device initialization
            await ctx.sleep(0.01)
            lifecycle_events.append(("initialized", ctx.clock.now()))
            device_ready.set()

            # Manual control loop with shutdown awareness
            iteration = 0
            while not ctx.shutdown_requested:
                iteration += 1
                lifecycle_events.append(("loop_iteration", ctx.clock.now()))

                await ctx.publish_state({"status": "running", "iteration": iteration})

                if iteration >= 2:
                    loop_iterations_done.set()

                await ctx.sleep(0.02)
                yield

        async def _orchestrate() -> None:
            await device_ready.wait()
            await loop_iterations_done.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify manual lifecycle execution
        event_types = [event[0] for event in lifecycle_events]
        assert "startup" in event_types
        assert "initialized" in event_types
        assert "loop_iteration" in event_types

        # Verify proper sequencing - startup should come before initialization and loops
        startup_idx = event_types.index("startup")
        init_idx = event_types.index("initialized")
        first_loop_idx = event_types.index("loop_iteration")

        assert startup_idx < init_idx < first_loop_idx

        # Verify multiple loop iterations occurred (manual control loop working)
        loop_count = len([e for e in event_types if e == "loop_iteration"])
        assert loop_count >= 2

        # Verify state publications from manual loop
        state_messages = harness.mqtt.get_messages_for("testapp/controller/state")
        assert len(state_messages) >= 2

        # Check state content
        first_payload = json.loads(state_messages[0][0])
        assert first_payload["status"] == "running"
        assert first_payload["iteration"] == 1

    async def test_device_command_integration_within_manual_loop(self) -> None:
        """Device integrates command handling within manual control loop.

        Technique: State-based Testing — device with manual loop that
        registers command handler, verify commands are processed within
        the device context and can influence loop behavior.
        """
        harness = AppHarness.create()
        device_mode = "idle"
        commands_received = 0
        command_received = asyncio.Event()
        handler_ready = asyncio.Event()

        @harness.app.device("configurable")
        async def configurable_device(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal device_mode, commands_received

            @ctx.on_command
            async def handle_command(sub_topic: str | None, payload: str) -> None:
                nonlocal device_mode, commands_received
                commands_received += 1
                device_mode = payload.lower()
                command_received.set()

                await ctx.publish_state(
                    {
                        "mode": device_mode,
                        "command_count": commands_received,
                    }
                )

            handler_ready.set()

            # Manual loop that responds to state changes
            loop_count = 0
            while not ctx.shutdown_requested:
                loop_count += 1

                # Publish current state periodically
                if loop_count % 3 == 1:  # Every 3rd iteration
                    await ctx.publish_state(
                        {
                            "mode": device_mode,
                            "loop_count": loop_count,
                            "command_count": commands_received,
                        }
                    )

                await ctx.sleep(0.01)
                yield

        async def _orchestrate() -> None:
            await handler_ready.wait()

            # Send command
            await harness.mqtt.deliver("testapp/configurable/set", "ACTIVE")
            await command_received.wait()

            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify command was processed
        assert commands_received >= 1
        assert device_mode == "active"

        # Verify state publications include command effects
        state_messages = harness.mqtt.get_messages_for("testapp/configurable/state")
        assert len(state_messages) >= 2

        # Find message with command response
        command_response_found = False
        for payload_str, _, _ in state_messages:
            payload = json.loads(payload_str)
            if payload.get("command_count", 0) >= 1:
                command_response_found = True
                assert payload["mode"] == "active"
                break

        assert command_response_found

    async def test_multiple_devices_independent_lifecycle(self) -> None:
        """Multiple manual devices operate with independent lifecycles.

        Technique: State-based Testing — register multiple devices with
        different loop patterns, verify they execute independently
        without coordination and can be shutdown gracefully.
        """
        harness = AppHarness.create()
        device_states = {"fast": 0, "slow": 0}
        both_started = asyncio.Event()
        sufficient_execution = asyncio.Event()
        shutdown_triggered = False

        def _check_both_started() -> None:
            if device_states["fast"] >= 1 and device_states["slow"] >= 1:
                both_started.set()

        @harness.app.device("fast")
        async def fast_device(ctx: DeviceContext) -> AsyncIterator[None]:
            device_states["fast"] = 1  # Mark as started
            _check_both_started()

            iteration = 0
            while not ctx.shutdown_requested:
                iteration += 1
                device_states["fast"] = iteration

                await ctx.publish_state({"device": "fast", "iteration": iteration})
                await ctx.sleep(0.01)
                yield

        @harness.app.device("slow")
        async def slow_device(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal shutdown_triggered
            device_states["slow"] = 1  # Mark as started
            _check_both_started()

            iteration = 0
            while not ctx.shutdown_requested:
                iteration += 1
                device_states["slow"] = iteration

                await ctx.publish_state({"device": "slow", "iteration": iteration})

                # Trigger shutdown directly when sufficient execution is reached
                if (
                    device_states["fast"] >= 5
                    and device_states["slow"] >= 3
                    and not shutdown_triggered
                ):
                    sufficient_execution.set()
                    shutdown_triggered = True
                    harness.trigger_shutdown()

                await ctx.sleep(0.03)
                yield

        async def _orchestrate() -> None:
            await both_started.wait()
            await sufficient_execution.wait()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify independent execution - fast should iterate more
        assert device_states["fast"] >= 5
        assert device_states["slow"] >= 3
        # Allow for timing variations - fast should generally execute more
        assert device_states["fast"] >= device_states["slow"]

        # Verify independent state publications
        fast_messages = harness.mqtt.get_messages_for("testapp/fast/state")
        slow_messages = harness.mqtt.get_messages_for("testapp/slow/state")

        assert len(fast_messages) >= 5
        assert len(slow_messages) >= 3

        # Verify message content
        fast_payload = json.loads(fast_messages[0][0])
        slow_payload = json.loads(slow_messages[0][0])

        assert fast_payload["device"] == "fast"
        assert slow_payload["device"] == "slow"

    async def test_device_graceful_shutdown_with_cleanup(self) -> None:
        """Device performs cleanup during graceful shutdown sequence.

        Technique: State-based Testing — device that tracks resource
        usage and cleanup actions, verify shutdown signal is respected
        and cleanup operations complete before device termination.
        """
        harness = AppHarness.create()
        resource_state = {"opened": False, "cleaned_up": False}
        cleanup_events: list[str] = []
        device_ready = asyncio.Event()
        device_ran = asyncio.Event()
        cleanup_done = asyncio.Event()

        @harness.app.device("resource_manager")
        async def resource_manager(ctx: DeviceContext) -> AsyncIterator[None]:
            # Startup: acquire resources
            resource_state["opened"] = True
            cleanup_events.append("resource_opened")

            await ctx.publish_state({"status": "started", "resources": "acquired"})
            device_ready.set()

            # Main loop
            try:
                iteration = 0
                while not ctx.shutdown_requested:
                    iteration += 1
                    await ctx.publish_state(
                        {
                            "status": "running",
                            "iteration": iteration,
                            "resources": "active",
                        }
                    )
                    if iteration == 1:
                        device_ran.set()
                    await ctx.sleep(0.02)
                    yield
            finally:
                # Cleanup resources regardless of how loop exits
                cleanup_events.append("cleanup_started")
                resource_state["cleaned_up"] = True
                cleanup_events.append("cleanup_completed")

                await ctx.publish_state(
                    {
                        "status": "shutdown",
                        "resources": "cleaned_up",
                    }
                )
                cleanup_done.set()

        async def _orchestrate() -> None:
            await device_ready.wait()
            await device_ran.wait()
            harness.trigger_shutdown()
            await cleanup_done.wait()

        _task = asyncio.create_task(_orchestrate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Verify cleanup sequence completed
        assert resource_state["opened"]
        assert resource_state["cleaned_up"]
        assert cleanup_events == [
            "resource_opened",
            "cleanup_started",
            "cleanup_completed",
        ]

        # Verify final state message shows cleanup
        state_messages = harness.mqtt.get_messages_for("testapp/resource_manager/state")
        assert len(state_messages) >= 2

        final_payload = json.loads(state_messages[-1][0])
        assert final_payload["status"] == "shutdown"
        assert final_payload["resources"] == "cleaned_up"


# ---------------------------------------------------------------------------
# TestRouterComposition — prefix routing and cross-talk isolation (cos-9rv)
# ---------------------------------------------------------------------------


class TestRouterComposition:
    """Router composition: distinct prefixes isolate command dispatch.

    Validates that two routers mounted at different MQTT prefixes each
    receive only their own messages; messages to one prefix do not
    cross-talk into the other router's handlers.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: inspect published state topics to prove
          only the targeted handler fired.

    See Also:
        ADR-044 — Public Router and composition API.
    """

    async def test_two_routers_no_crosstalk(self) -> None:
        """Commands to zone1 do not reach zone2 and vice versa.

        Registers two routers at prefixes 'zone1' and 'zone2', each with a
        'valve' command. Delivers a command to zone1/valve/set and asserts
        only zone1's handler fires; then vice versa.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()

        zone1_calls: list[str] = []
        zone2_calls: list[str] = []

        router1 = cosalette.Router(prefix="zone1")
        router2 = cosalette.Router(prefix="zone2")

        @router1.command("valve")
        async def zone1_valve(payload: str) -> dict[str, object]:
            zone1_calls.append(payload)
            return {"zone": "zone1", "cmd": payload}

        @router2.command("valve")
        async def zone2_valve(payload: str) -> dict[str, object]:
            zone2_calls.append(payload)
            return {"zone": "zone2", "cmd": payload}

        harness.app.include_router(router1)
        harness.app.include_router(router2)

        # Dispatch to zone1/valve — only zone1 handler must fire
        await harness.call_command("zone1/valve", "OPEN")
        assert zone1_calls == ["OPEN"], "zone1 valve handler must fire"
        assert zone2_calls == [], "zone2 must not receive zone1's command"

        # Dispatch to zone2/valve — only zone2 handler must fire
        await harness.call_command("zone2/valve", "CLOSE")
        assert zone1_calls == ["OPEN"], "zone1 must not receive zone2's command"
        assert zone2_calls == ["CLOSE"], "zone2 valve handler must fire"

        # Verify correct state topics published for each zone
        zone1_msgs = harness.messages_for("testapp/zone1/valve/state")
        zone2_msgs = harness.messages_for("testapp/zone2/valve/state")
        assert len(zone1_msgs) == 1
        assert json.loads(zone1_msgs[0][0])["zone"] == "zone1"
        assert len(zone2_msgs) == 1
        assert json.loads(zone2_msgs[0][0])["zone"] == "zone2"

    async def test_slash_composed_command_topic_routed_correctly(self) -> None:
        """Router-prefixed command is invoked via slash-composed name.

        A command registered as 'calibrate' on a router with prefix='sensors'
        produces a registration named 'sensors/calibrate'. call_command must
        find it and route to the exact handler; the state publishes to
        testapp/sensors/calibrate/state.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()
        handler_invoked = False

        router = cosalette.Router(prefix="sensors")

        @router.command("calibrate")
        async def calibrate(payload: str) -> dict[str, object]:
            nonlocal handler_invoked
            handler_invoked = True
            return {"calibrated": True, "ref": payload}

        harness.app.include_router(router)

        await harness.call_command("sensors/calibrate", '{"ref": "factory"}')

        assert handler_invoked
        msgs = harness.messages_for("testapp/sensors/calibrate/state")
        assert len(msgs) == 1
        assert json.loads(msgs[0][0])["calibrated"] is True

    async def test_slash_composed_command_live_mqtt_delivery(self) -> None:
        """slash-composed router command receives live MQTT delivery via harness.run().

        Registers a command on a router with prefix='floor1', included under
        prefix='building', producing the full name 'building/floor1/calibrate'.
        Delivers a real MQTT message to testapp/building/floor1/calibrate/set
        and asserts the handler receives the exact topic and payload, and that
        state is published to testapp/building/floor1/calibrate/state.

        This test exercises the real TopicRouter dispatch path — it does NOT
        use call_command(); the message travels the full
        MockMqttClient → TopicRouter → CommandRunner pipeline.

        Technique: Integration Testing + State-based Testing.

        See Also:
            ADR-044 — Public Router and composition API.
        """
        harness = AppHarness.create()
        handler_calls: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        router = cosalette.Router(prefix="floor1")

        @router.command("calibrate")
        async def calibrate(topic: str, payload: str) -> dict[str, object]:
            handler_calls.append((topic, payload))
            command_done.set()
            return {"calibrated": True, "echo": payload}

        harness.app.include_router(router, prefix="building")

        async def _orchestrate() -> None:
            # Poll until subscriptions are registered before delivery.
            while not harness.mqtt.subscriptions:
                await asyncio.sleep(0)
            await harness.mqtt.deliver(
                "testapp/building/floor1/calibrate/set", "factory"
            )
            await command_done.wait()
            # Poll until state is published before triggering shutdown.
            while not harness.messages_for("testapp/building/floor1/calibrate/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        # Handler received exact topic and payload
        assert handler_calls == [("testapp/building/floor1/calibrate/set", "factory")]

        # State published to the composed topic
        msgs = harness.messages_for("testapp/building/floor1/calibrate/state")
        assert len(msgs) == 1
        result = json.loads(msgs[0][0])
        assert result["calibrated"] is True
        assert result["echo"] == "factory"

    async def test_slash_composed_command_subtopic_live_mqtt(self) -> None:
        """Subtopic delivered below a slash-composed router command is routed.

        Same router/include_router setup as the base test; delivers to
        testapp/building/floor1/measure/fine/set (a sub-topic of 'measure')
        and asserts the handler fires with the full topic.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()
        handler_calls: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        router = cosalette.Router(prefix="floor1")

        @router.command("measure")
        async def measure(topic: str, payload: str) -> dict[str, object]:
            handler_calls.append((topic, payload))
            command_done.set()
            return {"measured": True}

        harness.app.include_router(router, prefix="building")

        async def _orchestrate() -> None:
            while not harness.mqtt.subscriptions:
                await asyncio.sleep(0)
            await harness.mqtt.deliver(
                "testapp/building/floor1/measure/fine/set", "precise"
            )
            await command_done.wait()
            while not harness.messages_for("testapp/building/floor1/measure/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        assert len(handler_calls) == 1
        assert handler_calls[0][0] == "testapp/building/floor1/measure/fine/set"
        assert handler_calls[0][1] == "precise"

    async def test_router_telemetry_publishes_to_prefixed_topic(self) -> None:
        """Telemetry on a router publishes to {prefix}/{name}/state.

        Registers a telemetry handler on a router with prefix='env', includes
        the router, runs the app, and asserts publication to
        testapp/env/temp/state.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()

        router = cosalette.Router(prefix="env")

        @router.telemetry("temp", interval=0.01)
        async def env_temp() -> dict[str, object]:
            return {"celsius": 23.5}

        harness.app.include_router(router)

        async def _shutdown() -> None:
            while not harness.messages_for("testapp/env/temp/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_shutdown())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        msgs = harness.messages_for("testapp/env/temp/state")
        assert len(msgs) >= 1
        assert json.loads(msgs[0][0]) == {"celsius": 23.5}


# ---------------------------------------------------------------------------
# TestTriggerableTelemetryUnderRouterPrefix — triggerable under prefix (cos-9rv)
# ---------------------------------------------------------------------------


class TestTriggerableTelemetryUnderRouterPrefix:
    """Triggerable telemetry under a Router prefix fires on MQTT trigger.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: MQTT trigger to {prefix}/{name}/set fires
          handler immediately; result published to {prefix}/{name}/state.

    See Also:
        ADR-044 — Public Router and composition API.
    """

    async def test_triggerable_under_router_prefix_fires_on_set(self) -> None:
        """MQTT message to testapp/zone/sensor/set triggers the handler.

        Router with prefix='zone' has a triggerable telemetry 'sensor'.
        After include_router the name becomes 'zone/sensor'. Delivering to
        testapp/zone/sensor/set must fire the handler immediately beyond the
        first scheduled cycle.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()
        call_count = 0
        triggered_fired = asyncio.Event()

        router = cosalette.Router(prefix="zone")

        @router.telemetry("sensor", interval=3600, triggerable=True)
        async def zone_sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                triggered_fired.set()
            return {"value": call_count, "prefix": "zone"}

        harness.app.include_router(router)

        async def _simulate() -> None:
            # Wait for first scheduled publish
            while not harness.mqtt.get_messages_for("testapp/zone/sensor/state"):
                await asyncio.sleep(0.01)
            # Fire trigger
            await harness.mqtt.deliver("testapp/zone/sensor/set", "")
            await triggered_fired.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        try:
            await asyncio.wait_for(harness.run(), timeout=10.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        assert call_count >= 2, (
            "Handler must be called at least twice (scheduled + triggered)"
        )
        msgs = harness.messages_for("testapp/zone/sensor/state")
        assert len(msgs) >= 2
        # Last publish must contain 'prefix' key
        last_payload = json.loads(msgs[-1][0])
        assert last_payload["prefix"] == "zone"


# ---------------------------------------------------------------------------
# TestTypedCommandPayload — Pydantic model payload (cos-9rv)
# ---------------------------------------------------------------------------


class _SetpointCmd(BaseModel):
    """Pydantic model for the thermostat setpoint command payload."""

    value: float
    unit: str = "celsius"


class _ThermoState(BaseModel):
    """Pydantic model for the thermostat state reply."""

    setpoint: float
    unit: str
    accepted: bool


class TestTypedCommandPayload:
    """Typed command payload: Pydantic model deserialization + response.

    Technique:
        - Integration Testing: call_command dispatches JSON through the
          typed binding layer (ADR-046) into a Pydantic model.
        - State-based Testing: returned model is serialized and published.

    See Also:
        ADR-046 — Typed handler contracts.
    """

    @pytest.mark.parametrize(
        "payload,expected_value,expected_unit",
        [
            ({"value": 21.5, "unit": "celsius"}, 21.5, "celsius"),
            ({"value": 19.0}, 19.0, "celsius"),  # default field applied
            ({"value": 25.0, "unit": "fahrenheit"}, 25.0, "fahrenheit"),  # non-default
        ],
    )
    async def test_pydantic_payload_deserialized_and_response_published(
        self,
        payload: dict[str, object],
        expected_value: float,
        expected_unit: str,
    ) -> None:
        """JSON payload is deserialized to Pydantic model; model returned as state.

        Covers: full payload, default-field omission, and non-default unit.

        Technique: Equivalence Partitioning + Integration Testing +
        State-based Testing.
        """
        harness = AppHarness.create()
        received_cmd: _SetpointCmd | None = None

        @harness.app.command("thermostat")
        async def thermostat_cmd(
            cmd: Annotated[_SetpointCmd, Payload()],
        ) -> dict[str, object]:
            nonlocal received_cmd
            received_cmd = cmd
            return {"setpoint": cmd.value, "unit": cmd.unit}

        await harness.call_command("thermostat", payload)

        assert received_cmd is not None
        assert isinstance(received_cmd, _SetpointCmd)
        assert received_cmd.value == expected_value
        assert received_cmd.unit == expected_unit

        msgs = harness.messages_for("testapp/thermostat/state")
        assert len(msgs) == 1
        response = json.loads(msgs[0][0])
        assert response["setpoint"] == expected_value
        assert response["unit"] == expected_unit


# ---------------------------------------------------------------------------
# TestTypedTelemetryReturn — Pydantic model return (cos-9rv)
# ---------------------------------------------------------------------------


class TestTypedTelemetryReturn:
    """Typed telemetry return: Pydantic model is serialized on publish.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: model returned by handler appears as JSON on
          the state topic.

    See Also:
        ADR-046 — Typed handler contracts.
    """

    async def test_pydantic_return_serialized_to_state_topic(self) -> None:
        """Telemetry returning a Pydantic model is serialized to JSON on publish.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()

        @harness.app.telemetry("thermo", interval=0.01)
        async def thermo() -> _ThermoState:
            return _ThermoState(setpoint=22.0, unit="celsius", accepted=True)

        async def _shutdown() -> None:
            while not harness.messages_for("testapp/thermo/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_shutdown())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        msgs = harness.messages_for("testapp/thermo/state")
        assert len(msgs) >= 1
        payload = json.loads(msgs[0][0])
        assert payload["setpoint"] == 22.0
        assert payload["unit"] == "celsius"
        assert payload["accepted"] is True


# ---------------------------------------------------------------------------
# TestPeriodicTaskArchetype — @app.periodic + tick_periodic (cos-9rv)
# ---------------------------------------------------------------------------


class TestPeriodicTaskArchetype:
    """Periodic background task archetype via AppHarness.tick_periodic().

    Validates that a periodic handler registered with @app.periodic is
    invocable via tick_periodic() and that multiple calls execute the
    handler each time with expected side-effects.

    Technique:
        - Integration Testing: tick_periodic() drives handler directly,
          bypassing the clock interval, exercising the real DI injection path.
        - State-based Testing: verify side-effects accumulate with each tick.

    See Also:
        ADR-041 — @app.periodic design decision.
    """

    async def test_tick_periodic_invokes_handler_once(self) -> None:
        """tick_periodic() calls the named periodic handler exactly once.

        Technique: Integration Testing — direct invocation via test harness.
        """
        harness = AppHarness.create()
        call_count = 0

        @harness.app.periodic("cache_refresh", interval=60.0)
        async def cache_refresh() -> None:
            nonlocal call_count
            call_count += 1

        await harness.tick_periodic("cache_refresh")

        assert call_count == 1

    async def test_tick_periodic_multiple_calls_accumulate(self) -> None:
        """tick_periodic() called N times increments handler count N times.

        Technique: State-based Testing — cumulative invocation count.
        """
        harness = AppHarness.create()
        call_count = 0

        @harness.app.periodic("heartbeat", interval=30.0)
        async def heartbeat() -> None:
            nonlocal call_count
            call_count += 1

        for _ in range(5):
            await harness.tick_periodic("heartbeat")

        assert call_count == 5

    async def test_tick_periodic_unknown_name_raises(self) -> None:
        """tick_periodic() raises ValueError for an unknown task name.

        Technique: Error Guessing — unknown name sentinel.
        """
        harness = AppHarness.create()

        with pytest.raises(ValueError, match="No periodic task named"):
            await harness.tick_periodic("nonexistent")

    async def test_periodic_handler_records_side_effect(self) -> None:
        """Periodic handler records a side-effect observable after tick_periodic().

        Verifies that a periodic handler executes its body when driven by
        tick_periodic() and that the resulting state mutation is visible
        after the coroutine returns.  This test does NOT publish via MQTT;
        it exercises the DI-injection path and confirms tick semantics.

        Technique: Integration Testing — direct handler invocation via
        tick_periodic(), state-based assertion on accumulated side-effect.
        """
        harness = AppHarness.create()
        state_published = False

        @harness.app.periodic("status_broadcast", interval=10.0)
        async def status_broadcast() -> None:
            nonlocal state_published
            state_published = True

        await harness.tick_periodic("status_broadcast")

        assert state_published


# ---------------------------------------------------------------------------
# TestStreamProxyLifecycleOwnership — stream proxy + run_stream (cos-9rv)
# ---------------------------------------------------------------------------


class _StreamItem:
    """Minimal item type for stream lifecycle integration tests."""


class _LifecycleTrackingPort:
    """Fake StreamablePort that records all lifecycle calls in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._callback: object = None
        self.scan_started: asyncio.Event = asyncio.Event()

    async def open(self) -> None:
        self.calls.append("open")

    async def close(self) -> None:
        self.calls.append("close")

    async def start_scan(self) -> None:
        self.calls.append("start_scan")
        self.scan_started.set()

    async def stop_scan(self) -> None:
        self.calls.append("stop_scan")

    def register_callback(self, cb: object) -> None:
        self.calls.append("register_callback")
        self._callback = cb


class TestStreamProxyLifecycleOwnership:
    """Stream receiver: framework owns lifecycle; handler receives proxy.

    Validates ADR-045 contracts:
    - open/register_callback/start_scan/stop_scan/close are called by the
      framework (run_stream), not the handler.
    - The handler receives a _StreamHandlerProxy, not the raw adapter.
    - The proxy blocks access to lifecycle methods (open, close, start_scan,
      stop_scan) while forwarding non-lifecycle attribute access.

    Technique:
        - Protocol Conformance: _StreamHandlerProxy enforces ADR-045 contract.
        - State Transition Testing: port lifecycle state sequence verified.
        - Integration Testing: run_stream wires the full lifecycle end-to-end.

    See Also:
        ADR-045 — Stateful stream receiver semantics.
    """

    async def test_framework_calls_lifecycle_in_canonical_order(self) -> None:
        """run_stream calls open → register_callback → start_scan → stop_scan → close.

        Technique: State Transition Testing — canonical lifecycle order.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()

        async def handler(stream: Stream[_StreamItem]) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        async def _drive() -> None:
            # Wait until start_scan fires — deterministic, no tick-counting.
            await port.scan_started.wait()
            shutdown.set()

        driver = asyncio.create_task(_drive())
        try:
            await harness.run_stream(handler, resolved, shutdown=shutdown)
        finally:
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)

        assert "open" in port.calls
        assert "register_callback" in port.calls
        assert "start_scan" in port.calls
        assert "stop_scan" in port.calls
        assert "close" in port.calls

        # Canonical ordering
        idx = port.calls.index
        assert idx("open") < idx("register_callback")
        assert idx("register_callback") < idx("start_scan")
        assert idx("start_scan") < idx("stop_scan")
        assert idx("stop_scan") < idx("close")

    async def test_handler_receives_proxy_not_raw_port(self) -> None:
        """Handler injecting the concrete port type receives a StreamHandlerProxy.

        Technique: Protocol Conformance — proxy type assertion.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()
        received: list[object] = []

        async def handler(
            stream: Stream[_StreamItem],
            p: _LifecycleTrackingPort,
        ) -> AsyncIterator[None]:
            received.append(p)
            shutdown.set()
            async for _ in stream:
                yield

        await harness.run_stream(handler, resolved, shutdown=shutdown)

        assert len(received) == 1
        assert isinstance(received[0], StreamHandlerProxy), (
            f"Expected StreamHandlerProxy, got {type(received[0])}"
        )

    async def test_proxy_blocks_lifecycle_method_open(self) -> None:
        """Accessing proxy.open raises AttributeError (lifecycle method blocked).

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.open

    async def test_proxy_blocks_lifecycle_method_close(self) -> None:
        """Accessing proxy.close raises AttributeError (lifecycle method blocked).

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.close

    async def test_proxy_blocks_start_scan_and_stop_scan(self) -> None:
        """Accessing proxy.start_scan and proxy.stop_scan raise AttributeError.

        Technique: Protocol Conformance — ADR-045 lifecycle method guard.
        """
        port = _LifecycleTrackingPort()
        proxy = StreamHandlerProxy(port)

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.start_scan

        with pytest.raises(AttributeError, match="lifecycle method"):
            _ = proxy.stop_scan

    def test_proxy_allows_non_lifecycle_attribute_access(self) -> None:
        """Proxy forwards non-lifecycle attributes to the underlying adapter.

        Technique: Protocol Conformance — ADR-045 non-lifecycle forwarding.
        """

        class _ExtendedPort(_LifecycleTrackingPort):
            """Port with an extra non-lifecycle method for testing."""

            def get_status(self) -> str:
                return "ready"

        port = _ExtendedPort()
        proxy = StreamHandlerProxy(port)

        # Non-lifecycle method should be accessible
        assert proxy.get_status() == "ready"  # type: ignore[attr-defined]

    async def test_framework_closes_port_when_handler_raises(self) -> None:
        """Port lifecycle (stop_scan, close) runs even when handler raises.

        Validates ADR-045: the framework calls stop_scan and close
        regardless of whether the stream handler raises.  ``run_stream``
        absorbs handler exceptions (logging them) so the caller sees a
        clean return — the key invariant is that lifecycle cleanup runs.

        Technique: Fault Injection + State Transition Testing.
        """
        harness = AppHarness.create()
        port = _LifecycleTrackingPort()
        resolved: dict[type, object] = {StreamablePort[_StreamItem]: port}
        shutdown = asyncio.Event()

        async def failing_handler(stream: Stream[_StreamItem]) -> AsyncIterator[None]:
            raise ValueError("simulated handler error")
            yield  # pragma: no cover  # make it an async generator

        # run_stream absorbs handler exceptions (logs, does not re-raise)
        await harness.run_stream(failing_handler, resolved, shutdown=shutdown)

        assert "stop_scan" in port.calls
        assert "close" in port.calls
        assert port.calls.index("stop_scan") < port.calls.index("close")
