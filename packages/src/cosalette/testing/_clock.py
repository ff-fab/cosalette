"""Deterministic fake clock for testing.

Satisfies ClockPort (PEP 544 structural subtyping) with a manually
controllable time value — no real time dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class FakeClock:
    """Test double for ClockPort.

    Attributes:
        _time: The current "now" value returned by ``now()``.
            Assign it to set virtual time *absolutely*, or call
            :meth:`advance` to move it forward *relatively*.

    Example::

        clock = FakeClock(42.0)
        assert clock.now() == 42.0
        clock.advance(57.0)
        assert clock.now() == 99.0
    """

    _time: float = 0.0

    def now(self) -> float:
        """Return the manually set time value."""
        return self._time

    def advance(self, seconds: float) -> None:
        """Move virtual time forward by *seconds*, without sleeping.

        Relative to the current value, unlike assigning ``_time``,
        which sets virtual time absolutely.  Unlike :meth:`sleep` this
        does not yield to the event loop, so no other task gets to run
        — use it to simulate work that consumed time.

        Args:
            seconds: Virtual seconds to add.  ``0`` is a no-op.

        Raises:
            ValueError: If *seconds* is negative.  A monotonic clock
                never runs backwards.

        Example::

            clock = FakeClock(10.0)
            clock.advance(5.0)
            assert clock.now() == 15.0
        """
        if seconds < 0:
            msg = f"advance() requires a non-negative duration, got {seconds!r}"
            raise ValueError(msg)
        self._time += seconds

    async def sleep(self, seconds: float) -> None:
        """Advance virtual time by *seconds* with no real delay.

        Allows tests to exercise sleep-dependent code paths
        without wall-clock waiting.  The ``asyncio.sleep(0)``
        yields to the event loop so concurrent tasks interleave
        correctly.
        """
        await asyncio.sleep(0)
        if seconds > 0:
            self._time += seconds
