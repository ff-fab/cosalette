"""Unit tests for cosalette._filters — signal filter module.

Test Techniques Used:
    - Specification-based Testing: Protocol conformance, constructor contracts
    - Boundary Value Analysis: tau/dt at zero, negative values
    - Error Guessing: bool-is-int gotcha (Python subclass trap)
    - State Transition Testing: seed → update → reset → re-seed cycle
    - Property-based Reasoning: convergence, sample-rate independence
    - Sliding Window Testing: median correctness over partial/full windows
    - Adaptive Behaviour: 1€ Filter speed-dependent smoothing
"""

from __future__ import annotations

import pytest

from cosalette._filters import (
    Filter,
    MedianFilter,
    OneEuroFilter,
    Pt1Filter,
    _alpha_from_cutoff,
)

# =============================================================================
# Tests
# =============================================================================


class TestFilterProtocol:
    """Verify Filter is a runtime-checkable protocol.

    Technique: Specification-based Testing — structural subtyping checks.
    """

    def test_pt1_satisfies_filter_protocol(self) -> None:
        """Pt1Filter satisfies the Filter protocol via structural subtyping."""
        # Arrange
        f = Pt1Filter(tau=1.0, dt=1.0)

        # Assert
        assert isinstance(f, Filter)


class TestPt1FilterValidation:
    """Constructor validation for tau and dt parameters.

    Technique: Boundary Value Analysis + Error Guessing.
    """

    def test_tau_must_be_positive(self) -> None:
        """tau=0 and tau=-1 both raise ValueError."""
        with pytest.raises(ValueError, match="tau must be positive"):
            Pt1Filter(tau=0, dt=1.0)

        with pytest.raises(ValueError, match="tau must be positive"):
            Pt1Filter(tau=-1, dt=1.0)

    def test_dt_must_be_positive(self) -> None:
        """dt=0 and dt=-1 both raise ValueError."""
        with pytest.raises(ValueError, match="dt must be positive"):
            Pt1Filter(tau=1.0, dt=0)

        with pytest.raises(ValueError, match="dt must be positive"):
            Pt1Filter(tau=1.0, dt=-1)

    def test_bool_tau_raises_type_error(self) -> None:
        """bool tau is rejected — bool is a subclass of int in Python.

        This guards against accidental ``Pt1Filter(tau=True, dt=1.0)``
        where ``True`` would otherwise pass as ``1``.
        """
        with pytest.raises(TypeError, match="tau must be a number, got bool"):
            Pt1Filter(tau=True, dt=1.0)  # type: ignore[arg-type]

    def test_bool_dt_raises_type_error(self) -> None:
        """bool dt is rejected for the same bool-is-int reason."""
        with pytest.raises(TypeError, match="dt must be a number, got bool"):
            Pt1Filter(tau=1.0, dt=True)  # type: ignore[arg-type]


