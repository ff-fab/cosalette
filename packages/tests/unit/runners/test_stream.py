"""Unit tests for cosalette._stream — streaming primitives.

Test Techniques Used:
    - Specification-based Testing: Verifying StreamablePort protocol
      contract and Stream async-iterator behaviour.
    - Equivalence Partitioning: BackpressurePolicy values (drop_newest,
      drop_oldest, raise) form natural equivalence classes.
    - Boundary Value Analysis: queue capacity limits (maxsize=0 unbounded,
      maxsize=1 minimal, maxsize=2 multi-slot).
    - Protocol Conformance: asserting non-runtime-checkable protocol behavior.
    - State-based Testing: shutdown event priority over queued items.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from cosalette._runners._stream_types import (
    BackpressurePolicy,
    Stream,
    StreamablePort,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# StreamablePort protocol
# ---------------------------------------------------------------------------


class TestStreamablePortProtocol:
    """Tests for StreamablePort structural protocol."""

    def test_not_runtime_checkable(self) -> None:
        """StreamablePort is NOT @runtime_checkable — isinstance raises TypeError.

        ADR-045: StreamablePort uses async lifecycle and is deliberately not
        runtime-checkable.  This pins the absence of @runtime_checkable so
        accidental re-addition is caught immediately.

        Test Technique: Protocol Conformance — asserting non-runtime-checkable
        behavior so accidental @runtime_checkable additions are caught.
        """
        with pytest.raises(TypeError):
            isinstance(object(), StreamablePort)  # ty: ignore[isinstance-against-protocol]


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

            async def open(self) -> None: ...
            async def close(self) -> None: ...
            async def start_scan(self) -> None: ...
            async def stop_scan(self) -> None: ...

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

    # ------------------------------------------------------------------
    # Backpressure policy tests
    # ------------------------------------------------------------------

    async def test_backpressure_drop_newest_drops_incoming(self) -> None:
        """drop_newest discards the incoming item when the queue is full."""
        stream: Stream[int] = Stream(maxsize=1, backpressure="drop_newest")
        stream.put(1)  # fills the queue
        stream.put(2)  # dropped — queue already full
        assert stream._queue.qsize() == 1
        assert stream._queue.get_nowait() == 1  # original item preserved
        stream.shutdown()

    async def test_backpressure_drop_oldest_evicts_head(self) -> None:
        """drop_oldest evicts the oldest item to make room for the incoming one."""
        stream: Stream[int] = Stream(maxsize=1, backpressure="drop_oldest")
        stream.put(1)  # fills the queue
        stream.put(2)  # evicts 1, enqueues 2
        assert stream._queue.qsize() == 1
        assert stream._queue.get_nowait() == 2  # new item kept, old discarded
        stream.shutdown()

    async def test_backpressure_drop_oldest_multiple_overflow(self) -> None:
        """drop_oldest with maxsize=2: repeated overflow evicts from head."""
        stream: Stream[int] = Stream(maxsize=2, backpressure="drop_oldest")
        stream.put(1)
        stream.put(2)  # full: [1, 2]
        stream.put(3)  # evicts 1 → [2, 3]
        assert stream._queue.get_nowait() == 2
        assert stream._queue.get_nowait() == 3
        stream.shutdown()

    @pytest.mark.parametrize("policy", ["drop_newest", "drop_oldest", "raise"])
    async def test_backpressure_policy_inert_when_unbounded(
        self, policy: BackpressurePolicy
    ) -> None:
        """All policies are inert on an unbounded queue (maxsize=0)."""
        stream: Stream[int] = Stream(maxsize=0, backpressure=policy)
        for i in range(10):
            stream.put(i)  # never raises, never drops
        assert stream._queue.qsize() == 10
        stream.shutdown()

    async def test_thread_safe_backpressure_drop_newest(self) -> None:
        """thread_safe=True: drop_newest drops incoming item on loop thread."""
        stream: Stream[int] = Stream(
            maxsize=1, backpressure="drop_newest", thread_safe=True
        )
        stream.put(1)  # schedules _enqueue via call_soon_threadsafe
        await asyncio.sleep(0)  # let _enqueue run → fills queue: [1]
        stream.put(2)  # schedules _enqueue → policy: drop incoming
        await asyncio.sleep(0)  # let _enqueue run
        assert stream._queue.qsize() == 1
        assert stream._queue.get_nowait() == 1  # original preserved
        stream.shutdown()

    async def test_thread_safe_backpressure_drop_oldest(self) -> None:
        """thread_safe=True: drop_oldest evicts head item on loop thread."""
        stream: Stream[int] = Stream(
            maxsize=1, backpressure="drop_oldest", thread_safe=True
        )
        stream.put(1)  # schedules _enqueue via call_soon_threadsafe
        await asyncio.sleep(0)  # fills queue: [1]
        stream.put(2)  # schedules _enqueue → evicts 1, enqueues 2
        await asyncio.sleep(0)  # let _enqueue run
        assert stream._queue.qsize() == 1
        assert stream._queue.get_nowait() == 2  # new item kept
        stream.shutdown()
