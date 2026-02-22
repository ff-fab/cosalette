"""Unit tests for cosalette._strategies — publish strategy module.

Test Techniques Used:
- Specification-based Testing: Protocol conformance, constructor contracts
- Equivalence Partitioning: Time-mode vs count-mode via @parametrize
- Boundary Value Analysis: Counter at N-1 / N, elapsed time at boundary
- Decision Table: Mutual-exclusivity validation (seconds × n)
- Error Guessing: Unbound clock fallback, threshold NotImplementedError
- State Transition Testing: Counter resets via on_published
"""

from __future__ import annotations

import pytest

from cosalette._strategies import (
    AllStrategy,
    AnyStrategy,
    Every,
    OnChange,
    PublishStrategy,
)
from cosalette.testing._clock import FakeClock

# =============================================================================
# Fixtures
# =============================================================================

CURRENT: dict[str, object] = {"temperature": 21.5}
PREVIOUS: dict[str, object] = {"temperature": 20.0}


# =============================================================================
# Tests
# =============================================================================


class TestPublishStrategyProtocol:
    """Verify PublishStrategy is a runtime-checkable protocol.

    Technique: Specification-based Testing — structural subtyping checks.
    """

    def test_protocol_is_runtime_checkable(self) -> None:
        """PublishStrategy can be used with isinstance."""

        class Dummy:
            def should_publish(
                self,
                current: dict[str, object],
                previous: dict[str, object] | None,
            ) -> bool:
                return True

            def on_published(self) -> None:
                pass

            def _bind(self, clock: object) -> None:
                pass

        assert isinstance(Dummy(), PublishStrategy)

    def test_class_without_methods_does_not_satisfy(self) -> None:
        """A class missing required methods fails isinstance."""

        class NotAStrategy:
            pass

        assert not isinstance(NotAStrategy(), PublishStrategy)

    def test_every_seconds_satisfies_protocol(self) -> None:
        """Every(seconds=...) satisfies PublishStrategy."""
        assert isinstance(Every(seconds=1.0), PublishStrategy)

    def test_every_n_satisfies_protocol(self) -> None:
        """Every(n=...) satisfies PublishStrategy."""
        assert isinstance(Every(n=1), PublishStrategy)

    def test_on_change_satisfies_protocol(self) -> None:
        """OnChange() satisfies PublishStrategy."""
        assert isinstance(OnChange(), PublishStrategy)

    def test_any_strategy_satisfies_protocol(self) -> None:
        """AnyStrategy satisfies PublishStrategy."""
        assert isinstance(AnyStrategy(OnChange()), PublishStrategy)

    def test_all_strategy_satisfies_protocol(self) -> None:
        """AllStrategy satisfies PublishStrategy."""
        assert isinstance(AllStrategy(OnChange()), PublishStrategy)


class TestEveryValidation:
    """Constructor validation for mutually-exclusive parameters.

    Technique: Decision Table — the 2×2 matrix of seconds/n presence.
    """

    def test_raises_when_both_seconds_and_n_provided(self) -> None:
        """Both 'seconds' and 'n' → ValueError."""
        with pytest.raises(ValueError, match="not both"):
            Every(seconds=10.0, n=5)

    def test_raises_when_neither_seconds_nor_n_provided(self) -> None:
        """Neither 'seconds' nor 'n' → ValueError."""
        with pytest.raises(ValueError, match="exactly one"):
            Every()

    def test_raises_for_non_positive_seconds(self) -> None:
        """seconds <= 0 → ValueError."""
        with pytest.raises(ValueError, match="positive"):
            Every(seconds=0)

    def test_raises_for_negative_seconds(self) -> None:
        """Negative seconds → ValueError."""
        with pytest.raises(ValueError, match="positive"):
            Every(seconds=-5.0)

    def test_raises_for_non_positive_n(self) -> None:
        """n <= 0 → ValueError."""
        with pytest.raises(ValueError, match="positive"):
            Every(n=0)

    def test_raises_for_negative_n(self) -> None:
        """Negative n → ValueError."""
        with pytest.raises(ValueError, match="positive"):
            Every(n=-1)