class TestPt1Filter:
    """Functional tests for the PT1 first-order low-pass filter.

    Technique: State Transition Testing + Property-based Reasoning.
    """

    def test_initial_value_is_none(self) -> None:
        """Before any update, value is None."""
        # Arrange
        f = Pt1Filter(tau=1.0, dt=1.0)

        # Assert
        assert f.value is None

    def test_first_update_seeds_filter(self) -> None:
        """First update returns the raw value unchanged and sets value."""
        # Arrange
        f = Pt1Filter(tau=1.0, dt=1.0)

        # Act
        result = f.update(42.0)

        # Assert
        assert result == 42.0
        assert f.value == 42.0

    def test_second_update_applies_formula(self) -> None:
        """With tau=4.0, dt=1.0 → alpha=0.2.

        Feed 10.0 (seed), then 20.0.
        Expected: 0.2 * 20 + 0.8 * 10 = 12.0
        """
        # Arrange
        f = Pt1Filter(tau=4.0, dt=1.0)
        f.update(10.0)  # seed

        # Act
        result = f.update(20.0)

        # Assert
        assert result == pytest.approx(12.0)
        assert f.value == pytest.approx(12.0)

    def test_properties_expose_parameters(self) -> None:
        """tau, dt, and alpha properties return correct values."""
        # Arrange
        f = Pt1Filter(tau=4.0, dt=1.0)

        # Assert
        assert f.tau == 4.0
        assert f.dt == 1.0
        assert f.alpha == pytest.approx(0.2)

    def test_alpha_computation(self) -> None:
        """Alpha is correctly computed from tau and dt.

        alpha = dt / (tau + dt):
        - tau=9, dt=1 → 1/10 = 0.1
        - tau=1, dt=1 → 1/2  = 0.5
        """
        # Assert
        assert Pt1Filter(tau=9.0, dt=1.0).alpha == pytest.approx(0.1)
        assert Pt1Filter(tau=1.0, dt=1.0).alpha == pytest.approx(0.5)

    def test_convergence(self) -> None:
        """Filter converges to steady-state input after many iterations.

        Seed with 0.0, then feed constant 50.0 for 200 iterations.
        The output must converge to 50.0 within tight tolerance.
        """
        # Arrange
        f = Pt1Filter(tau=4.0, dt=1.0)
        f.update(0.0)  # seed

        # Act
        for _ in range(200):
            f.update(50.0)

        # Assert
        assert f.value == pytest.approx(50.0, abs=0.01)

    def test_reset_clears_state(self) -> None:
        """After updates, reset() makes value None; next update re-seeds."""
        # Arrange
        f = Pt1Filter(tau=1.0, dt=1.0)
        f.update(10.0)
        f.update(20.0)

        # Act
        f.reset()

        # Assert
        assert f.value is None

        # Re-seed
        result = f.update(99.0)
        assert result == 99.0
        assert f.value == 99.0

    def test_heavy_smoothing(self) -> None:
        """tau=99, dt=1 → alpha=0.01 — second value barely moves from seed.

        Seed with 100.0, update with 0.0.
        Expected: 0.01 * 0 + 0.99 * 100 = 99.0
        """
        # Arrange
        f = Pt1Filter(tau=99.0, dt=1.0)
        f.update(100.0)  # seed

        # Act
        result = f.update(0.0)

        # Assert
        assert result == pytest.approx(99.0)

    def test_fast_tracking(self) -> None:
        """tau=0.1, dt=1.0 → alpha ≈ 0.909 — second value close to new input.

        Seed with 0.0, update with 100.0.
        Expected: (1/1.1)*100 + (0.1/1.1)*0 ≈ 90.909
        """
        # Arrange
        f = Pt1Filter(tau=0.1, dt=1.0)
        f.update(0.0)  # seed

        # Act
        result = f.update(100.0)

        # Assert
        assert result == pytest.approx(100.0 / 1.1, rel=1e-6)

    def test_sample_rate_independence(self) -> None:
        """Same tau gives same effective smoothing at different sample rates.

        Two filters with tau=5s:
        - Filter A: dt=1s, updated 10 times with value 100.0
        - Filter B: dt=2s, updated 5 times with value 100.0

        Both cover the same total elapsed time (10s) and should converge
        to approximately the same filtered value.  This demonstrates the
        key advantage of PT1 over raw EWMA: the smoothing is defined in
        physical time, not in number of samples.
        """
        # Arrange
        fa = Pt1Filter(tau=5.0, dt=1.0)
        fb = Pt1Filter(tau=5.0, dt=2.0)

        fa.update(0.0)  # seed both at 0
        fb.update(0.0)

        # Act — same total elapsed time (10s)
        for _ in range(10):
            fa.update(100.0)
        for _ in range(5):
            fb.update(100.0)

        # Assert — values should be approximately equal
        assert fa.value is not None
        assert fb.value is not None
        assert fa.value == pytest.approx(fb.value, rel=0.05)


# =============================================================================
# MedianFilter tests
# =============================================================================


class TestMedianFilterValidation:
    """Constructor validation for MedianFilter.

    Technique: Boundary Value Analysis + Error Guessing.
    """

    def test_window_must_be_positive(self) -> None:
        """window=0 and window=-1 both raise ValueError."""
        with pytest.raises(ValueError, match="window must be >= 1"):
            MedianFilter(window=0)

        with pytest.raises(ValueError, match="window must be >= 1"):
            MedianFilter(window=-1)

    def test_bool_window_raises_type_error(self) -> None:
        """bool window is rejected — bool is a subclass of int in Python."""
        with pytest.raises(TypeError, match="window must be an int, got bool"):
            MedianFilter(window=True)  # type: ignore[arg-type]

    def test_non_int_window_raises_type_error(self) -> None:
        """Float window is rejected — window must be an exact int."""
        with pytest.raises(TypeError, match="window must be an int, got float"):
            MedianFilter(window=3.5)  # type: ignore[arg-type]


