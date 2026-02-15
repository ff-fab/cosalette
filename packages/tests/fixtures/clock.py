"""Shared clock fixtures for testing.

Provides a deterministic :class:`FakeClock` that satisfies
:class:`~cosalette._clock.ClockPort` without real monotonic time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeClock:
    """Deterministic clock for testing — always returns ``_time``."""

    _time: float = 0.0

    def now(self) -> float:
        return self._time
