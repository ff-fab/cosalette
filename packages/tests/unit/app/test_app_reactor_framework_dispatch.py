"""Tests for reactor dispatch on framework-owned handlers.

(telemetry, command, trigger)

Test Techniques Used:
- Specification-based Testing: Verifying framework-managed handlers dispatch
    reactors only at the documented success boundaries.
- State Transition Testing: Success and failure paths for telemetry and
    command execution, including downstream reactor error publication.
- Branch/Condition Coverage: Reactor dispatch, dispatch suppression,
    grouped telemetry behavior, and dependency injection branches.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

import cosalette
from cosalette.testing import AppHarness


@dataclass
class MockTelemetryTestState:
    """Test state object for telemetry reactor testing."""

    events: list[str] = field(default_factory=list)
    _pending_events: list[str] = field(default_factory=list)

    def drain_events(self) -> list[str]:
        """Drain and clear pending events."""
        events = self._pending_events.copy()
        self._pending_events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Add an event to the pending list."""
        self._pending_events.append(event)


@pytest.mark.asyncio
class TestTelemetryReactorDispatch:
    """Tests for reactor dispatch on telemetry handlers."""

    async def test_telemetry_handler_dispatches_reactors_after_success(self) -> None:
        """Verify telemetry handlers dispatch reactors after successful execution."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        telemetry_done = asyncio.Event()

        @harness.app.telemetry("test_sensor", interval=0.1)
        async def sensor_handler(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("telemetry_executed")
            telemetry_done.set()
            return {"temperature": 22.5}

        async def _shutdown() -> None:
            await telemetry_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have at least one reactor call after telemetry execution
        assert len(reactor_calls) >= 1
        assert "reactor: telemetry_executed" in reactor_calls

    async def test_telemetry_failure_does_not_dispatch_reactors(self) -> None:
        """Verify failed telemetry handlers do not dispatch reactors."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        error_done = asyncio.Event()

        @harness.app.telemetry("test_sensor", interval=0.1)
        async def failing_sensor(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("before_failure")
            error_done.set()
            raise ValueError("sensor failure")

        async def _shutdown() -> None:
            await error_done.wait()
            await asyncio.sleep(0.05)  # Allow error to be published
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have captured the sensor error
        error_messages = harness.mqtt.get_messages_for("testapp/test_sensor/error")
        assert len(error_messages) >= 1
        # LEAK-01: downstream exception text is redacted to the class name.
        assert "ValueError" in error_messages[0][0]
        assert "sensor failure" not in error_messages[0][0]

        # Should not have dispatched reactors since telemetry failed
        assert reactor_calls == []

    async def test_grouped_telemetry_dispatches_reactors(self) -> None:
        """Verify grouped telemetry handlers dispatch reactors after success."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        telemetry_done = asyncio.Event()
        sensor_count = 0

        @harness.app.telemetry("sensor1", interval=0.1, group="test_group")
        async def sensor1_handler(state: MockTelemetryTestState) -> dict[str, Any]:
            nonlocal sensor_count
            state.add_event("sensor1_executed")
            sensor_count += 1
            if sensor_count >= 2:
                telemetry_done.set()
            return {"value": 1}

        @harness.app.telemetry("sensor2", interval=0.1, group="test_group")
        async def sensor2_handler(state: MockTelemetryTestState) -> dict[str, Any]:
            nonlocal sensor_count
            state.add_event("sensor2_executed")
            sensor_count += 1
            if sensor_count >= 2:
                telemetry_done.set()
            return {"value": 2}

        async def _shutdown() -> None:
            await telemetry_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have reactor calls for both sensors
        assert len(reactor_calls) >= 2
        assert "reactor: sensor1_executed" in reactor_calls
        assert "reactor: sensor2_executed" in reactor_calls


@pytest.mark.asyncio
class TestReactorDependencyInjection:
    """Tests for reactor dependency injection with state objects."""

    async def test_reactor_receives_state_and_other_dependencies(self) -> None:
        """Verify reactors can receive state objects and other DI dependencies."""
        captured_injections: dict[str, Any] = {}
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def comprehensive_reactor(
            events: list[str],
            state: MockTelemetryTestState,
            settings: cosalette.Settings,
            ctx: cosalette.DeviceContext,
        ) -> None:
            captured_injections["events"] = events
            captured_injections["state"] = state
            captured_injections["settings"] = settings
            captured_injections["ctx"] = ctx

        device_done = asyncio.Event()

        @harness.app.device("test_device")
        async def device_handler(
            ctx: cosalette.DeviceContext, state: MockTelemetryTestState
        ) -> Any:
            state.add_event("test_event")
            device_done.set()
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Verify all dependencies were injected
        assert "events" in captured_injections
        assert captured_injections["events"] == ["test_event"]
        assert "state" in captured_injections
        assert isinstance(captured_injections["state"], MockTelemetryTestState)
        assert "settings" in captured_injections
        assert isinstance(captured_injections["settings"], cosalette.Settings)
        assert "ctx" in captured_injections
        assert isinstance(captured_injections["ctx"], cosalette.DeviceContext)


@pytest.mark.asyncio
class TestCommandReactorDispatch:
    """Tests for reactor dispatch on command handlers."""

    async def test_command_handler_dispatches_reactors_after_success(self) -> None:
        """Verify command handlers dispatch reactors after successful execution."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        command_done = asyncio.Event()

        @harness.app.command("test_command")
        async def command_handler(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("command_executed")
            command_done.set()
            return {"status": "success"}

        async def _simulate() -> None:
            await asyncio.sleep(0.05)  # Let app start
            await harness.mqtt.deliver("testapp/test_command/set", "trigger")
            await command_done.wait()
            await asyncio.sleep(0.05)  # Allow reactor to execute
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have dispatched reactor after successful command
        assert len(reactor_calls) == 1
        assert "reactor: command_executed" in reactor_calls

        # Should have published the command result
        state_messages = harness.mqtt.get_messages_for("testapp/test_command/state")
        assert len(state_messages) == 1

    async def test_command_failure_does_not_dispatch_reactors(self) -> None:
        """Verify failed command handlers do not dispatch reactors."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        command_done = asyncio.Event()

        @harness.app.command("test_command")
        async def failing_command(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("before_failure")
            command_done.set()
            raise ValueError("command failure")

        async def _simulate() -> None:
            await asyncio.sleep(0.05)  # Let app start
            await harness.mqtt.deliver("testapp/test_command/set", "trigger")
            await command_done.wait()
            await asyncio.sleep(0.05)  # Allow error to be published
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have captured the command error
        error_messages = harness.mqtt.get_messages_for("testapp/test_command/error")
        assert len(error_messages) == 1
        # LEAK-01: downstream exception text is redacted to the class name.
        assert "ValueError" in error_messages[0][0]
        assert "command failure" not in error_messages[0][0]

        # Should not have dispatched reactors since command failed
        assert reactor_calls == []

    async def test_reactor_failure_in_command_path_publishes_error(self) -> None:
        """Verify reactor failures in command path are published as errors."""
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def failing_reactor(events: list[str]) -> None:
            raise RuntimeError("reactor explosion")

        command_done = asyncio.Event()

        @harness.app.command("test_command")
        async def command_handler(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("command_executed")
            command_done.set()
            return {"status": "success"}

        async def _simulate() -> None:
            await asyncio.sleep(0.05)  # Let app start
            await harness.mqtt.deliver("testapp/test_command/set", "trigger")
            await command_done.wait()
            await asyncio.sleep(0.05)  # Allow reactor and error to execute
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have published the command result before reactor failed
        state_messages = harness.mqtt.get_messages_for("testapp/test_command/state")
        assert len(state_messages) == 1

        # Should have published reactor failure as error
        error_messages = harness.mqtt.get_messages_for("testapp/test_command/error")
        assert len(error_messages) == 1
        # LEAK-01: downstream exception text is redacted to the class name.
        assert "RuntimeError" in error_messages[0][0]
        assert "reactor explosion" not in error_messages[0][0]

    async def test_sub_command_dispatches_reactors_after_success(self) -> None:
        """Verify sub-command handlers dispatch reactors after execution."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTelemetryTestState:
            return MockTelemetryTestState()

        @harness.app.react(MockTelemetryTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        command_done = asyncio.Event()

        @harness.app.command("cover", sub="open")
        async def open_cover(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("cover_opened")
            command_done.set()
            return {"position": 100}

        @harness.app.command("cover", sub="close")
        async def close_cover(state: MockTelemetryTestState) -> dict[str, Any]:
            state.add_event("cover_closed")
            return {"position": 0}

        async def _simulate() -> None:
            await asyncio.sleep(0.05)  # Let app start
            await harness.mqtt.deliver("testapp/cover/set", '{"command": "open"}')
            await command_done.wait()
            await asyncio.sleep(0.05)  # Allow reactor to execute
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Should have dispatched reactor after successful sub-command
        assert len(reactor_calls) == 1
        assert "reactor: cover_opened" in reactor_calls

        # Should have published the command result
        state_messages = harness.mqtt.get_messages_for("testapp/cover/state")
        assert len(state_messages) == 1
