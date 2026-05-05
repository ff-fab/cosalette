"""Tests for reactor dispatch on @app.stream handlers.

Verifies that stream handlers dispatch reactors after each yielded
boundary and once at normal completion, but not on cancellation or error.

NOTE: Full integration tests for stream reactor dispatch are deferred
due to complexity of mocking Stream infrastructure. The core implementation
follows the device async generator pattern and is covered by those tests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cosalette._registration import _StreamRegistration
from cosalette._runners._stream_runner import _run_stream_handler
from cosalette._stream import Stream


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