class TestEverySeconds:
    """Time-based throttle: Every(seconds=N).

    Technique: Boundary Value Analysis — testing at and below the
    elapsed-time threshold.
    """

    def test_returns_false_before_seconds_elapsed(self) -> None:
        """Before the interval, should_publish returns False."""
        clock = FakeClock(0.0)
        strategy = Every(seconds=10.0)
        strategy._bind(clock)

        clock._time = 5.0  # only half elapsed
        assert strategy.should_publish(CURRENT, PREVIOUS) is False

    def test_returns_true_when_seconds_elapsed(self) -> None:
        """At or beyond the interval, should_publish returns True."""
        clock = FakeClock(0.0)
        strategy = Every(seconds=10.0)
        strategy._bind(clock)

        clock._time = 10.0
        assert strategy.should_publish(CURRENT, PREVIOUS) is True

    def test_returns_true_when_seconds_exceeded(self) -> None:
        """Well past the interval, still returns True."""
        clock = FakeClock(0.0)
        strategy = Every(seconds=10.0)
        strategy._bind(clock)

        clock._time = 99.0
        assert strategy.should_publish(CURRENT, PREVIOUS) is True

    def test_resets_on_published(self) -> None:
        """After on_published, the timer restarts from current time."""
        clock = FakeClock(0.0)
        strategy = Every(seconds=10.0)
        strategy._bind(clock)

        clock._time = 10.0
        assert strategy.should_publish(CURRENT, PREVIOUS) is True
        strategy.on_published()

        # Immediately after publish — not enough time elapsed
        clock._time = 15.0
        assert strategy.should_publish(CURRENT, PREVIOUS) is False

        # Enough time since last publish
        clock._time = 20.0
        assert strategy.should_publish(CURRENT, PREVIOUS) is True

    def test_returns_true_without_bind(self) -> None:
        """Before _bind is called, always returns True (safe fallback).

        Technique: Error Guessing — unbound clock edge case.
        """
        strategy = Every(seconds=10.0)
        # No _bind call
        assert strategy.should_publish(CURRENT, PREVIOUS) is True


class TestEveryN:
    """Count-based throttle: Every(n=N).

    Technique: State Transition Testing — counter increments and resets.
    """

    def test_returns_false_before_n_calls(self) -> None:
        """Before N calls, should_publish returns False."""
        strategy = Every(n=3)
        assert strategy.should_publish(CURRENT, PREVIOUS) is False  # call 1
        assert strategy.should_publish(CURRENT, PREVIOUS) is False  # call 2

    def test_returns_true_on_nth_call(self) -> None:
        """On the N-th call, should_publish returns True."""
        strategy = Every(n=3)
        strategy.should_publish(CURRENT, PREVIOUS)  # 1
        strategy.should_publish(CURRENT, PREVIOUS)  # 2
        assert strategy.should_publish(CURRENT, PREVIOUS) is True  # 3

    def test_resets_on_published(self) -> None:
        """After on_published, the counter resets to 0."""
        strategy = Every(n=2)
        strategy.should_publish(CURRENT, PREVIOUS)  # 1
        assert strategy.should_publish(CURRENT, PREVIOUS) is True  # 2
        strategy.on_published()

        # Counter reset — need 2 more calls
        assert strategy.should_publish(CURRENT, PREVIOUS) is False  # 1
        assert strategy.should_publish(CURRENT, PREVIOUS) is True  # 2

    def test_every_n_1_always_publishes(self) -> None:
        """n=1 means every call triggers a publish.

        Technique: Boundary Value Analysis — minimum valid n.
        """
        strategy = Every(n=1)
        assert strategy.should_publish(CURRENT, PREVIOUS) is True
        strategy.on_published()
        assert strategy.should_publish(CURRENT, PREVIOUS) is True


class TestOnChange:
    """Exact-equality change detection: OnChange().

    Technique: Specification-based Testing — verifying the equality
    contract.
    """

    def test_returns_true_when_dict_differs(self) -> None:
        """Different payloads → should publish."""
        strategy = OnChange()
        assert strategy.should_publish(CURRENT, PREVIOUS) is True

    def test_returns_false_when_dict_same(self) -> None:
        """Identical payloads → should not publish."""
        strategy = OnChange()
        same = {"temperature": 21.5}
        assert strategy.should_publish(same, dict(same)) is False

    def test_returns_true_when_previous_is_none(self) -> None:
        """First publish (previous=None) → always publish."""
        strategy = OnChange()
        assert strategy.should_publish(CURRENT, None) is True

    def test_handles_nested_comparison(self) -> None:
        """Nested dicts are compared recursively by Python equality."""
        strategy = OnChange()
        a: dict[str, object] = {"sensor": {"temp": 21.5, "hum": 60}}
        b: dict[str, object] = {"sensor": {"temp": 21.5, "hum": 60}}
        c: dict[str, object] = {"sensor": {"temp": 22.0, "hum": 60}}

        assert strategy.should_publish(a, b) is False
        assert strategy.should_publish(a, c) is True

    def test_on_published_is_noop(self) -> None:
        """on_published does nothing — no state to reset."""
        strategy = OnChange()
        strategy.on_published()  # should not raise

    def test_threshold_raises_not_implemented(self) -> None:
        """Providing threshold raises NotImplementedError (Phase 2).

        Technique: Error Guessing — forward-compatible API surface.
        """
        strategy = OnChange(threshold=0.5)
        with pytest.raises(NotImplementedError, match="future release"):
            strategy.should_publish(CURRENT, PREVIOUS)

    def test_threshold_dict_raises_not_implemented(self) -> None:
        """Dict threshold also raises NotImplementedError."""
        strategy = OnChange(threshold={"temperature": 0.5})
        with pytest.raises(NotImplementedError, match="future release"):
            strategy.should_publish(CURRENT, PREVIOUS)


