"""Unit tests for cosalette._stream — streaming primitives.

Test Techniques Used:
    - Specification-based Testing: Verifying StreamablePort protocol
      contract and Stream async-iterator behaviour.
    - Protocol Conformance: isinstance checks for structural subtyping.
    - State-based Testing: shutdown event priority over queued items.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from cosalette._stream import Stream, StreamablePort

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConcretePort:
    """Minimal class satisfying StreamablePort for protocol tests."""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def start_scan(self) -> None: ...
    def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[[int], None]) -> None: ...


# ---------------------------------------------------------------------------
# StreamablePort protocol
# ---------------------------------------------------------------------------


class TestStreamablePortProtocol:
    """Tests for StreamablePort structural protocol."""

    def test_concrete_class_satisfies_protocol(self) -> None:
        """A class with all five methods satisfies StreamablePort."""
        port = _ConcretePort()
        assert isinstance(port, StreamablePort)

    def test_missing_method_does_not_satisfy_protocol(self) -> None:
        """A class missing a required method does not satisfy StreamablePort."""

        class _Incomplete:
            def open(self) -> None: ...
            def close(self) -> None: ...
            def start_scan(self) -> None: ...
            def stop_scan(self) -> None: ...

            # register_callback missing

        assert not isinstance(_Incomplete(), StreamablePort)

    def test_is_runtime_checkable(self) -> None:
        """StreamablePort can be used in isinstance checks at runtime."""
        # isinstance on a Protocol raises TypeError if not @runtime_checkable
        # — this assertion proves the decorator is present.
        assert isinstance(_ConcretePort(), StreamablePort) is True


# ---------------------------------------------------------------------------
# Stream async iterator
# ---------------------------------------------------------------------------


class TestStream:
    """Tests for Stream[T] push-to-pull bridge."""

    async def test_yields_single_item_then_shuts_down(self) -> None:
        """A single item put while the iterator is waiting is received,
        then shutdown terminates the loop cleanly.
        """
        stream: Stream[int] = Stream()
        received = asyncio.Event()

        async def _producer() -> None:
            stream.put(99)
            await received.wait()  # wait until consumer received the item
            stream.shutdown()

        task = asyncio.create_task(_producer())
        result = []
        async for item in stream:
            result.append(item)
            received.set()  # signal producer: item consumed
        await task

        assert result == [99]

    async def test_shutdown_stops_waiting_iteration(self) -> None:
        """shutdown() while waiting for next item stops the iterator."""
        stream: Stream[str] = Stream()

        async def _shutdown_soon() -> None:
            stream.shutdown()

        asyncio.create_task(_shutdown_soon())
        items = []
        async for item in stream:
            items.append(item)

        assert items == []

    async def test_shutdown_before_put_stops_immediately(self) -> None:
        """shutdown() before any put() yields nothing."""
        stream: Stream[int] = Stream()
        stream.shutdown()

        items = []
        async for item in stream:
            items.append(item)

        assert items == []

    async def test_shutdown_is_idempotent(self) -> None:
        """Calling shutdown() multiple times does not raise."""
        stream: Stream[int] = Stream()
        stream.shutdown()
        stream.shutdown()  # second call must not raise

    async def test_aiter_returns_self(self) -> None:
        """__aiter__ returns the same Stream instance."""
        stream: Stream[int] = Stream()
        assert stream.__aiter__() is stream

    async def test_put_from_callback_is_received(self) -> None:
        """Items put by a registered callback are yielded by the iterator."""
        stream: Stream[int] = Stream()
        received = asyncio.Event()

        class _Port:
            def __init__(self) -> None:
                self._cb: Callable[[int], None] | None = None

            def open(self) -> None: ...
            def close(self) -> None: ...
            def start_scan(self) -> None: ...
            def stop_scan(self) -> None: ...

            def register_callback(self, cb: Callable[[int], None]) -> None:
                self._cb = cb

            def fire(self, item: int) -> None:
                assert self._cb is not None
                self._cb(item)

        port = _Port()
        port.register_callback(stream.put)

        async def _driver() -> None:
            port.fire(42)
            await received.wait()  # wait until consumer received 42
            stream.shutdown()

        task = asyncio.create_task(_driver())
        items = []
        async for item in stream:
            items.append(item)
            received.set()  # signal driver: item consumed
        await task

        assert items == [42]

    async def test_shutdown_during_iteration_stops_loop(self) -> None:
        """Shutdown signalled from within the loop stops further iteration."""
        stream: Stream[int] = Stream()
        stream.put(10)
        stream.put(20)

        seen = []
        async for item in stream:
            seen.append(item)
            if item == 10:
                stream.shutdown()

        assert seen == [10]

    async def test_concurrent_put_and_shutdown(self) -> None:
        """Concurrent put() and shutdown() don't deadlock or lose the event."""
        stream: Stream[int] = Stream()

        async def _producer() -> None:
            for i in range(5):
                stream.put(i)
                await asyncio.sleep(0)
            stream.shutdown()

        asyncio.create_task(_producer())
        items = []
        async for item in stream:
            items.append(item)

        # At least one item is guaranteed before shutdown fires.
        assert 1 <= len(items) <= 5

    async def test_maxsize_raises_queue_full(self) -> None:
        """put() raises QueueFull when maxsize is exhausted."""
        stream: Stream[int] = Stream(maxsize=1)
        stream.put(1)
        with pytest.raises(asyncio.QueueFull):
            stream.put(2)
        stream.shutdown()

    async def test_thread_safe_constructs_in_running_loop(self) -> None:
        """Stream(thread_safe=True) constructs without error inside a running loop."""
        stream: Stream[int] = Stream(thread_safe=True)
        stream.shutdown()
