"""Deterministic fake clock for testing.

Satisfies ClockPort (PEP 544 structural subtyping) with a manually
controllable time value — no real time dependency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeClock:
    """Test double for ClockPort.

    Attributes:
        _time: The current "now" value returned by ``now()``.
            Set directly or via the constructor to control time
            in tests.

    Example::

        clock = FakeClock(42.0)
        assert clock.now() == 42.0
        clock._time = 99.0
        assert clock.now() == 99.0
    """

    _time: float = 0.0

    def now(self) -> float:
        """Return the manually set time value."""
        return self._time
