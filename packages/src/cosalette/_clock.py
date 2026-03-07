"""Monotonic clock port and system adapter.

Provides ClockPort (Protocol) and SystemClock for measuring elapsed time
and async sleep.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Monotonic clock for timing measurements.

    Used by device controllers and timing-sensitive components to
    measure elapsed time without being affected by system clock
    adjustments (NTP, manual changes, etc.).

    The default implementation wraps ``time.monotonic()``. Tests
    inject a deterministic fake clock for reproducible timing.
    """

    def now(self) -> float:
        """Return monotonic time in seconds.

        Returns:
            A float representing seconds from an arbitrary epoch.
            Only the *difference* between two calls is meaningful.
        """
        ...

    async def sleep(self, seconds: float) -> None:
        """Sleep for *seconds*.

        Used by :meth:`DeviceContext.sleep` for shutdown-aware sleeping.
        Production implementations delegate to ``asyncio.sleep``; test
        doubles may advance virtual time instead.
        """
        ...


class SystemClock:
    """Production clock wrapping ``time.monotonic()``.

    Satisfies :class:`ClockPort` via structural subtyping.

    Usage::

        clock = SystemClock()
        start = clock.now()
        # ... some work ...
        elapsed = clock.now() - start
    """

    def now(self) -> float:
        """Return monotonic time in seconds."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Sleep for *seconds* using ``asyncio.sleep``."""
        await asyncio.sleep(seconds)
