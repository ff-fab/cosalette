"""Integration tests — device archetypes (telemetry, command, manual, periodic).

Validates each archetype end-to-end: polling telemetry, reactive command
handling, manually-managed device loops, and periodic background tasks.

See Also:
    ADR-010 — Device archetypes.
    ADR-018 — Coalescing groups.
    ADR-025 — Command topic routing and dispatch patterns.
    ADR-041 — @app.periodic design decision.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import pytest

from cosalette._context import DeviceContext
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Adapter stubs — reused across telemetry and command archetype tests
# ---------------------------------------------------------------------------


@runtime_checkable
class SensorPort(Protocol):
    """Port protocol for a sensor that returns a reading."""

    def read(self) -> dict[str, object]: ...


class FakeSensor:
    """Concrete adapter stub returning a fixed reading."""

    def read(self) -> dict[str, object]:
        return {"count": 42, "trigger": "CLOSED"}


# ---------------------------------------------------------------------------
# TestTelemetryArchetype
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

        # Strict ordering is safe because the harness is fresh;
        # the first N topic messages are the first N poll publishes.
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


# ---------------------------------------------------------------------------
# TestCommandArchetype
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestManualDeviceArchetype
# ---------------------------------------------------------------------------


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
# TestPeriodicTaskArchetype
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
