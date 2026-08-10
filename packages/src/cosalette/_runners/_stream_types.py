"""Push-to-pull streaming primitives.

Provides two public types for hardware ports that deliver data via push
callbacks (BLE notify, serial events, HID reports):

- :class:`StreamablePort` — a Protocol defining the async open/close
  lifecycle and callback-registration contract.
- :class:`Stream` — a concrete ``AsyncIterator[T]`` that bridges sync
  push callbacks into ``async for`` loops via an ``asyncio.Queue`` and
  an ``asyncio.Event`` for clean shutdown.

See ADR-042 and ADR-045 for design rationale.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Literal, Protocol

BackpressurePolicy = Literal["drop_newest", "drop_oldest", "raise"]

logger = logging.getLogger(__name__)

_SENTINEL: object = object()


def apply_backpressure[T](
    queue: asyncio.Queue[T],
    item: T,
    policy: BackpressurePolicy,
    *,
    on_evict: Callable[[], None] | None = None,
    log_label: str = "item",
) -> None:
    """Enqueue *item* honoring *policy*; no-op when queue is unbounded.

    When ``maxsize > 0`` and the queue is full:

    - ``"raise"``: ``put_nowait`` raises :exc:`asyncio.QueueFull`.
    - ``"drop_newest"``: discard *item* (DEBUG log).
    - ``"drop_oldest"``: evict the oldest (calling *on_evict* if given,
      e.g. ``queue.task_done`` to keep :meth:`asyncio.Queue.join` balanced),
      then enqueue *item*.

    When ``maxsize == 0`` the policy is never evaluated.
    """
    if queue.maxsize > 0 and queue.full():
        if policy == "raise":
            queue.put_nowait(item)  # raises QueueFull
            return
        if policy == "drop_newest":
            logger.debug("%s dropped (drop_newest: queue full)", log_label)
            return
        # drop_oldest
        queue.get_nowait()
        if on_evict is not None:
            on_evict()
        logger.debug("%s oldest evicted (drop_oldest: queue full)", log_label)
        queue.put_nowait(item)
        return
    queue.put_nowait(item)


class StreamablePort[T_co](Protocol):
    """Contract for hardware ports that push data via callbacks.

    Implementers open a connection, optionally start and stop a hardware
    scan (e.g. BLE discovery, USB enumeration), and let callers register
    a callback that fires for every inbound datum.

    Lifecycle::

        await port.open()
        port.register_callback(stream.put)
        await port.start_scan()
        ...
        await port.stop_scan()
        await port.close()

    ``T_co`` is the type of item produced by the port (covariant: a port
    of ``Sensor`` satisfies ``StreamablePort[BaseSensor]``).
    """

    async def open(self) -> None:
        """Open the hardware connection."""
        ...

    async def close(self) -> None:
        """Close the hardware connection and release resources."""
        ...

    async def start_scan(self) -> None:
        """Begin emitting data (start scan / polling loop)."""
        ...

    async def stop_scan(self) -> None:
        """Stop emitting data without closing the connection."""
        ...

    def register_callback(self, cb: Callable[[T_co], None]) -> None:
        """Register *cb* to be called for each inbound datum.

        Args:
            cb: Sync callable invoked with each item.  The callback must
                not block; hardware callbacks are inherently synchronous.
                Use :class:`Stream` to bridge into async code.
        """
        ...


class Stream[T]:
    """Async iterator backed by a push-callback bridge.

    Bridges hardware callbacks (sync :meth:`put`) into ``async for``
    loops.  Shutdown is signalled once via :meth:`shutdown`;
    ``__anext__`` then raises :exc:`StopAsyncIteration` and all further
    iteration stops.  Shutdown is **immediate** — items still in the
    queue at shutdown are discarded, not drained.

    Args:
        maxsize: Maximum number of items buffered before :meth:`put`
            raises :exc:`asyncio.QueueFull`.  ``0`` (default) means
            unbounded, matching :class:`asyncio.Queue` semantics.
        backpressure: Policy applied when ``maxsize > 0`` and the queue
            is full.  ``"raise"`` (default) raises
            :exc:`asyncio.QueueFull` to preserve pre-backpressure
            behaviour.  ``"drop_newest"`` silently discards the incoming
            item.  ``"drop_oldest"`` evicts the oldest queued item to
            make room for the incoming one.  When :class:`Stream` is
            created by ``@app.stream``, the decorator defaults to
            ``"drop_newest"`` — a safer choice for IoT producers that
            cannot block.  The policy is a no-op when ``maxsize=0``.
        thread_safe: If ``True``, :meth:`put` may be called from any
            OS thread.  The stream captures the running event loop at
            construction time and uses
            :meth:`~asyncio.AbstractEventLoop.call_soon_threadsafe` to
            marshal enqueue calls.  When ``False`` (default), :meth:`put`
            must be called from the event-loop thread.

    The iterator uses a sentinel-value pattern: :meth:`shutdown` enqueues
    a module-level ``_SENTINEL`` object into the queue, so a waiting
    ``__anext__`` wakes immediately without creating extra tasks or sets.

    Typical usage::

        stream: Stream[SensorReading] = Stream()
        port.register_callback(stream.put)
        port.open()
        port.start_scan()
        async for reading in stream:
            ...  # process each pushed item
    """

    def __init__(
        self,
        *,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "raise",
        thread_safe: bool = False,
    ) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._backpressure = backpressure
        self._shutdown: asyncio.Event = asyncio.Event()
        self._thread_safe = thread_safe
        if thread_safe:
            self._loop = asyncio.get_running_loop()

    def put(self, item: T) -> None:
        """Push *item* onto the queue (sync, never blocks).

        When *thread_safe=True* was passed at construction, this method
        is safe to call from any OS thread.  Otherwise it must be called
        from the event-loop thread.  For off-loop use without
        *thread_safe*::

            loop.call_soon_threadsafe(stream.put, item)

        The backpressure policy takes effect when ``maxsize > 0`` and
        the queue is full:

        - ``"raise"`` — raises :exc:`asyncio.QueueFull` (sync mode) or
          surfaces the exception on the event-loop thread (thread-safe
          mode).
        - ``"drop_newest"`` — the incoming *item* is discarded; a DEBUG
          log is emitted.
        - ``"drop_oldest"`` — the oldest queued item is evicted and
          *item* is enqueued; a DEBUG log is emitted.

        When ``maxsize=0`` (unbounded) the policy is never evaluated.

        Raises:
            asyncio.QueueFull: When *maxsize* > 0, the queue is full,
                and *backpressure* is ``"raise"``.  In thread-safe mode
                the exception surfaces on the event-loop thread.
        """
        if self._thread_safe:
            self._loop.call_soon_threadsafe(self._enqueue_with_policy, item)
        else:
            self._enqueue_with_policy(item)

    def _enqueue_with_policy(self, item: T) -> None:
        """Apply backpressure policy and enqueue *item*.

        Must run on the event-loop thread.
        """
        apply_backpressure(self._queue, item, self._backpressure, log_label="Stream")

    def shutdown(self) -> None:
        """Signal the iterator to stop.

        Idempotent.  Once set, ``__anext__`` raises
        :exc:`StopAsyncIteration` on the next call.  Any items still in
        the queue are discarded — shutdown is immediate, not draining.
        Must be called from the event-loop thread.
        """
        self._shutdown.set()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_SENTINEL)  # ty: ignore[invalid-argument-type]

    def __aiter__(self) -> Stream[T]:
        return self

    async def __anext__(self) -> T:
        if self._shutdown.is_set():
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item  # type: ignore[return-value]
