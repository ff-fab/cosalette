"""Integration tests — full lifecycle validation.

Validates the complete cosalette application lifecycle: create app →
register device with adapter → start → publish state → receive
command → respond → shutdown.  Follows the gas2mqtt pattern from the
framework proposal (§8).

Test Techniques Used:
    - Integration Testing: end-to-end lifecycle via AppHarness.
    - State-based Testing: verify published messages and hook execution.
    - Protocol Conformance: adapter stubs satisfy port protocols.

See Also:
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

import pytest

from cosalette._command import Command
from cosalette._context import AppContext, DeviceContext
from cosalette.testing import AppHarness

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
        async def sensor(ctx: DeviceContext) -> None:
            await ctx.publish_state({"temperature": 22.5})
            device_done.set()

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
        async def blind(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                received_payloads.append(payload)
                command_received.set()

            handler_registered.set()

            while not ctx.shutdown_requested:
                await ctx.sleep(1)

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

        harness.mqtt.publish = _tracking_publish  # type: ignore[assignment]

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
        async def sensor(ctx: DeviceContext) -> None:
            execution_order.append("device")
            device_done.set()

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
        async def reader(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(SensorPort)
            resolved.append(adapter)
            device_done.set()

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
        async def reader(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(SensorPort)
            resolved.append(adapter)
            device_done.set()

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
        async def counter(ctx: DeviceContext) -> None:
            # Resolve adapter (like Hmc5883Adapter in gas2mqtt)
            sensor = ctx.adapter(SensorPort)
            reading = sensor.read()

            # Publish state (like gas2mqtt counter publishing)
            await ctx.publish_state(reading)
            execution_log.append("published")
            device_published.set()

            # Listen for commands
            @ctx.on_command
            async def handle_command(topic: str, payload: str) -> None:
                execution_log.append(f"command:{payload}")
                command_received.set()

            handler_registered.set()

            # Wait for shutdown
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

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

        harness.mqtt.publish = _tracking_publish  # type: ignore[assignment]

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            results["device"] = True
            device_ran.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

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

        harness.mqtt.publish = _tracking_publish  # type: ignore[assignment]

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
        async def blind(ctx: DeviceContext) -> None:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)
                command_received.set()
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
        async def blind(ctx: DeviceContext) -> None:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                blind_cmds.append(cmd)
                blind_done.set()
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> None:
            cmds = ctx.commands()
            light_ready.set()
            async for cmd in cmds:
                light_cmds.append(cmd)
                light_done.set()
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
        async def blind(ctx: DeviceContext) -> None:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                iter_received.append(cmd)
                blind_done.set()
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                callback_received.append(payload)
                light_done.set()

            light_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

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
        async def blind(ctx: DeviceContext) -> None:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)
                if len(received) >= count:
                    all_received.set()

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