class TestMedianFilter:
    """Functional tests for the sliding-window median filter.

    Technique: State Transition Testing + Boundary Value Analysis.
    """

    def test_initial_value_is_none(self) -> None:
        """Before any update, value is None."""
        f = MedianFilter(window=3)
        assert f.value is None

    def test_first_update_returns_raw(self) -> None:
        """Single value — median of [x] is x."""
        f = MedianFilter(window=5)
        result = f.update(42.0)
        assert result == 42.0
        assert f.value == 42.0

    def test_window_one_returns_raw(self) -> None:
        """window=1 always returns the last raw value."""
        f = MedianFilter(window=1)
        assert f.update(10.0) == 10.0
        assert f.update(20.0) == 20.0
        assert f.update(30.0) == 30.0

    def test_odd_window_median(self) -> None:
        """window=3, feed [10, 20, 30] → median is 20.0."""
        f = MedianFilter(window=3)
        f.update(10.0)
        f.update(20.0)
        result = f.update(30.0)
        assert result == pytest.approx(20.0)

    def test_even_window_median(self) -> None:
        """window=4, feed [10, 20, 30, 40] → median is 25.0."""
        f = MedianFilter(window=4)
        for v in [10.0, 20.0, 30.0]:
            f.update(v)
        result = f.update(40.0)
        assert result == pytest.approx(25.0)

    def test_spike_rejection(self) -> None:
        """window=5, feed [10, 10, 100, 10, 10] → median is 10."""
        f = MedianFilter(window=5)
        for v in [10.0, 10.0, 100.0, 10.0]:
            f.update(v)
        result = f.update(10.0)
        assert result == pytest.approx(10.0)

    def test_warmup_partial_window(self) -> None:
        """window=5, feed 2 values → median of those 2."""
        f = MedianFilter(window=5)
        f.update(10.0)
        result = f.update(20.0)
        # median of [10, 20] = 15.0
        assert result == pytest.approx(15.0)

    def test_sliding_window_drops_oldest(self) -> None:
        """window=3, feed 4 values — median uses last 3."""
        f = MedianFilter(window=3)
        f.update(1.0)
        f.update(2.0)
        f.update(3.0)
        # Window: [1, 2, 3], median = 2.0
        result = f.update(100.0)
        # Window: [2, 3, 100], median = 3.0
        assert result == pytest.approx(3.0)

    def test_reset_clears_state(self) -> None:
        """After updates, reset → value is None, next update re-seeds."""
        f = MedianFilter(window=3)
        f.update(10.0)
        f.update(20.0)

        f.reset()

        assert f.value is None
        result = f.update(99.0)
        assert result == 99.0
        assert f.value == 99.0

    def test_window_property(self) -> None:
        """window property returns the configured value."""
        f = MedianFilter(window=7)
        assert f.window == 7

    def test_satisfies_filter_protocol(self) -> None:
        """MedianFilter satisfies the Filter protocol via structural subtyping."""
        f = MedianFilter(window=3)
        assert isinstance(f, Filter)


# =============================================================================
# OneEuroFilter tests
# =============================================================================


class TestOneEuroFilterValidation:
    """Constructor validation for OneEuroFilter.

    Technique: Boundary Value Analysis + Error Guessing.
    """

    def test_min_cutoff_must_be_positive(self) -> None:
        """min_cutoff=0 and min_cutoff=-1 raise ValueError."""
        with pytest.raises(ValueError, match="min_cutoff must be positive"):
            OneEuroFilter(min_cutoff=0)

        with pytest.raises(ValueError, match="min_cutoff must be positive"):
            OneEuroFilter(min_cutoff=-1)

    def test_beta_must_be_non_negative(self) -> None:
        """beta=-0.1 raises ValueError; beta=0 is valid."""
        with pytest.raises(ValueError, match="beta must be non-negative"):
            OneEuroFilter(beta=-0.1)

        # beta=0 should NOT raise
        OneEuroFilter(beta=0.0)

    def test_d_cutoff_must_be_positive(self) -> None:
        """d_cutoff=0 and d_cutoff=-1 raise ValueError."""
        with pytest.raises(ValueError, match="d_cutoff must be positive"):
            OneEuroFilter(d_cutoff=0)

        with pytest.raises(ValueError, match="d_cutoff must be positive"):
            OneEuroFilter(d_cutoff=-1)

    def test_dt_must_be_positive(self) -> None:
        """dt=0 and dt=-1 raise ValueError."""
        with pytest.raises(ValueError, match="dt must be positive"):
            OneEuroFilter(dt=0)

        with pytest.raises(ValueError, match="dt must be positive"):
            OneEuroFilter(dt=-1)

    def test_bool_params_raise_type_error(self) -> None:
        """bool passed for any numeric parameter raises TypeError."""
        with pytest.raises(TypeError, match="min_cutoff must be a number, got bool"):
            OneEuroFilter(min_cutoff=True)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="beta must be a number, got bool"):
            OneEuroFilter(beta=True)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="d_cutoff must be a number, got bool"):
            OneEuroFilter(d_cutoff=True)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="dt must be a number, got bool"):
            OneEuroFilter(dt=True)  # type: ignore[arg-type]


