"""Signal filters for smoothing and conditioning telemetry data.

Provides reusable filter primitives for common IoT signal processing
tasks — noise smoothing, spike rejection, and adaptive filtering.
Filters are domain-level utilities: they transform data inside device
handlers, complementing the framework's publish strategies that control
*when* data is published.

See ADR-014 for design rationale.

Filters provided:
    - ``Pt1Filter`` — first-order low-pass (time-constant-based EWMA)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Filter(Protocol):
    """Signal filter contract.

    All filters follow the ``update → value`` pattern:

    1. Call ``update(raw)`` with each new measurement.
    2. The return value is the filtered output.
    3. Access ``value`` for the current filtered state.
    4. Call ``reset()`` to clear internal state.

    The first ``update()`` call seeds the filter — it returns the raw
    value unchanged (no history to smooth against).
    """

    @property
    def value(self) -> float | None:
        """Current filtered value, or ``None`` before the first update."""
        ...

    def update(self, raw: float) -> float:
        """Feed a raw measurement and return the filtered value."""
        ...

    def reset(self) -> None:
        """Clear internal state so the next update re-seeds."""
        ...


# ---------------------------------------------------------------------------
# PT1 (first-order low-pass) filter
# ---------------------------------------------------------------------------


class Pt1Filter:
    """First-order low-pass filter parameterised by time constant.

    Uses the recurrence relation::

        alpha    = dt / (tau + dt)
        filtered = alpha * raw + (1 - alpha) * previous

    This is equivalent to a standard EWMA but with *alpha* derived from
    the physical time constant *τ* (tau) and the sample interval *dt*,
    making the smoothing behaviour **sample-rate-independent**.

    Args:
        tau: Time constant in seconds (controls smoothing strength).
            Larger values → heavier smoothing.
        dt: Sample interval in seconds (time between updates).

    Raises:
        TypeError: If *tau* or *dt* is a ``bool``.
        ValueError: If *tau* or *dt* is zero or negative.
    """

    __slots__ = ("_alpha", "_dt", "_tau", "_value")

    def __init__(self, tau: float, dt: float) -> None:
        # Bool check before numeric — bool is a subclass of int in Python,
        # so ``isinstance(True, (int, float))`` is True.  We reject bools
        # explicitly because they signal a caller mistake.
        if isinstance(tau, bool):
            msg = f"tau must be a number, got bool: {tau!r}"
            raise TypeError(msg)
        if isinstance(dt, bool):
            msg = f"dt must be a number, got bool: {dt!r}"
            raise TypeError(msg)

        if tau <= 0:
            msg = f"tau must be positive, got {tau!r}"
            raise ValueError(msg)
        if dt <= 0:
            msg = f"dt must be positive, got {dt!r}"
            raise ValueError(msg)

        self._tau = float(tau)
        self._dt = float(dt)
        self._alpha = self._dt / (self._tau + self._dt)
        self._value: float | None = None

    # -- Read-only properties ------------------------------------------------

    @property
    def tau(self) -> float:
        """Time constant τ in seconds."""
        return self._tau

    @property
    def dt(self) -> float:
        """Sample interval in seconds."""
        return self._dt

    @property
    def alpha(self) -> float:
        """Smoothing factor derived from ``dt / (tau + dt)``."""
        return self._alpha

    @property
    def value(self) -> float | None:
        """Current filtered value, or ``None`` before the first update."""
        return self._value

    # -- Mutating methods ----------------------------------------------------

    def update(self, raw: float) -> float:
        """Feed a raw measurement and return the filtered value.

        The first call *seeds* the filter — returns ``raw`` unchanged.
        Subsequent calls apply the low-pass recurrence.
        """
        if self._value is None:
            self._value = raw
        else:
            self._value = self._alpha * raw + (1 - self._alpha) * self._value
        return self._value

    def reset(self) -> None:
        """Clear internal state so the next ``update`` re-seeds."""
        self._value = None
