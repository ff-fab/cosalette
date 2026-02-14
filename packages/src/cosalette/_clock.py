"""Monotonic clock port and system adapter.

Provides ClockPort (Protocol) and SystemClock for measuring elapsed time.

**Why monotonic?** time.monotonic() is immune to NTP adjustments and
manual system-clock changes, making it suitable for measuring elapsed
durations. The epoch is arbitrary — only *differences* between now()
calls are meaningful (PEP 418).

See Also:
    ADR-006 for hexagonal architecture and Protocol-based ports.
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

    Satisfies :class:`ClockPort` via structural subtyping — no
    base-class inheritance required (PEP 544).

    Usage::

        clock = SystemClock()
        start = clock.now()
        # ... some work ...
        elapsed = clock.now() - start
    """

    def now(self) -> float:
        """Return monotonic time in seconds."""
        return time.monotonic()