class TestOneEuroFilter:
    """Functional tests for the 1€ adaptive low-pass filter.

    Technique: State Transition Testing + Adaptive Behaviour Verification.
    """

    def test_initial_value_is_none(self) -> None:
        """Before any update, value is None."""
        f = OneEuroFilter()
        assert f.value is None

    def test_first_update_seeds_filter(self) -> None:
        """First update returns the raw value unchanged."""
        f = OneEuroFilter()
        result = f.update(42.0)
        assert result == 42.0
        assert f.value == 42.0

    def test_beta_zero_is_pure_lowpass(self) -> None:
        """With beta=0, the filter behaves as a fixed-cutoff PT1.

        Feed a constant then a step — verify smoothing occurs
        (the output does not immediately jump to the new value).
        """
        f = OneEuroFilter(min_cutoff=0.5, beta=0.0, dt=1.0)
        f.update(0.0)  # seed

        # Step to 100
        result = f.update(100.0)

        # Should NOT equal 100 — smoothing must occur
        assert result < 100.0
        assert result > 0.0

    def test_adaptive_tracking(self) -> None:
        """With beta > 0, filter tracks faster during rapid changes.

        Feed a step input; compare settled values after the same number
        of updates with beta=0 vs beta=1.0.  The beta=1.0 version should
        be closer to the step target.
        """
        f_slow = OneEuroFilter(min_cutoff=0.5, beta=0.0, dt=1.0)
        f_fast = OneEuroFilter(min_cutoff=0.5, beta=1.0, dt=1.0)

        f_slow.update(0.0)
        f_fast.update(0.0)

        for _ in range(5):
            f_slow.update(100.0)
            f_fast.update(100.0)

        # f_fast should be closer to 100 than f_slow
        assert f_fast.value is not None
        assert f_slow.value is not None
        assert abs(100.0 - f_fast.value) < abs(100.0 - f_slow.value)

    def test_stable_signal_heavily_smoothed(self) -> None:
        """Feed a constant value, then a small perturbation — heavy smoothing.

        With low min_cutoff, a tiny bump should barely affect the output.
        """
        f = OneEuroFilter(min_cutoff=0.1, beta=0.0, dt=1.0)
        # Establish a stable baseline
        for _ in range(50):
            f.update(50.0)

        # Small perturbation
        result = f.update(51.0)

        # Output should still be very close to 50 due to heavy smoothing
        assert result is not None
        assert result == pytest.approx(50.0, abs=0.5)

    def test_defaults(self) -> None:
        """OneEuroFilter() uses min_cutoff=1.0, beta=0.0, d_cutoff=1.0, dt=1.0."""
        f = OneEuroFilter()
        assert f.min_cutoff == 1.0
        assert f.beta == 0.0
        assert f.d_cutoff == 1.0
        assert f.dt == 1.0

    def test_properties_expose_parameters(self) -> None:
        """All 4 read-only properties return correct values."""
        f = OneEuroFilter(min_cutoff=2.0, beta=0.5, d_cutoff=3.0, dt=0.1)
        assert f.min_cutoff == 2.0
        assert f.beta == 0.5
        assert f.d_cutoff == 3.0
        assert f.dt == 0.1

    def test_reset_clears_state(self) -> None:
        """After updates, reset → value is None, next update re-seeds."""
        f = OneEuroFilter()
        f.update(10.0)
        f.update(20.0)

        f.reset()

        assert f.value is None
        result = f.update(99.0)
        assert result == 99.0
        assert f.value == 99.0

    def test_satisfies_filter_protocol(self) -> None:
        """OneEuroFilter satisfies the Filter protocol via structural subtyping."""
        f = OneEuroFilter()
        assert isinstance(f, Filter)

    def test_convergence(self) -> None:
        """Feed constant value for many iterations — converges to that value."""
        f = OneEuroFilter(min_cutoff=1.0, beta=0.0, dt=1.0)
        f.update(0.0)  # seed

        for _ in range(200):
            f.update(50.0)

        assert f.value == pytest.approx(50.0, abs=0.01)


