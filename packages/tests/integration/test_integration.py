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
from typing import Protocol, runtime_checkable

import pytest

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
        harness = AppHarness.create()
        execution_order: list[str] = []
        device_done = asyncio.Event()

        @harness.app.on_startup
        async def setup(ctx: AppContext) -> None:
            execution_order.append("startup")

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
        harness = AppHarness.create()
        hook_called = asyncio.Event()

        @harness.app.on_shutdown
        async def teardown(ctx: AppContext) -> None:
            hook_called.set()

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
        cleanly with hooks.

        Technique: Integration Testing — exercises the full App
        orchestrator with protocol-conforming adapter stubs.
        """
        harness = AppHarness.create()
        execution_log: list[str] = []
        device_published = asyncio.Event()
        command_received = asyncio.Event()

        # --- Register adapter ---
        harness.app.adapter(SensorPort, FakeSensor)

        # --- Startup hook ---
        @harness.app.on_startup
        async def on_startup(ctx: AppContext) -> None:
            execution_log.append("startup")

        # --- Shutdown hook ---
        @harness.app.on_shutdown
        async def on_shutdown(ctx: AppContext) -> None:
            execution_log.append("shutdown")

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
        # 1. Startup hook ran before device
        assert "startup" in execution_log
        assert execution_log.index("startup") < execution_log.index("published")

        # 2. Device published correct state from adapter
        messages = harness.mqtt.get_messages_for("testapp/counter/state")
        assert len(messages) >= 1
        payload = json.loads(messages[0][0])
        assert payload == {"count": 42, "trigger": "CLOSED"}

        # 3. Command was received
        assert "command:RESET" in execution_log

        # 4. Shutdown hook ran
        assert "shutdown" in execution_log

        # 5. Ordering: startup → publish → command → shutdown
        assert execution_log.index("startup") < execution_log.index("published")
        assert execution_log.index("published") < execution_log.index("command:RESET")
        assert execution_log.index("command:RESET") < execution_log.index("shutdown")
