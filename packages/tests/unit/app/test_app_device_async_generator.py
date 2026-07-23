"""Tests for @app.device async generator lifecycle and reactor dispatch.

Test Techniques Used:
- Specification-based Testing: Verifying the async-generator-only device
    contract and reactor dispatch at yield boundaries.
- State Transition Testing: Yield-to-yield progression, normal completion,
    and failure paths for device and reactor execution.
- Branch/Condition Coverage: Valid async generators, coroutine rejection,
    invalid return rejection, and propagated exceptions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

import cosalette
from cosalette.testing import AppHarness


@dataclass
class MockTestState:
    """Test state object for reactor testing."""

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
class TestAsyncGeneratorDevice:
    """Tests for async generator device handlers."""

    async def test_async_generator_device_yields_dispatch_reactors(self) -> None:
        """Verify async generator device dispatches reactors after each yield."""
        reactor_calls: list[str] = []
        harness = AppHarness.create()

        @harness.app.state
        def test_state() -> MockTestState:
            return MockTestState()

        @harness.app.react(MockTestState)
        async def test_reactor(events: list[str]) -> None:
            reactor_calls.extend(f"reactor: {event}" for event in events)

        device_done = asyncio.Event()

        @harness.app.device("test_device")
        async def device_handler(
            ctx: cosalette.DeviceContext, state: MockTestState
        ) -> Any:
            state.add_event("step1")
            yield  # Should dispatch reactor here
            state.add_event("step2")
            yield  # Should dispatch reactor here
            state.add_event("step3")
            # Final dispatch happens at completion
            device_done.set()

        async def _shutdown() -> None:
            await device_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Expect reactor calls after each yield plus completion
        assert reactor_calls == [
            "reactor: step1",
            "reactor: step2",
            "reactor: step3",
        ]

    async def test_coroutine_device_raises_typeerror(self) -> None:
        """Verify coroutine-style device handlers raise clear TypeError."""
        harness = AppHarness.create()

        @harness.app.device("test_device")
        async def device_handler(ctx: cosalette.DeviceContext) -> None:
            # This is a coroutine, not an async generator
            pass

        async def _shutdown() -> None:
            await asyncio.sleep(0.1)  # Give time for device to start and error
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Check for error in MQTT error topic
        error_messages = harness.mqtt.get_messages_for("testapp/test_device/error")
        assert len(error_messages) >= 1
        error_content = error_messages[0][0]
        # LEAK-01: exception text is redacted to the class name on the topic.
        assert "TypeError" in error_content
        assert "must return an async generator" not in error_content
        assert "device_handler" not in error_content

    async def test_non_async_device_raises_typeerror(self) -> None:
        """Verify non-async-generator device return raises clear TypeError."""
        harness = AppHarness.create()

        @harness.app.device("test_device")
        async def device_handler() -> str:  # Returns str, not async generator
            return "not_async_iterable"

        async def _shutdown() -> None:
            await asyncio.sleep(0.1)  # Give time for device to start and error
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Check for error in MQTT error topic
        error_messages = harness.mqtt.get_messages_for("testapp/test_device/error")
        assert len(error_messages) >= 1
        error_content = error_messages[0][0]
        # LEAK-01: exception text is redacted to the class name on the topic.
        assert "TypeError" in error_content
        assert "must return an async generator" not in error_content
        assert "device_handler" not in error_content

    async def test_async_generator_with_exception_propagates(self) -> None:
        """Verify exceptions in async generator devices propagate to error publisher."""
        harness = AppHarness.create()
        device_done = asyncio.Event()

        @harness.app.device("test_device")
        async def device_handler() -> Any:
            device_done.set()
            yield
            raise ValueError("test error")

        async def _shutdown() -> None:
            await device_done.wait()
            await asyncio.sleep(0.05)  # Allow error to be published
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Check for error in MQTT error topic
        error_messages = harness.mqtt.get_messages_for("testapp/test_device/error")
        assert len(error_messages) >= 1
        error_content = error_messages[0][0]
        # LEAK-01: exception text is redacted to the class name on the topic.
        assert "ValueError" in error_content
        assert "test error" not in error_content

    async def test_reactor_failure_propagates_to_error_publisher(self) -> None:
        """Verify reactor failures propagate to existing error handling."""
        harness = AppHarness.create()
        device_done = asyncio.Event()

        @harness.app.state
        def test_state() -> MockTestState:
            return MockTestState()

        @harness.app.react(MockTestState)
        async def failing_reactor(events: list[str]) -> None:
            device_done.set()
            raise RuntimeError("reactor failure")

        @harness.app.device("test_device")
        async def device_handler(
            ctx: cosalette.DeviceContext, state: MockTestState
        ) -> Any:
            state.add_event("trigger_failure")
            yield

        async def _shutdown() -> None:
            await device_done.wait()
            await asyncio.sleep(0.05)  # Allow error to be published
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=1.0)

        # Check for error in MQTT error topic
        error_messages = harness.mqtt.get_messages_for("testapp/test_device/error")
        assert len(error_messages) >= 1
        error_content = error_messages[0][0]
        # LEAK-01: exception text is redacted to the class name on the topic.
        assert "RuntimeError" in error_content
        assert "reactor failure" not in error_content
