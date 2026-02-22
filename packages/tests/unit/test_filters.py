"""Unit tests for cosalette._filters — signal filter module.

Test Techniques Used:
    - Specification-based Testing: Protocol conformance, constructor contracts
    - Boundary Value Analysis: tau/dt at zero, negative values
    - Error Guessing: bool-is-int gotcha (Python subclass trap)
    - State Transition Testing: seed → update → reset → re-seed cycle
    - Property-based Reasoning: convergence, sample-rate independence
"""

from __future__ import annotations

import pytest

from cosalette._filters import Filter, Pt1Filter

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
