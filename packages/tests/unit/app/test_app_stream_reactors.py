"""Tests for reactor dispatch on @app.stream handlers.

Includes validation coverage plus a focused runner-level reactor dispatch test.

Test Techniques Used:
- Specification-based Testing: Verifying accepted stream handler shapes and
    reactor dispatch at yielded boundaries.
- Equivalence Partitioning: Coroutine, invalid scalar, async generator, and
    custom async-iterable handler return forms.
- State Transition Testing: Per-yield stream boundaries and final completion
    drain behavior for registered reactors.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

import cosalette
from cosalette._registration import _StreamRegistration
from cosalette._runners._stream_runner import _run_stream_handler
from cosalette._stream import Stream


@dataclass
class MockStreamState:
    """State object for stream reactor dispatch tests."""

    _pending_events: list[str] = field(default_factory=list)
    drain_calls: int = 0

    def drain_events(self) -> list[str]:
        """Drain and count pending events."""
        self.drain_calls += 1
        events = self._pending_events.copy()
        self._pending_events.clear()
        return events

    def add_event(self, event: str) -> None:
        """Record a pending event."""
        self._pending_events.append(event)


@pytest.mark.asyncio
class TestStreamHandlerValidation:
    """Tests for stream handler type validation (breaking change)."""

    async def test_coroutine_style_handler_raises_typeerror(self) -> None:
        """Verify coroutine-style handlers are rejected with clear TypeError."""

        async def coroutine_handler(stream: Stream[str]) -> None:
            """Old coroutine-style handler (no yield)."""
            await asyncio.sleep(0.01)

        # Create a mock registration
        reg = _StreamRegistration(
            name="test",
            func=coroutine_handler,
            injection_plan=[("stream", Stream[str])],
            enabled_spec=True,
            maxsize=0,
            backpressure="drop_newest",
            summary=None,
            behavior=None,
            effects=None,
        )

        # Create a mock stream
        stream: Stream[str] = Stream(maxsize=0)
        providers: dict[type, Any] = {}
        reactors = []

        with pytest.raises(TypeError, match="must return an async generator"):
            await _run_stream_handler(reg, stream, providers, reactors)

    async def test_non_async_iterable_raises_typeerror(self) -> None:
        """Verify non-async-iterable return values are rejected."""

        # Returns str, not AsyncIterable
        async def bad_handler(stream: Stream[str]) -> str:
            return "not_async_iterable"

        reg = _StreamRegistration(
            name="test",
            func=bad_handler,
            injection_plan=[("stream", Stream[str])],
            enabled_spec=True,
            maxsize=0,
            backpressure="drop_newest",
            summary=None,
            behavior=None,
            effects=None,
        )

        stream: Stream[str] = Stream(maxsize=0)
        providers: dict[type, Any] = {}
        reactors = []

        with pytest.raises(TypeError, match="must return an async generator"):
            await _run_stream_handler(reg, stream, providers, reactors)

    async def test_async_generator_handler_accepted(self) -> None:
        """Verify async generator handlers are accepted."""

        async def async_gen_handler(stream: Stream[str]):
            """New async generator style handler."""
            yield  # Valid async generator

        reg = _StreamRegistration(
            name="test",
            func=async_gen_handler,
            injection_plan=[("stream", Stream[str])],
            enabled_spec=True,
            maxsize=0,
            backpressure="drop_newest",
            summary=None,
            behavior=None,
            effects=None,
        )

        stream: Stream[str] = Stream(maxsize=0)
        providers: dict[type, Any] = {}
        reactors = []

        # Should not raise - handler completes normally
        await _run_stream_handler(reg, stream, providers, reactors)

    async def test_custom_async_iterable_accepted(self) -> None:
        """Verify custom AsyncIterable implementations are accepted."""

        class CustomAsyncIterable:
            """Custom async iterable for testing."""

            def __aiter__(self) -> CustomAsyncIterable:
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        def custom_iterable_handler(stream: Stream[str]) -> CustomAsyncIterable:
            """Handler returning custom async iterable (not async)."""
            return CustomAsyncIterable()

        reg = _StreamRegistration(
            name="test",
            func=custom_iterable_handler,
            injection_plan=[("stream", Stream[str])],
            enabled_spec=True,
            maxsize=0,
            backpressure="drop_newest",
            summary=None,
            behavior=None,
            effects=None,
        )

        stream: Stream[str] = Stream(maxsize=0)
        providers: dict[type, Any] = {}
        reactors = []

        # Should not raise - custom async iterable is valid
        await _run_stream_handler(reg, stream, providers, reactors)


@pytest.mark.asyncio
class TestStreamReactorDispatch:
    """Tests for stream reactor dispatch boundaries."""

    async def test_multiple_reactors_share_same_batch_per_yield(self) -> None:
        """Multiple stream reactors receive the same drained batch each boundary."""
        app = cosalette.App(name="test", version="1.0.0")

        @app.state
        def make_state() -> MockStreamState:
            return MockStreamState()

        first_batches: list[list[str]] = []
        second_batches: list[list[str]] = []

        @app.react(MockStreamState)
        async def reactor_one(events: list[str]) -> None:
            first_batches.append(list(events))

        @app.react(MockStreamState)
        async def reactor_two(events: list[str]) -> None:
            second_batches.append(list(events))

        async def stream_handler(stream: Stream[str], state: MockStreamState):
            state.add_event("yield-1")
            yield
            state.add_event("yield-2")
            yield

        reg = _StreamRegistration(
            name="test",
            func=stream_handler,
            injection_plan=[("stream", Stream[str]), ("state", MockStreamState)],
            enabled_spec=True,
            maxsize=0,
            backpressure="drop_newest",
            summary=None,
            behavior=None,
            effects=None,
        )

        state = MockStreamState()
        stream: Stream[str] = Stream(maxsize=0)
        providers: dict[type, Any] = {MockStreamState: state}

        await _run_stream_handler(reg, stream, providers, app._reactors)

        assert first_batches == [["yield-1"], ["yield-2"]]
        assert second_batches == [["yield-1"], ["yield-2"]]
        assert state.drain_calls == 3
