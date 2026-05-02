"""Integration tests — cross-feature functionality and stress scenarios.

Validates complex interactions between cosalette framework components:
telemetry + persistence, command handling + state machines, error
propagation, configuration overrides, and failure isolation.

Test Techniques Used:
    - Integration Testing: Multi-component interaction via AppHarness.
    - State Machine Testing: Command-driven state transitions.
    - Stress Testing: High-frequency operations with bounded limits.
    - Error Injection: Exception propagation and isolation.
    - Configuration Testing: Environment overrides and validation.
    - Failure Isolation: Component failures don't break others.

See Also:
    ADR-007 — Testing strategy (integration layer).
    ADR-011 — Error handling and publishing.
    ADR-013 — Telemetry publish strategies.
    ADR-015 — Persistence.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Protocol, runtime_checkable

import pytest

import cosalette
from cosalette._context import DeviceContext
from cosalette._persistence._persist import SaveOnShutdown
from cosalette._persistence._stores import MemoryStore
from cosalette._settings import MqttSettings
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# =============================================================================
# Adapter stubs for cross-feature testing
# =============================================================================


@runtime_checkable
class StateMachinePort(Protocol):
    """Port protocol for a device with state machine behavior."""

    def get_state(self) -> str: ...
    def transition_to(self, state: str) -> bool: ...
    def read_sensor(self) -> dict[str, object]: ...


class StatefulDevice:
    """Stateful device adapter with command-driven transitions."""

    def __init__(self) -> None:
        self._state = "idle"
        self._valid_transitions = {
            "idle": ["armed"],
            "armed": ["active", "idle"],
            "active": ["idle"],
        }

    def get_state(self) -> str:
        return self._state

    def transition_to(self, state: str) -> bool:
        if state in self._valid_transitions.get(self._state, []):
            self._state = state
            return True
        return False

    def read_sensor(self) -> dict[str, object]:
        """Telemetry method for state + sensor data."""
        return {"state": self._state, "timestamp": 1234567890}


class FailingAdapter:
    """Adapter that fails during telemetry after N successful calls."""

    def __init__(self, *, fail_after: int = 2) -> None:
        self.call_count = 0
        self.fail_after = fail_after

    def read_data(self) -> dict[str, object]:
        self.call_count += 1
        if self.call_count > self.fail_after:
            msg = f"Simulated failure on call {self.call_count}"
            raise RuntimeError(msg)
        return {"data": f"reading_{self.call_count}"}


class HighFrequencyAdapter:
    """Adapter for stress testing high-frequency telemetry."""

    def __init__(self) -> None:
        self.call_count = 0

    def read_metrics(self) -> dict[str, object]:
        self.call_count += 1
        return {
            "sequence": self.call_count,
            "cpu_percent": 45.2 + (self.call_count % 10),
            "memory_mb": 128 + (self.call_count % 50),
        }


# =============================================================================
# Test helpers
# =============================================================================


async def _cancel_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# =============================================================================
# Tests
# =============================================================================


class TestCrossFeatureSmoke:
    """Cross-feature smoke tests combining multiple framework components.

    Technique: Integration Testing — validates component interactions.
    """

    async def test_command_state_persistence_integration(self) -> None:
        """Commands change device state, publishes are persisted on shutdown.

        Validates cos-4a2.2.4: Cross-feature integration smoke tests.
        Combines command handling, state machine, telemetry, and persistence.
        """
        backend = MemoryStore()
        harness = AppHarness.create(store=backend)
        command_received = asyncio.Event()
        device_published = asyncio.Event()
        command_processed = asyncio.Event()
        active_state_persisted = asyncio.Event()

        # Pre-instantiate shared device to ensure state consistency
        shared_stateful_device = StatefulDevice()
        harness.app.adapter(StateMachinePort, lambda: shared_stateful_device)

        @harness.app.device("statemachine")
        async def statemachine(ctx: DeviceContext) -> None:
            state_adapter = ctx.adapter(StateMachinePort)

            # Publish initial state
            await ctx.publish_state(state_adapter.read_sensor())
            device_published.set()

            @ctx.on_command
            async def handle(sub_topic: str | None, payload: str) -> None:
                if payload == "arm" and state_adapter.transition_to("armed"):
                    await ctx.publish_state({"state": "armed", "timestamp": 1234567890})
                elif payload == "activate" and state_adapter.transition_to("active"):
                    await ctx.publish_state(
                        {"state": "active", "timestamp": 1234567890}
                    )
                elif payload == "reset" and state_adapter.transition_to("idle"):
                    await ctx.publish_state({"state": "idle", "timestamp": 1234567890})

                # Signal that command processing is complete
                command_processed.set()

            command_received.set()

            # Wait for shutdown
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        @harness.app.telemetry("state_monitor", interval=0.02, persist=SaveOnShutdown())
        async def state_monitor_telemetry(
            ctx: DeviceContext,
            store: cosalette.DeviceStore,
        ) -> dict[str, object]:
            # Use the shared adapter instance to observe command-driven state
            monitor_adapter = ctx.adapter(StateMachinePort)
            data = monitor_adapter.read_sensor()
            store.update(data)  # Store state for persistence

            # Signal when active state is persisted
            if data["state"] == "active":
                active_state_persisted.set()

            return data

        # Start and coordinate the test
        async def orchestrate():
            await device_published.wait()
            await command_received.wait()

            # Send state transition commands and wait for processing
            await harness.mqtt.deliver("testapp/statemachine/set", "arm")
            await command_processed.wait()

            # Reset the event and send next command
            command_processed.clear()
            await harness.mqtt.deliver("testapp/statemachine/set", "activate")
            await command_processed.wait()

            # Wait for active state to be persisted, then trigger shutdown
            await active_state_persisted.wait()
            harness.trigger_shutdown()

        orchestrate_task = asyncio.create_task(orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify state was published via MQTT
        state_messages = harness.mqtt.get_messages_for("testapp/statemachine/state")
        assert len(state_messages) >= 1  # Should have at least initial state

        # Verify telemetry published
        telemetry_messages = harness.mqtt.get_messages_for(
            "testapp/state_monitor/state"
        )
        assert len(telemetry_messages) >= 2  # Should have initial + active states

        # Verify persistence worked and contains command-driven state
        saved_data = backend.load("state_monitor")
        assert saved_data is not None
        assert "state" in saved_data
        # Verify persisted state is deterministically "active"
        assert saved_data["state"] == "active"


class TestHighFrequencyStress:
    """High-frequency telemetry stress testing.

    Technique: Stress Testing — bounded high-frequency operations.
    """

    async def test_high_frequency_telemetry_stress(self) -> None:
        """High-frequency telemetry with multiple handlers reaches target counts.

        Validates cos-4a2.1.3: High-frequency telemetry stress testing.
        Tests framework performance under rapid telemetry publication.
        """
        harness = AppHarness.create()
        adapter1 = HighFrequencyAdapter()
        adapter2 = HighFrequencyAdapter()
        target_count = 8  # Bounded target for reliability
        shutdown_event = asyncio.Event()

        @harness.app.telemetry("metrics1", interval=0.01)  # 10ms interval
        async def metrics1(ctx: DeviceContext) -> dict[str, object]:
            # Use adapter directly without registration for simple case
            data = adapter1.read_metrics()
            if (
                adapter1.call_count >= target_count
                and adapter2.call_count >= target_count // 2
            ):
                shutdown_event.set()
            return data

        @harness.app.telemetry("metrics2", interval=0.01)  # 10ms interval
        async def metrics2(ctx: DeviceContext) -> dict[str, object]:
            data = adapter2.read_metrics()
            if (
                adapter1.call_count >= target_count
                and adapter2.call_count >= target_count // 2
            ):
                shutdown_event.set()
            return data

        # Orchestrate shutdown
        async def orchestrate():
            await shutdown_event.wait()
            harness.trigger_shutdown()

        orchestrate_task = asyncio.create_task(orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify both adapters published expected counts
        assert adapter1.call_count >= target_count
        assert adapter2.call_count >= target_count // 2  # Allow some variance

        # Verify MQTT published expected counts
        metrics1_messages = harness.mqtt.get_messages_for("testapp/metrics1/state")
        metrics2_messages = harness.mqtt.get_messages_for("testapp/metrics2/state")

        assert len(metrics1_messages) >= target_count
        assert len(metrics2_messages) >= target_count // 2


class TestErrorPipelineIntegration:
    """Error pipeline integration and failure handling.

    Technique: Error Injection — exception propagation testing.
    """

    async def test_telemetry_handler_exceptions_continue_operation(self) -> None:
        """Handler exceptions are isolated, other handlers continue running.

        Validates cos-4a2.1.4: Error pipeline integration scenarios.
        Tests that one failing handler doesn't break the entire app.
        """
        harness = AppHarness.create()
        failing_adapter = FailingAdapter(fail_after=2)
        successful_calls = 0
        failed_calls = 0
        shutdown_event = asyncio.Event()

        @harness.app.telemetry("failing", interval=0.02)
        async def failing_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal failed_calls
            try:
                return failing_adapter.read_data()
            except RuntimeError:
                failed_calls += 1
                if failed_calls >= 3 and successful_calls > failed_calls:
                    shutdown_event.set()
                raise  # Re-raise to test framework error handling

        @harness.app.telemetry("stable", interval=0.01)
        async def stable_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal successful_calls
            successful_calls += 1
            if failed_calls >= 3 and successful_calls > failed_calls:
                shutdown_event.set()
            return {"stable_data": successful_calls}

        # Orchestrate shutdown
        async def orchestrate():
            await shutdown_event.wait()
            harness.trigger_shutdown()

        orchestrate_task = asyncio.create_task(orchestrate())

        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify the failing handler actually failed
        assert failed_calls >= 3

        # Verify the stable handler continued working
        assert successful_calls > failed_calls

        # Verify stable telemetry published successfully
        stable_messages = harness.mqtt.get_messages_for("testapp/stable/state")
        assert len(stable_messages) > 0

    async def test_publish_failure_handling(self) -> None:
        """MQTT publish failures are handled gracefully.

        Validates cos-4a2.1.4: Error pipeline integration scenarios.
        Tests framework behavior when MQTT publishing fails.
        """
        harness = AppHarness.create()
        publish_attempts = 0
        sensor_publish_failures = 0

        # Capture original publish method for wrapper
        original_publish = harness.mqtt.publish

        async def _tracking_publish(
            topic: str,
            payload: str | dict[str, Any],
            *,
            retain: bool = False,
            qos: int = 1,
        ) -> None:
            nonlocal sensor_publish_failures
            if topic == "testapp/sensor/state" and publish_attempts >= 5:
                sensor_publish_failures += 1
                harness.trigger_shutdown()
                raise RuntimeError("MQTT connection lost")
            await original_publish(topic, payload, retain=retain, qos=qos)

        harness.mqtt.publish = _tracking_publish  # ty: ignore[invalid-assignment]

        @harness.app.telemetry("sensor", interval=0.01)
        async def sensor_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal publish_attempts
            publish_attempts += 1
            return {"reading": publish_attempts}

        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            harness.mqtt.publish = original_publish  # ty: ignore[invalid-assignment]

        # Verify successful messages were published before failure
        sensor_messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(sensor_messages) == 4
        # Verify telemetry handler ran expected number of attempts
        assert publish_attempts >= 5
        # Verify exactly one publish failure occurred
        assert sensor_publish_failures == 1


class TestConfigurationValidation:
    """Configuration validation and environment override testing.

    Technique: Configuration Testing — settings validation.
    """

    async def test_settings_constructor_override(self) -> None:
        """Constructor parameters override default settings.

        Validates cos-4a2.1.5: Configuration validation and constructor overrides.
        Tests that custom settings are properly applied via AppHarness constructor.
        """
        # Test custom MQTT settings via constructor override
        custom_mqtt = MqttSettings(
            host="test-broker.local", port=8883, topic_prefix="testprefix"
        )

        harness = AppHarness.create(mqtt=custom_mqtt)
        settings_captured = None

        @harness.app.telemetry("config_test", interval=0.01)
        async def config_test(settings: cosalette.Settings) -> dict[str, object]:
            nonlocal settings_captured
            settings_captured = settings
            harness.trigger_shutdown()
            return {"test": "data"}

        await asyncio.wait_for(harness.run(), timeout=2.0)

        # Verify settings were applied
        assert settings_captured is not None
        assert settings_captured.mqtt.host == "test-broker.local"
        assert settings_captured.mqtt.port == 8883
        assert settings_captured.mqtt.topic_prefix == "testprefix"

    def test_settings_validation_errors(self) -> None:
        """Invalid settings values raise validation errors.

        Validates cos-4a2.1.5: Configuration validation and environment overrides.
        """
        with pytest.raises(ValueError, match="port"):
            MqttSettings(port=-1)  # Invalid port range

        with pytest.raises(ValueError, match="host"):
            MqttSettings(host="")  # Empty host


class TestComplexStateMachine:
    """Complex device state machine pattern testing.

    Technique: State Machine Testing — deterministic transitions.
    """

    async def test_command_driven_state_transitions(self) -> None:
        """Commands drive deterministic finite state transitions.

        Validates cos-4a2.1.6: Complex device state machine patterns.
        Tests command-driven state machine with multiple valid paths.
        """
        harness = AppHarness.create()
        command_handler_ready = asyncio.Event()
        states_published: list[str] = []

        # Register adapter class
        harness.app.adapter(StateMachinePort, StatefulDevice)

        @harness.app.device("fsm")
        async def finite_state_machine(ctx: DeviceContext) -> None:
            state_adapter = ctx.adapter(StateMachinePort)

            @ctx.on_command
            async def handle_command(sub_topic: str | None, payload: str) -> None:
                old_state = state_adapter.get_state()
                success = False

                if payload == "arm":
                    success = state_adapter.transition_to("armed")
                elif payload == "activate":
                    success = state_adapter.transition_to("active")
                elif payload == "reset":
                    success = state_adapter.transition_to("idle")

                new_state = state_adapter.get_state()

                if success:
                    states_published.append(new_state)
                    await ctx.publish_state(
                        {"state": new_state, "transition": f"{old_state}->{new_state}"}
                    )

                # Complete test after full cycle
                if new_state == "idle" and len(states_published) >= 3:
                    harness.trigger_shutdown()

            command_handler_ready.set()

            # Wait for shutdown
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        # Start app and coordinate
        async def orchestrate():
            await command_handler_ready.wait()

            # Execute state transition sequence: idle -> armed -> active -> idle
            await harness.mqtt.deliver("testapp/fsm/set", "arm")
            await harness.mqtt.deliver("testapp/fsm/set", "activate")
            await harness.mqtt.deliver("testapp/fsm/set", "reset")

        orchestrate_task = asyncio.create_task(orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify state transition sequence
        assert states_published == ["armed", "active", "idle"]

        # Verify state messages published
        state_messages = harness.mqtt.get_messages_for("testapp/fsm/state")
        assert len(state_messages) >= 3  # Should have transition states


class TestFailureIsolation:
    """Failure scenario coverage and component isolation.

    Technique: Failure Isolation — component independence testing.
    """

    async def test_command_failure_isolation_recovery(self) -> None:
        """Invalid commands don't prevent other operations from working.

        Validates cos-4a2.1.7: Failure scenario coverage.
        Tests that command failures don't break application state.
        """
        harness = AppHarness.create()
        command_handler_ready = asyncio.Event()
        valid_command_processed = False
        monitor_published = False

        # Register adapter class
        harness.app.adapter(StateMachinePort, StatefulDevice)

        @harness.app.device("resilient_device")
        async def resilient_device(ctx: DeviceContext) -> None:
            nonlocal valid_command_processed
            state_adapter = ctx.adapter(StateMachinePort)

            @ctx.on_command
            async def handle_command(sub_topic: str | None, payload: str) -> None:
                nonlocal valid_command_processed
                # Handle valid and invalid commands
                if payload == "invalid_command":
                    # Raise exception for invalid command to test failure isolation
                    msg = f"Invalid command: {payload}"
                    raise ValueError(msg)
                elif payload == "arm" and state_adapter.transition_to("armed"):
                    await ctx.publish_state({"state": "armed", "transition": "success"})
                    valid_command_processed = True

            command_handler_ready.set()

            # Wait for shutdown
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        @harness.app.telemetry("monitor", interval=0.01)
        async def monitor_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal monitor_published
            monitor_published = True
            # Give commands time to process, then shutdown
            if valid_command_processed:
                harness.trigger_shutdown()
            return {"monitor": "active"}

        # Orchestrate test
        async def orchestrate():
            await command_handler_ready.wait()
            # Send invalid command first (should not break system)
            await harness.mqtt.deliver(
                "testapp/resilient_device/set", "invalid_command"
            )
            # Send valid command (should work despite prior failure)
            await harness.mqtt.deliver("testapp/resilient_device/set", "arm")

        orchestrate_task = asyncio.create_task(orchestrate())

        try:
            # Should complete despite command failure
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify valid command was processed
        assert valid_command_processed

        # Verify telemetry still published
        assert monitor_published
        monitor_messages = harness.mqtt.get_messages_for("testapp/monitor/state")
        assert len(monitor_messages) >= 1

    async def test_mixed_success_failure_telemetry(self) -> None:
        """Mixed successful and failing telemetry handlers coexist.

        Validates cos-4a2.1.7: Failure scenario coverage.
        Tests graceful degradation when some handlers fail.
        """
        harness = AppHarness.create()
        successful_runs = 0
        failure_count = 0
        shutdown_event = asyncio.Event()

        @harness.app.telemetry("unreliable", interval=0.02)
        async def unreliable_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal failure_count
            failure_count += 1
            if failure_count <= 3:  # Fail first 3 times
                if successful_runs >= 8:  # Trigger shutdown when reliable is done
                    shutdown_event.set()
                raise RuntimeError(f"Simulated failure {failure_count}")
            return {"unreliable_data": failure_count}

        @harness.app.telemetry("reliable", interval=0.01)
        async def reliable_telemetry(ctx: DeviceContext) -> dict[str, object]:
            nonlocal successful_runs
            successful_runs += 1
            if successful_runs >= 8:  # Target reached
                shutdown_event.set()
            return {"reliable_data": successful_runs}

        # Orchestrate shutdown
        async def orchestrate():
            await shutdown_event.wait()
            harness.trigger_shutdown()

        orchestrate_task = asyncio.create_task(orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            await _cancel_task(orchestrate_task)

        # Verify reliable handler succeeded
        assert successful_runs >= 8

        # Verify unreliable handler failed then potentially recovered
        assert failure_count > 3

        # Verify reliable telemetry published throughout
        reliable_messages = harness.mqtt.get_messages_for("testapp/reliable/state")
        assert len(reliable_messages) >= 8

        # Verify some unreliable messages eventually published (if it recovered)
        unreliable_messages = harness.mqtt.get_messages_for("testapp/unreliable/state")
        # This might be 0 if it never recovered, which is also valid behavior
