"""Monotonic clock adapter — time measurement for production use.

Wraps :func:`time.monotonic` in a class satisfying
:class:`~velux2mqtt.ports.protocols.ClockPort`.  A separate
``FakeClock`` for testing lives in the test fixtures, not here —
the infrastructure layer only contains production code.

**Why monotonic?**  ``time.monotonic()`` is immune to NTP adjustments
and manual system-clock changes, making it suitable for measuring
elapsed durations (motor travel time, dead-time compensation).
The epoch is arbitrary — only *differences* between ``now()`` calls
are meaningful (PEP 418).
"""

from __future__ import annotations

import time


class SystemClock:
    """Production clock wrapping ``time.monotonic()``.

    Satisfies :class:`~velux2mqtt.ports.protocols.ClockPort` via
    structural subtyping — no base-class inheritance required.

    Usage::

        clock = SystemClock()
        start = clock.now()
        # ... some work ...
        elapsed = clock.now() - start
    """

    def now(self) -> float:
        """Return monotonic time in seconds."""
        return time.monotonic()
