"""Signal filters for smoothing and conditioning telemetry data.

Provides reusable filter primitives for common IoT signal processing
tasks — noise smoothing, spike rejection, and adaptive filtering.
Filters are domain-level utilities: they transform data inside device
handlers, complementing the framework's publish strategies that control
*when* data is published.

See ADR-014 for design rationale.

Filters provided:
    - ``Pt1Filter`` — first-order low-pass (time-constant-based EWMA)
    - ``MedianFilter`` — sliding-window median (spike rejection)
    - ``OneEuroFilter`` — adaptive low-pass (1€ Filter)
"""

from __future__ import annotations

import math
import statistics
from collections import deque
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


# ---------------------------------------------------------------------------
# Median filter
# ---------------------------------------------------------------------------


class MedianFilter:
    """Sliding-window median filter for spike rejection.

    Maintains a window of the last *k* values and returns their median.

    Args:
        window: Number of samples in the sliding window.

    Raises:
        TypeError: If *window* is a ``bool``.
        ValueError: If *window* is less than 1.
    """

    __slots__ = ("_buffer", "_value", "_window")

    def __init__(self, window: int) -> None:
        if isinstance(window, bool):
            msg = f"window must be an int, got bool: {window!r}"
            raise TypeError(msg)
        if not isinstance(window, int):
            msg = f"window must be an int, got {type(window).__name__}: {window!r}"
            raise TypeError(msg)
        if window < 1:
            msg = f"window must be >= 1, got {window!r}"
            raise ValueError(msg)

        self._window = window
        self._buffer: deque[float] = deque(maxlen=window)
        self._value: float | None = None

    # -- Read-only properties ------------------------------------------------

    @property
    def window(self) -> int:
        """Number of samples in the sliding window."""
        return self._window

    @property
    def value(self) -> float | None:
        """Current filtered value, or ``None`` before the first update."""
        return self._value

    # -- Mutating methods ----------------------------------------------------

    def update(self, raw: float) -> float:
        """Feed a raw measurement and return the median of the window.

        During warmup the median is computed over available samples.
        """
        self._buffer.append(raw)
        self._value = statistics.median(self._buffer)
        return self._value

    def reset(self) -> None:
        """Clear internal state so the next ``update`` re-seeds."""
        self._buffer.clear()
        self._value = None


# ---------------------------------------------------------------------------
# 1€ (One Euro) adaptive low-pass filter
# ---------------------------------------------------------------------------


def _alpha_from_cutoff(cutoff: float, dt: float) -> float:
    """Compute smoothing factor from cutoff frequency and sample interval."""
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return dt / (tau + dt)


class OneEuroFilter:
    """Adaptive low-pass filter (1€ Filter).

    Smooths heavily when the signal is stable and tracks rapidly when
    it changes — ideal for sensors that are mostly static but experience
    occasional real movement (temperature, humidity, barometric pressure).

    Based on Casiez, Roussel & Vogel (2012): "1€ Filter: A Simple
    Speed-based Low-pass Filter for Noisy Input in Interactive Systems."

    The filter uses two internal PT1 stages:

    1. A derivative estimator (PT1 with cutoff ``d_cutoff``) to measure
       how fast the signal is changing.
    2. A signal filter (PT1 with adaptive cutoff) that automatically
       adjusts smoothing: low cutoff (heavy smoothing) when the signal
       is stable, high cutoff (light smoothing) when it moves.

    The relationship: ``cutoff = min_cutoff + beta * |derivative|``

    Args:
        min_cutoff: Minimum cutoff frequency in Hz. Controls smoothing
            when the signal is stable. Lower → smoother. Default ``1.0``.
        beta: Speed coefficient. Controls how much the cutoff increases
            when the signal changes rapidly. ``0.0`` disables adaptation
            (pure low-pass). Default ``0.0``.
        d_cutoff: Cutoff frequency for the derivative estimator in Hz.
            Default ``1.0``.
        dt: Sample interval in seconds. Default ``1.0``.

    Raises:
        TypeError: If any numeric parameter is a ``bool``.
        ValueError: If *min_cutoff*, *d_cutoff*, or *dt* is not positive.
        ValueError: If *beta* is negative.
    """

    __slots__ = (
        "_beta",
        "_d_cutoff",
        "_dt",
        "_dx_filtered",
        "_min_cutoff",
        "_prev_raw",
        "_value",
    )

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
        dt: float = 1.0,
    ) -> None:
        # Bool guard — bool is a subclass of int in Python.
        for name, val in (
            ("min_cutoff", min_cutoff),
            ("beta", beta),
            ("d_cutoff", d_cutoff),
            ("dt", dt),
        ):
            if isinstance(val, bool):
                msg = f"{name} must be a number, got bool: {val!r}"
                raise TypeError(msg)

        if min_cutoff <= 0:
            msg = f"min_cutoff must be positive, got {min_cutoff!r}"
            raise ValueError(msg)
        if beta < 0:
            msg = f"beta must be non-negative, got {beta!r}"
            raise ValueError(msg)
        if d_cutoff <= 0:
            msg = f"d_cutoff must be positive, got {d_cutoff!r}"
            raise ValueError(msg)
        if dt <= 0:
            msg = f"dt must be positive, got {dt!r}"
            raise ValueError(msg)

        self._min_cutoff = float(min_cutoff)
        self._beta = float(beta)
        self._d_cutoff = float(d_cutoff)
        self._dt = float(dt)
        self._value: float | None = None
        self._prev_raw: float | None = None
        self._dx_filtered: float = 0.0

    # -- Read-only properties ------------------------------------------------

    @property
    def min_cutoff(self) -> float:
        """Minimum cutoff frequency in Hz."""
        return self._min_cutoff

    @property
    def beta(self) -> float:
        """Speed coefficient."""
        return self._beta

    @property
    def d_cutoff(self) -> float:
        """Cutoff frequency for the derivative estimator in Hz."""
        return self._d_cutoff

    @property
    def dt(self) -> float:
        """Sample interval in seconds."""
        return self._dt

    @property
    def value(self) -> float | None:
        """Current filtered value, or ``None`` before the first update."""
        return self._value

    # -- Mutating methods ----------------------------------------------------

    def update(self, raw: float) -> float:
        """Feed a raw measurement and return the adaptively filtered value.

        The first call seeds the filter — returns ``raw`` unchanged.
        Subsequent calls adapt smoothing based on signal velocity.
        """
        if self._value is None:
            # First call: seed all state.
            self._value = raw
            self._prev_raw = raw
            self._dx_filtered = 0.0
            return self._value

        # 1. Raw derivative.
        assert self._prev_raw is not None  # Invariant: seeded ⟹ prev_raw set
        dx = (raw - self._prev_raw) / self._dt

        # 2. Filter the derivative.
        alpha_d = _alpha_from_cutoff(self._d_cutoff, self._dt)
        self._dx_filtered = alpha_d * dx + (1 - alpha_d) * self._dx_filtered

        # 3. Adaptive cutoff.
        cutoff = self._min_cutoff + self._beta * abs(self._dx_filtered)

        # 4. Adaptive alpha and signal filtering.
        alpha = _alpha_from_cutoff(cutoff, self._dt)
        self._value = alpha * raw + (1 - alpha) * self._value

        # 5. Store previous raw.
        self._prev_raw = raw
        return self._value

    def reset(self) -> None:
        """Clear internal state so the next ``update`` re-seeds."""
        self._value = None
        self._prev_raw = None
        self._dx_filtered = 0.0
