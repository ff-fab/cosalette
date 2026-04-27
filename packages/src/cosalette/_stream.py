"""Push-to-pull streaming primitives.

Provides two public types for hardware ports that deliver data via push
callbacks (BLE notify, serial events, HID reports):

- :class:`StreamablePort` — a runtime-checkable Protocol defining the
  open/close lifecycle and callback-registration contract.
- :class:`Stream` — a concrete ``AsyncIterator[T]`` that bridges sync
  push callbacks into ``async for`` loops via an ``asyncio.Queue`` and
  an ``asyncio.Event`` for clean shutdown.

See ADR-042 for design rationale.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class StreamablePort[T_co](Protocol):
    """Contract for hardware ports that push data via callbacks.

    Implementers open a connection, optionally start and stop a hardware
    scan (e.g. BLE discovery, USB enumeration), and let callers register
    a callback that fires for every inbound datum.

    Lifecycle::

        port.open()
        port.register_callback(stream.put)
        port.start_scan()
        ...
        port.stop_scan()
        port.close()

    ``T_co`` is the type of item produced by the port (covariant: a port
    of ``Sensor`` satisfies ``StreamablePort[BaseSensor]``).
    """

    def open(self) -> None:
        """Open the hardware connection."""
        ...

    def close(self) -> None:
        """Close the hardware connection and release resources."""
        ...

    def start_scan(self) -> None:
        """Begin emitting data (start scan / polling loop)."""
        ...

    def stop_scan(self) -> None:
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
    iteration stops.

    Typical usage::

        stream: Stream[SensorReading] = Stream()
        port.register_callback(stream.put)
        port.open()
        port.start_scan()
        async for reading in stream:
            ...  # process each pushed item

    The iterator races ``queue.get()`` against the shutdown event with
    ``asyncio.wait`` — no timeout polling, no busy-wait.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._shutdown: asyncio.Event = asyncio.Event()

    def put(self, item: T) -> None:
        """Push *item* onto the queue (sync, never blocks).

        Called by hardware callbacks or any sync producer.
        """
        self._queue.put_nowait(item)

    def shutdown(self) -> None:
        """Signal the iterator to stop.

        Idempotent.  Once set, ``__anext__`` raises
        :exc:`StopAsyncIteration` on the next call.
        """
        self._shutdown.set()

    def __aiter__(self) -> Stream[T]:
        return self

    async def __anext__(self) -> T:
        queue_task = asyncio.create_task(self._queue.get())
        shutdown_task = asyncio.create_task(self._shutdown.wait())
        done, pending = await asyncio.wait(
            {queue_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if shutdown_task in done:
            raise StopAsyncIteration
        return queue_task.result()
