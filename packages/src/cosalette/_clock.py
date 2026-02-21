"""Monotonic clock port and system adapter.

Provides ClockPort (Protocol) and SystemClock for measuring elapsed time.
"""

from __future__ import annotations

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
