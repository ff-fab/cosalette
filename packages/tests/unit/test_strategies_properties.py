"""Property-based tests for publish strategies (_strategies.py).

Complements the example-based tests in ``test_strategies.py`` by verifying
behavioural **invariants** over randomly generated payloads and parameters.

Test Techniques Used:
- Property-based Testing: Verifying invariants over random inputs
- State Transition Testing: Counter reset / timer reset cycles
- Specification-based Testing: Strategy contracts from ADR-013
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from cosalette._strategies import Every, OnChange
from cosalette.testing import FakeClock

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Simple string keys for dict generation — avoids pathological keys.
_keys = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
    min_size=1,
    max_size=10,
)

# Leaf values: numeric or string (the two main categories for OnChange).
_leaf_values = st.one_of(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.text(min_size=0, max_size=20),
    st.booleans(),
)

# Flat telemetry dicts — representative of real payloads.
_payload = st.dictionaries(keys=_keys, values=_leaf_values, min_size=1, max_size=10)

# Positive floats for thresholds and time intervals.
_positive_floats = st.floats(
    min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False
)


# =============================================================================
# OnChange properties
# =============================================================================


class TestOnChangeProperties:
    """Property-based tests for the OnChange publish strategy.

    OnChange is the most complex strategy: it supports exact equality,
    global thresholds, and per-field thresholds with nested dict traversal.
    """

    @given(current=_payload)
    @settings(max_examples=200)
    def test_first_publish_always(self, current: dict[str, object]) -> None:
        """With previous=None, should_publish always returns True.

        The first reading must always be published — there's nothing
        to compare against.
        """
        strategy = OnChange()
        assert strategy.should_publish(current, None) is True

    @given(current=_payload)
    @settings(max_examples=200)
    def test_identical_dicts_never_publish(self, current: dict[str, object]) -> None:
        """Exact-equality mode: identical dicts suppress publishing.

        OnChange(threshold=None) uses ``current != previous``. When
        both dicts are structurally equal, should_publish returns False.
        """
        strategy = OnChange()
        assert strategy.should_publish(current, current) is False

    @given(current=_payload)
    @settings(max_examples=200)
    def test_identical_dicts_with_threshold_never_publish(
        self, current: dict[str, object]
    ) -> None:
        """Threshold mode: identical dicts still suppress publishing.

        Even with a numeric threshold, if all values are exactly the
        same, no field exceeds the dead-band.
        """
        strategy = OnChange(threshold=0.0)
        assert strategy.should_publish(current, current) is False

    @given(
        value=st.floats(
            min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
        threshold=_positive_floats,
    )
    @settings(max_examples=200)
    def test_below_threshold_suppressed(self, value: float, threshold: float) -> None:
        """When |current - previous| <= threshold, publishing is suppressed.

        The dead-band uses strict inequality: ``abs(cur - prev) > threshold``.
        So exactly at the threshold boundary, it does NOT publish.
        """
        # Construct a delta that's within the threshold
        delta = threshold * 0.5  # Guaranteed <= threshold
        current = {"temp": value + delta}
        previous = {"temp": value}
        strategy = OnChange(threshold=threshold)
        assert strategy.should_publish(current, previous) is False

    @given(
        value=st.floats(
            min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False
        ),
        threshold=st.floats(
            min_value=1e-6, max_value=1e3, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=200)
    def test_above_threshold_publishes(self, value: float, threshold: float) -> None:
        """When |current - previous| > threshold, publishing triggers.

        We add 2*threshold to guarantee we exceed the dead-band.
        """
        current = {"temp": value + 2 * threshold}
        previous = {"temp": value}
        strategy = OnChange(threshold=threshold)
        assert strategy.should_publish(current, previous) is True

    @given(
        payload=_payload,
        extra_key=_keys,
        extra_value=_leaf_values,
    )
    @settings(max_examples=200)
    def test_structural_change_always_publishes(
        self, payload: dict[str, object], extra_key: str, extra_value: object
    ) -> None:
        """Adding a key always triggers a publish, regardless of threshold.

        Structural changes (added/removed keys) bypass numeric comparison
        entirely — the key sets differ, so the dicts are considered changed.
        """
        assume(extra_key not in payload)
        previous = dict(payload)
        current = {**payload, extra_key: extra_value}
        strategy = OnChange(threshold=1e9)  # Absurdly high threshold
        assert strategy.should_publish(current, previous) is True


# =============================================================================
# Every (count mode) properties
# =============================================================================


class TestEveryCountProperties:
    """Property-based tests for the count-based Every strategy."""

    @given(n=st.integers(min_value=1, max_value=100))
    @settings(max_examples=200)
    def test_publishes_on_nth_call(self, n: int) -> None:
        """should_publish returns True on exactly the Nth call.

        The internal counter increments on each call. The Nth call
        makes counter == n, triggering a publish.
        """
        strategy = Every(n=n)
        dummy = {"x": 1}

        for i in range(1, n):
            assert strategy.should_publish(dummy, dummy) is False, (
                f"Premature True at call {i}"
            )

        assert strategy.should_publish(dummy, dummy) is True

    @given(
        n=st.integers(min_value=1, max_value=50),
        cycles=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=200)
    def test_counter_resets_after_on_published(self, n: int, cycles: int) -> None:
        """After on_published(), the counter resets and needs N more calls.

        This verifies the reset contract over multiple cycles —
        the strategy is reusable across publish events.
        """
        strategy = Every(n=n)
        dummy = {"x": 1}

        for _cycle in range(cycles):
            for i in range(1, n):
                assert strategy.should_publish(dummy, dummy) is False, (
                    f"Premature True at call {i} in cycle {_cycle}"
                )
            assert strategy.should_publish(dummy, dummy) is True
            strategy.on_published()


# =============================================================================
# Every (time mode) properties
# =============================================================================


class TestEveryTimeProperties:
    """Property-based tests for the time-based Every strategy."""

    @given(
        seconds=_positive_floats,
        elapsed=_positive_floats,
    )
    @settings(max_examples=200)
    def test_publishes_after_elapsed(self, seconds: float, elapsed: float) -> None:
        """When elapsed >= seconds, should_publish returns True.

        The strategy compares ``clock.now() - last_publish_time``
        against the configured interval.
        """
        assume(elapsed >= seconds)
        clock = FakeClock(0.0)
        strategy = Every(seconds=seconds)
        strategy._bind(clock)
        strategy.on_published()  # Reset last_publish_time to 0.0

        clock._time = elapsed
        dummy = {"x": 1}
        assert strategy.should_publish(dummy, dummy) is True

    @given(
        seconds=st.floats(
            min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(max_examples=200)
    def test_suppressed_before_elapsed(self, seconds: float) -> None:
        """When elapsed < seconds, should_publish returns False.

        We set elapsed to half the interval — guaranteed to be below.
        """
        clock = FakeClock(0.0)
        strategy = Every(seconds=seconds)
        strategy._bind(clock)
        strategy.on_published()

        clock._time = seconds * 0.5
        dummy = {"x": 1}
        assert strategy.should_publish(dummy, dummy) is False

    @given(
        seconds=_positive_floats,
        cycles=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=200)
    def test_timer_resets_after_on_published(self, seconds: float, cycles: int) -> None:
        """After on_published(), the timer resets to current clock time.

        Each cycle: advance clock past the interval → publish → reset.
        Immediately after reset, should_publish returns False.
        """
        clock = FakeClock(0.0)
        strategy = Every(seconds=seconds)
        strategy._bind(clock)
        dummy = {"x": 1}

        for cycle in range(cycles):
            # Advance past interval
            clock._time = (cycle + 1) * seconds * 2
            assert strategy.should_publish(dummy, dummy) is True
            strategy.on_published()

            # Immediately after reset — not enough time has passed
            assert strategy.should_publish(dummy, dummy) is False
