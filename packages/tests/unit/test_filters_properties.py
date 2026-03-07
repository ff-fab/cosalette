"""Property-based tests for signal filters (_filters.py).

Complements the example-based tests in ``test_filters.py`` by verifying
mathematical **invariants** over randomly generated inputs.  Hypothesis
generates hundreds of inputs per property and automatically shrinks
any failure to the minimal reproducing case.

Test Techniques Used:
- Property-based Testing: Verifying invariants over random inputs
- Boundary Value Analysis: Extreme float values, minimal window sizes
- Specification-based Testing: Mathematical contracts of each filter
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cosalette._filters import MedianFilter, OneEuroFilter, Pt1Filter

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Realistic positive floats for filter parameters (tau, dt, cutoff, etc.).
# Exclude subnormals, inf, nan — they don't represent physical time/frequency.
positive_floats = st.floats(
    min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False
)

# Realistic sensor readings — bounded to avoid overflow in arithmetic.
sensor_values = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)

# Non-empty lists of sensor readings for filter sequences.
sensor_sequences = st.lists(sensor_values, min_size=1, max_size=200)


# =============================================================================
# Pt1Filter properties
# =============================================================================


class TestPt1FilterProperties:
    """Property-based tests for the first-order low-pass filter.

    These properties derive directly from the Pt1 recurrence relation:
    ``filtered = alpha * raw + (1 - alpha) * previous`` where
    ``alpha = dt / (tau + dt)``, guaranteeing ``alpha ∈ (0, 1)``.
    """

    @given(tau=positive_floats, dt=positive_floats)
    @settings(max_examples=200)
    def test_alpha_in_open_unit_interval(self, tau: float, dt: float) -> None:
        """alpha = dt/(tau+dt) is always in (0, 1) for positive tau, dt.

        This is the fundamental stability guarantee — if alpha left the
        unit interval, the filter would diverge or oscillate.
        """
        f = Pt1Filter(tau=tau, dt=dt)
        assert 0.0 < f.alpha < 1.0

    @given(raw=sensor_values, tau=positive_floats, dt=positive_floats)
    @settings(max_examples=200)
    def test_seed_passthrough(self, raw: float, tau: float, dt: float) -> None:
        """The first update() returns the raw value unchanged.

        No historical data to smooth against → output == input.
        This is the filter's "seeding" contract.
        """
        f = Pt1Filter(tau=tau, dt=dt)
        result = f.update(raw)
        assert result == raw

    @given(
        constant=sensor_values,
        tau=positive_floats,
        dt=positive_floats,
    )
    @settings(max_examples=200)
    def test_convergence_to_constant_input(
        self, constant: float, tau: float, dt: float
    ) -> None:
        """Feeding a constant value N times → output converges to that value.

        For a Pt1 filter, after enough updates with a constant input c,
        the output approaches c asymptotically.  With 200 updates the
        error should be negligible for any reasonable tau/dt ratio.
        """
        f = Pt1Filter(tau=tau, dt=dt)
        for _ in range(200):
            f.update(constant)

        assert f.value == pytest.approx(constant, abs=1e-3)

    @given(
        seed=sensor_values,
        raw=sensor_values,
        tau=positive_floats,
        dt=positive_floats,
    )
    @settings(max_examples=200)
    def test_output_between_previous_and_input(
        self, seed: float, raw: float, tau: float, dt: float
    ) -> None:
        """After seeding, the next output lies between previous and raw.

        This is a direct consequence of the weighted average formula
        with alpha ∈ (0, 1): the result is a convex combination.
        """
        f = Pt1Filter(tau=tau, dt=dt)
        f.update(seed)
        result = f.update(raw)

        lo = min(seed, raw)
        hi = max(seed, raw)
        assert (
            lo <= result <= hi
            or result == pytest.approx(lo, abs=1e-9)
            or result == pytest.approx(hi, abs=1e-9)
        )


# =============================================================================
# MedianFilter properties
# =============================================================================


class TestMedianFilterProperties:
    """Property-based tests for the sliding-window median filter.

    The median has strong mathematical guarantees: it's always bounded
    by the window's extremes and is robust to outliers (breakdown
    point of 50%).
    """

    @given(
        values=sensor_sequences,
        window=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=200)
    def test_output_bounded_by_window(self, values: list[float], window: int) -> None:
        """The median is always between the min and max of the current window.

        This is a fundamental mathematical property of the median
        statistic — it can never exceed the range of its inputs.
        """
        f = MedianFilter(window=window)
        buf: list[float] = []

        for v in values:
            result = f.update(v)
            buf.append(v)
            # Only the last `window` values matter
            active = buf[-window:]
            assert min(active) <= result <= max(active)

    @given(
        constant=sensor_values,
        window=st.integers(min_value=1, max_value=50),
        n=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    def test_constant_input_returns_constant(
        self, constant: float, window: int, n: int
    ) -> None:
        """Feeding the same value repeatedly → median equals that value.

        Median of [c, c, c, ...] = c for any window size.
        """
        f = MedianFilter(window=window)
        for _ in range(n):
            result = f.update(constant)
            assert result == constant

    @given(raw=sensor_values)
    @settings(max_examples=200)
    def test_seed_passthrough(self, raw: float) -> None:
        """The first update() returns the raw value unchanged.

        With a single element in the buffer, median([x]) = x.
        """
        f = MedianFilter(window=5)
        assert f.update(raw) == raw

    @given(
        baseline=sensor_values,
        window=st.integers(min_value=3, max_value=51).filter(lambda w: w % 2 == 1),
    )
    @settings(max_examples=200)
    def test_spike_rejection_odd_window(self, baseline: float, window: int) -> None:
        """A single outlier in a full odd-sized window cannot change the median.

        With an odd window of size W filled with baseline values, replacing
        one value with an outlier leaves the median at baseline — the
        outlier is outnumbered. This is the key use case for MedianFilter.
        """
        f = MedianFilter(window=window)
        # Fill the window with baseline
        for _ in range(window):
            f.update(baseline)

        # Inject a single spike (far from baseline)
        spike = baseline + 1e6 if baseline < 0 else baseline - 1e6
        f.update(spike)

        # Median should still be baseline (spike is just one value out of W)
        assert f.value == baseline


# =============================================================================
# OneEuroFilter properties
# =============================================================================


class TestOneEuroFilterProperties:
    """Property-based tests for the 1€ adaptive low-pass filter.

    The OneEuroFilter adapts its smoothing based on signal velocity.
    With beta=0 it degenerates to a fixed-cutoff Pt1, which gives us
    testable convergence properties.
    """

    @given(
        raw=sensor_values,
        min_cutoff=positive_floats,
        dt=positive_floats,
    )
    @settings(max_examples=200)
    def test_seed_passthrough(self, raw: float, min_cutoff: float, dt: float) -> None:
        """The first update() returns the raw value unchanged.

        Same seeding contract as all filters.
        """
        f = OneEuroFilter(min_cutoff=min_cutoff, beta=0.0, dt=dt)
        assert f.update(raw) == raw

    @given(
        constant=sensor_values,
        min_cutoff=positive_floats,
        dt=positive_floats,
    )
    @settings(max_examples=200)
    def test_convergence_with_beta_zero(
        self, constant: float, min_cutoff: float, dt: float
    ) -> None:
        """With beta=0, constant input converges to that value.

        beta=0 disables adaptation → the filter becomes a fixed-cutoff
        Pt1.  The convergence property is identical.
        """
        f = OneEuroFilter(min_cutoff=min_cutoff, beta=0.0, dt=dt)
        for _ in range(200):
            f.update(constant)

        assert f.value == pytest.approx(constant, abs=1e-3)

    @given(
        constant=sensor_values,
        min_cutoff=positive_floats,
        beta=st.floats(
            min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
        dt=positive_floats,
    )
    @settings(max_examples=200)
    def test_convergence_with_any_beta(
        self, constant: float, min_cutoff: float, beta: float, dt: float
    ) -> None:
        """Constant input converges regardless of beta.

        When the input is constant, the derivative is zero, so the
        adaptive cutoff equals min_cutoff. Convergence is guaranteed.
        """
        f = OneEuroFilter(min_cutoff=min_cutoff, beta=beta, dt=dt)
        for _ in range(200):
            f.update(constant)

        assert f.value == pytest.approx(constant, abs=1e-3)