class TestAnyStrategy:
    """OR-composite via AnyStrategy / ``|`` operator.

    Technique: Branch Coverage — testing True/False combinations.
    """

    def test_publishes_if_any_child_says_yes(self) -> None:
        """At least one True → composite returns True."""
        yes = OnChange()  # current != previous → True
        no = Every(n=999)  # counter at 1 of 999 → False
        composite = AnyStrategy(yes, no)

        assert composite.should_publish(CURRENT, PREVIOUS) is True

    def test_does_not_publish_if_all_children_say_no(self) -> None:
        """All False → composite returns False."""
        no1 = Every(n=999)
        no2 = Every(n=999)
        composite = AnyStrategy(no1, no2)

        assert composite.should_publish(CURRENT, PREVIOUS) is False

    def test_on_published_calls_all_children(self) -> None:
        """on_published propagates to every child."""
        e1 = Every(n=2)
        e2 = Every(n=2)
        composite = AnyStrategy(e1, e2)

        # Drive both to threshold
        composite.should_publish(CURRENT, PREVIOUS)
        composite.should_publish(CURRENT, PREVIOUS)
        composite.on_published()

        # Both counters should be reset → next call is 1 of 2 → False
        assert e1.should_publish(CURRENT, PREVIOUS) is False
        assert e2.should_publish(CURRENT, PREVIOUS) is False

    def test_flattens_nested_any_strategy(self) -> None:
        """AnyStrategy(AnyStrategy(a, b), c) → AnyStrategy(a, b, c)."""
        a, b, c = OnChange(), OnChange(), OnChange()
        nested = AnyStrategy(AnyStrategy(a, b), c)

        assert len(nested._children) == 3
        assert nested._children == [a, b, c]

    def test_raises_on_empty_children(self) -> None:
        """Zero children → ValueError.

        Technique: Error Guessing — ``any([])`` returns False, which
        would silently suppress publishing.
        """
        with pytest.raises(ValueError, match="at least one child"):
            AnyStrategy()

    def test_all_children_evaluated_no_short_circuit(self) -> None:
        """Stateful children always advance even when another already decided.

        Technique: State Transition Testing — ``Every(n=2)`` must have
        its counter incremented on every ``should_publish`` call,
        even when a sibling ``OnChange`` already returned True.
        """
        yes = OnChange()  # always True when current != previous
        counter = Every(n=3)  # needs 3 calls to return True
        composite = AnyStrategy(yes, counter)

        # Call 1: OnChange→True, Every→False (counter=1) → True
        composite.should_publish(CURRENT, PREVIOUS)
        # Call 2: OnChange→True, Every→False (counter=2) → True
        composite.should_publish(CURRENT, PREVIOUS)
        # Call 3: OnChange→True, Every→True (counter=3) → True
        composite.should_publish(CURRENT, PREVIOUS)

        # If short-circuit occurred, counter would NOT have advanced.
        # Verify counter reached 3 by checking internal state.
        assert counter._counter == 3


