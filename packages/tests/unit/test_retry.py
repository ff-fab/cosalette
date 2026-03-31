"""Unit tests for cosalette._retry — backoff strategies and circuit breaker.

Test Techniques Used:
- Boundary Value Analysis: Backoff delay at attempt 1, at max_delay cap
- State Transition Testing: CircuitBreaker closed → open → half-open → closed
- Specification-based: Protocol compliance, jitter bounds
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cosalette._retry import (
    CircuitBreaker,
    ExponentialBackoff,
    FixedBackoff,
    LinearBackoff,
)

pytestmark = pytest.mark.unit


class TestExponentialBackoff:
    """ExponentialBackoff delay calculations.

    Technique: Boundary Value Analysis + Specification-based.
    """

    def test_delay_attempt_1_returns_base(self) -> None:
        """First attempt delay equals base (before jitter)."""
        strategy = ExponentialBackoff(base=2.0, max_delay=60.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0  # no jitter
            assert strategy.delay(1) == 2.0

    def test_delay_attempt_2_doubles(self) -> None:
        """Second attempt doubles the base."""
        strategy = ExponentialBackoff(base=2.0, max_delay=60.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            assert strategy.delay(2) == 4.0

    def test_delay_attempt_3_quadruples(self) -> None:
        """Third attempt is base * 4."""
        strategy = ExponentialBackoff(base=2.0, max_delay=60.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            assert strategy.delay(3) == 8.0

    def test_delay_capped_at_max(self) -> None:
        """Delay never exceeds max_delay."""
        strategy = ExponentialBackoff(base=2.0, max_delay=10.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            # 2^9 * 2 = 1024, but capped at 10
            assert strategy.delay(10) == 10.0

    def test_jitter_applied_within_bounds(self) -> None:
        """Delay includes ±20% jitter."""
        strategy = ExponentialBackoff(base=10.0, max_delay=100.0)
        # Test lower bound
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 0.8
            assert strategy.delay(1) == 8.0
        # Test upper bound
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.2
            assert strategy.delay(1) == 12.0

    def test_repr(self) -> None:
        strategy = ExponentialBackoff(base=3.0, max_delay=30.0)
        assert repr(strategy) == "ExponentialBackoff(base=3.0, max_delay=30.0)"


class TestLinearBackoff:
    """LinearBackoff delay calculations.

    Technique: Boundary Value Analysis.
    """

    def test_delay_grows_linearly(self) -> None:
        strategy = LinearBackoff(step=3.0, max_delay=60.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            assert strategy.delay(1) == 3.0
            assert strategy.delay(2) == 6.0
            assert strategy.delay(3) == 9.0

    def test_delay_capped_at_max(self) -> None:
        strategy = LinearBackoff(step=5.0, max_delay=10.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            assert strategy.delay(3) == 10.0

    def test_repr(self) -> None:
        strategy = LinearBackoff(step=2.0, max_delay=30.0)
        assert repr(strategy) == "LinearBackoff(step=2.0, max_delay=30.0)"


class TestFixedBackoff:
    """FixedBackoff always returns constant delay.

    Technique: Specification-based.
    """

    def test_delay_constant_regardless_of_attempt(self) -> None:
        strategy = FixedBackoff(delay=5.0)
        with patch("cosalette._retry.random") as mock_random:
            mock_random.uniform.return_value = 1.0
            assert strategy.delay(1) == 5.0
            assert strategy.delay(10) == 5.0
            assert strategy.delay(100) == 5.0

    def test_repr(self) -> None:
        strategy = FixedBackoff(delay=7.5)
        assert repr(strategy) == "FixedBackoff(delay=7.5)"


class TestCircuitBreaker:
    """CircuitBreaker state machine transitions.

    Technique: State Transition Testing — closed → open → half-open → closed.
    """

    def test_initial_state_closed(self) -> None:
        cb = CircuitBreaker(threshold=3)
        assert cb.state == "closed"
        assert cb.consecutive_failures == 0

    def test_should_attempt_when_closed(self) -> None:
        cb = CircuitBreaker(threshold=3)
        assert cb.should_attempt() is True

    def test_failures_below_threshold_stay_closed(self) -> None:
        """Failures below threshold don't open the circuit."""
        cb = CircuitBreaker(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        assert cb.consecutive_failures == 2
        assert cb.should_attempt() is True

    def test_failures_at_threshold_open_circuit(self) -> None:
        """Reaching threshold opens the circuit."""
        cb = CircuitBreaker(threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert cb.consecutive_failures == 3

    def test_open_skips_and_transitions_to_half_open(self) -> None:
        """Open state: should_attempt returns False, transitions to half-open."""
        cb = CircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        assert cb.should_attempt() is False
        assert cb.state == "half-open"

    def test_half_open_allows_probe(self) -> None:
        """Half-open state: should_attempt returns True (probe)."""
        cb = CircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        cb.should_attempt()  # open → half-open
        assert cb.should_attempt() is True  # probe allowed

    def test_probe_success_closes_circuit(self) -> None:
        """Successful probe in half-open returns to closed."""
        cb = CircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        cb.should_attempt()  # open → half-open
        cb.record_success()
        assert cb.state == "closed"
        assert cb.consecutive_failures == 0

    def test_probe_failure_reopens_circuit(self) -> None:
        """Failed probe in half-open reopens the circuit."""
        cb = CircuitBreaker(threshold=2)
        cb.record_failure()
        cb.record_failure()
        # Now open, consecutive_failures=2
        cb.should_attempt()  # open → half-open
        cb.record_failure()
        # consecutive_failures=3, >= threshold=2 → re-opens
        assert cb.state == "open"

    def test_success_resets_all_state(self) -> None:
        """record_success always resets to clean closed state."""
        cb = CircuitBreaker(threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.state == "closed"
        assert cb.consecutive_failures == 0

    def test_repr(self) -> None:
        cb = CircuitBreaker(threshold=7)
        assert repr(cb) == "CircuitBreaker(threshold=7)"

    def test_threshold_property(self) -> None:
        cb = CircuitBreaker(threshold=10)
        assert cb.threshold == 10

    def test_threshold_zero_raises(self) -> None:
        """Threshold must be >= 1."""
        with pytest.raises(ValueError, match="threshold must be a positive integer"):
            CircuitBreaker(threshold=0)

    def test_threshold_negative_raises(self) -> None:
        """Negative threshold raises."""
        with pytest.raises(ValueError, match="threshold must be a positive integer"):
            CircuitBreaker(threshold=-1)
