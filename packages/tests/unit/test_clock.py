"""Unit tests for cosalette._clock — clock port and system adapter.

Test Techniques Used:
    - Specification-based Testing: Verifying ClockPort protocol
      contract
    - Protocol Conformance: isinstance checks for structural
      subtyping
    - Boundary Value Analysis: Monotonic ordering guarantees
"""

from __future__ import annotations

from cosalette._clock import ClockPort, SystemClock


class TestSystemClock:
    """Tests for SystemClock production implementation.

    Technique: Specification-based Testing — verifying public
    contract.
    """

    def test_satisfies_clock_port_protocol(self) -> None:
        """SystemClock is recognized as ClockPort."""
        clock = SystemClock()
        assert isinstance(clock, ClockPort)

    def test_now_returns_float(self) -> None:
        """now() returns a float value."""
        clock = SystemClock()
        result = clock.now()
        assert isinstance(result, float)

    def test_now_is_monotonically_non_decreasing(self) -> None:
        """Successive calls return non-decreasing values."""
        clock = SystemClock()
        t1 = clock.now()
        t2 = clock.now()
        assert t2 >= t1


class TestClockPortProtocol:
    """Tests for ClockPort protocol definition.

    Technique: Protocol Conformance — structural subtyping checks.
    """

    def test_custom_class_satisfies_protocol(self) -> None:
        """A class with now() -> float satisfies ClockPort."""

        class FakeClock:
            def now(self) -> float:
                return 42.0

        assert isinstance(FakeClock(), ClockPort)

    def test_class_without_now_does_not_satisfy(self) -> None:
        """A class without now() does not satisfy ClockPort."""

        class NotAClock:
            pass

        assert not isinstance(NotAClock(), ClockPort)