class TestAllStrategy:
    """AND-composite via AllStrategy / ``&`` operator.

    Technique: Branch Coverage — testing True/False combinations.
    """

    def test_publishes_only_if_all_children_say_yes(self) -> None:
        """All True → composite returns True."""
        yes1 = OnChange()
        yes2 = OnChange()
        composite = AllStrategy(yes1, yes2)

        assert composite.should_publish(CURRENT, PREVIOUS) is True

    def test_does_not_publish_if_any_child_says_no(self) -> None:
        """One False → composite returns False."""
        yes = OnChange()
        no = Every(n=999)
        composite = AllStrategy(yes, no)

        assert composite.should_publish(CURRENT, PREVIOUS) is False

    def test_on_published_calls_all_children(self) -> None:
        """on_published propagates to every child."""
        e1 = Every(n=1)
        e2 = Every(n=1)
        composite = AllStrategy(e1, e2)

        assert composite.should_publish(CURRENT, PREVIOUS) is True
        composite.on_published()

        # Both counters should be reset → next call is 1 of 1 → True
        assert e1.should_publish(CURRENT, PREVIOUS) is True
        assert e2.should_publish(CURRENT, PREVIOUS) is True

    def test_flattens_nested_all_strategy(self) -> None:
        """AllStrategy(AllStrategy(a, b), c) → AllStrategy(a, b, c)."""
        a, b, c = OnChange(), OnChange(), OnChange()
        nested = AllStrategy(AllStrategy(a, b), c)

        assert len(nested._children) == 3
        assert nested._children == [a, b, c]

    def test_raises_on_empty_children(self) -> None:
        """Zero children → ValueError.

        Technique: Error Guessing — ``all([])`` returns True, which
        would cause unconditional publishing.
        """
        with pytest.raises(ValueError, match="at least one child"):
            AllStrategy()

    def test_all_children_evaluated_no_short_circuit(self) -> None:
        """Stateful children always advance even when another already decided.

        Technique: State Transition Testing — ``Every(n=3)`` must have
        its counter incremented even when a sibling ``Every(n=999)``
        already returned False.
        """
        no = Every(n=999)  # always False (counter never reaches 999)
        counter = Every(n=3)  # needs 3 calls to return True
        composite = AllStrategy(no, counter)

        # Call 1: no→False, counter→False (counter=1) → False
        composite.should_publish(CURRENT, PREVIOUS)
        # Call 2: no→False, counter→False (counter=2) → False
        composite.should_publish(CURRENT, PREVIOUS)
        # Call 3: no→False, counter→True (counter=3) → False
        composite.should_publish(CURRENT, PREVIOUS)

        # If short-circuit occurred, counter would NOT have advanced.
        assert counter._counter == 3


class TestComposition:
    """Operator-based composition and clock propagation.

    Technique: Specification-based Testing — verifying ``|`` / ``&``
    semantics and _bind propagation.
    """

    def test_pipe_returns_any_strategy(self) -> None:
        """``|`` operator produces AnyStrategy."""
        result = OnChange() | Every(n=1)
        assert isinstance(result, AnyStrategy)

    def test_ampersand_returns_all_strategy(self) -> None:
        """``&`` operator produces AllStrategy."""
        result = OnChange() & Every(n=1)
        assert isinstance(result, AllStrategy)

    def test_triple_pipe_composition(self) -> None:
        """Three strategies composed with ``|``, left-associative."""
        a, b, c = OnChange(), Every(n=1), Every(seconds=1.0)
        result = a | b | c

        # Left-associative: (a | b) | c  → AnyStrategy flattens
        assert isinstance(result, AnyStrategy)
        assert len(result._children) == 3

    def test_triple_ampersand_composition(self) -> None:
        """Three strategies composed with ``&``, left-associative."""
        a, b, c = OnChange(), Every(n=1), Every(seconds=1.0)
        result = a & b & c

        assert isinstance(result, AllStrategy)
        assert len(result._children) == 3

    def test_clock_binding_propagates_through_any(self) -> None:
        """_bind on AnyStrategy reaches Every(seconds=...) children."""
        clock = FakeClock(0.0)
        timed = Every(seconds=10.0)
        composite = OnChange() | timed

        composite._bind(clock)

        # Without binding, timed would return True (fallback).
        # After binding at t=0, at t=5 it should return False.
        clock._time = 5.0
        assert timed.should_publish(CURRENT, PREVIOUS) is False

    def test_clock_binding_propagates_through_all(self) -> None:
        """_bind on AllStrategy reaches Every(seconds=...) children."""
        clock = FakeClock(0.0)
        timed = Every(seconds=10.0)
        composite = OnChange() & timed

        composite._bind(clock)

        clock._time = 5.0
        assert timed.should_publish(CURRENT, PREVIOUS) is False

    def test_mixed_composition(self) -> None:
        """``&`` and ``|`` can be mixed: (a & b) | c."""
        a = OnChange()
        b = Every(n=1)
        c = Every(seconds=1.0)

        result = (a & b) | c
        assert isinstance(result, AnyStrategy)
        assert len(result._children) == 2
        assert isinstance(result._children[0], AllStrategy)

    def test_base_bind_is_noop(self) -> None:
        """_StrategyBase._bind is a no-op (doesn't raise)."""
        strategy = OnChange()
        clock = FakeClock(0.0)
        strategy._bind(clock)  # should not raise