# =============================================================================
# _alpha_from_cutoff helper tests
# =============================================================================


class TestAlphaFromCutoff:
    """Direct tests for the _alpha_from_cutoff helper function.

    Technique: Specification-based Testing — verify the mathematical formula
    alpha = dt / (tau + dt) where tau = 1 / (2 * pi * cutoff).
    """

    def test_known_value(self) -> None:
        """cutoff=1 Hz, dt=1 s → tau ≈ 0.15915, alpha ≈ 0.8626."""
        import math

        alpha = _alpha_from_cutoff(cutoff=1.0, dt=1.0)
        tau = 1.0 / (2.0 * math.pi * 1.0)
        expected = 1.0 / (tau + 1.0)
        assert alpha == pytest.approx(expected)

    def test_high_cutoff_approaches_one(self) -> None:
        """Very high cutoff → tau ≈ 0 → alpha ≈ 1 (no smoothing)."""
        alpha = _alpha_from_cutoff(cutoff=1e6, dt=1.0)
        assert alpha == pytest.approx(1.0, abs=1e-5)

    def test_low_cutoff_approaches_zero(self) -> None:
        """Very low cutoff → tau very large → alpha ≈ 0 (heavy smoothing)."""
        alpha = _alpha_from_cutoff(cutoff=1e-6, dt=1.0)
        assert alpha < 0.001

    def test_dt_scales_alpha(self) -> None:
        """Doubling dt increases alpha (faster tracking at lower sample rate)."""
        a1 = _alpha_from_cutoff(cutoff=1.0, dt=0.5)
        a2 = _alpha_from_cutoff(cutoff=1.0, dt=1.0)
        assert a2 > a1


# =============================================================================
# __repr__ tests
# =============================================================================


class TestFilterRepr:
    """Verify __repr__ output for debugging ergonomics.

    Technique: Specification-based Testing — repr contract.
    """

    def test_pt1_repr_before_update(self) -> None:
        """Pt1Filter repr shows tau, dt, and value=None before any update."""
        f = Pt1Filter(tau=5.0, dt=1.0)
        assert repr(f) == "Pt1Filter(tau=5.0, dt=1.0, value=None)"

    def test_pt1_repr_after_update(self) -> None:
        """Pt1Filter repr shows current filtered value."""
        f = Pt1Filter(tau=4.0, dt=1.0)
        f.update(10.0)
        assert repr(f) == "Pt1Filter(tau=4.0, dt=1.0, value=10.0)"

    def test_median_repr_before_update(self) -> None:
        """MedianFilter repr shows window and value=None before any update."""
        f = MedianFilter(window=5)
        assert repr(f) == "MedianFilter(window=5, value=None)"

    def test_median_repr_after_update(self) -> None:
        """MedianFilter repr shows current median value."""
        f = MedianFilter(window=3)
        f.update(10.0)
        assert repr(f) == "MedianFilter(window=3, value=10.0)"

    def test_one_euro_repr_before_update(self) -> None:
        """OneEuroFilter repr shows all params and value=None."""
        f = OneEuroFilter(min_cutoff=0.5, beta=0.1, d_cutoff=2.0, dt=0.5)
        expected = (
            "OneEuroFilter(min_cutoff=0.5, beta=0.1, d_cutoff=2.0, dt=0.5, value=None)"
        )
        assert repr(f) == expected

    def test_one_euro_repr_after_update(self) -> None:
        """OneEuroFilter repr shows current filtered value."""
        f = OneEuroFilter()
        f.update(42.0)
        expected = (
            "OneEuroFilter(min_cutoff=1.0, beta=0.0, d_cutoff=1.0, dt=1.0, value=42.0)"
        )
        assert repr(f) == expected
