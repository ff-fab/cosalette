"""Publish strategies for controlling when telemetry is published.

Implements the Strategy pattern (GoF) for publish-decision logic in the
device loop. Each strategy encapsulates a single rule; composites combine
rules with boolean operators.

See ADR-013 for design rationale and phase plan.

Strategies provided:
    - ``Every(seconds=N)`` — time-based throttle (requires ClockPort)
    - ``Every(n=N)`` — count-based throttle
    - ``OnChange()`` — exact-equality change detection
    - ``AnyStrategy`` / ``AllStrategy`` — boolean composites via ``|`` / ``&``
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cosalette._clock import ClockPort

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PublishStrategy(Protocol):
    """Publish-decision contract for the device loop.

    The framework calls ``_bind`` before the loop to inject the clock,
    ``should_publish`` each iteration, and ``on_published`` after a
    successful publish to let the strategy reset internal state
    (counters, timestamps, etc.).
    """

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Decide whether the current reading should be published.

        Args:
            current: The latest telemetry payload.
            previous: The last *published* payload, or ``None`` on the
                very first call.

        Returns:
            ``True`` if the framework should publish ``current``.
        """
        ...

    def on_published(self) -> None:
        """Called after a successful publish to reset internal state."""
        ...

    def _bind(self, clock: ClockPort) -> None:
        """Inject a :class:`ClockPort` for timing-aware strategies.

        Called by the framework before the device loop starts.
        Strategies that don't need a clock should no-op.
        """
        ...


# ---------------------------------------------------------------------------
# Abstract base with operator support
# ---------------------------------------------------------------------------


class _StrategyBase:
    """Concrete base providing ``|`` (OR) and ``&`` (AND) composition.

    All shipped strategies inherit from this class so users can write
    expressive combinations such as::

        strategy = Every(seconds=60) | OnChange()
    """

    def __or__(self, other: _StrategyBase) -> AnyStrategy:
        """Combine two strategies with OR semantics."""
        return AnyStrategy(self, other)

    def __and__(self, other: _StrategyBase) -> AllStrategy:
        """Combine two strategies with AND semantics."""
        return AllStrategy(self, other)

    # Default no-op; subclasses that need a clock override this.
    def _bind(self, clock: ClockPort) -> None:
        """Inject a :class:`ClockPort` for timing-aware strategies.

        The framework calls this before the device loop starts.
        The default implementation is a no-op; override in subclasses
        that depend on elapsed time.
        """

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Decide whether the current reading should be published."""
        raise NotImplementedError  # pragma: no cover

    def on_published(self) -> None:
        """Called after a successful publish."""
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# Every (time or count)
# ---------------------------------------------------------------------------


class Every(_StrategyBase):
    """Time-based or count-based publish throttle.

    Exactly **one** of ``seconds`` or ``n`` must be provided.

    ``Every(seconds=30)``
        Publish at most once every 30 seconds.  Requires a
        :class:`ClockPort` injected via ``_bind()``.  Before binding,
        ``should_publish`` always returns ``True`` (safe fallback).

    ``Every(n=5)``
        Publish every 5th reading.  No clock dependency.

    Raises:
        ValueError: If both, neither, or non-positive values are given.
    """

    def __init__(
        self,
        *,
        seconds: float | None = None,
        n: int | None = None,
    ) -> None:
        if seconds is not None and n is not None:
            msg = "Specify exactly one of 'seconds' or 'n', not both"
            raise ValueError(msg)
        if seconds is None and n is None:
            msg = "Specify exactly one of 'seconds' or 'n'"
            raise ValueError(msg)

        if seconds is not None and seconds <= 0:
            msg = "'seconds' must be positive"
            raise ValueError(msg)
        if n is not None and n <= 0:
            msg = "'n' must be positive"
            raise ValueError(msg)

        self._seconds = seconds
        self._n = n

        # Time-mode state
        self._clock: ClockPort | None = None
        self._last_publish_time: float | None = None

        # Count-mode state
        self._counter: int = 0

    # -- clock injection ----------------------------------------------------

    def _bind(self, clock: ClockPort) -> None:
        """Inject a clock for time-based throttling."""
        self._clock = clock
        self._last_publish_time = clock.now()

    # -- protocol -----------------------------------------------------------

    def should_publish(
        self,
        current: dict[str, object],  # noqa: ARG002
        previous: dict[str, object] | None,  # noqa: ARG002
    ) -> bool:
        """Return ``True`` when enough time/calls have elapsed."""
        if self._seconds is not None:
            return self._should_publish_time()
        return self._should_publish_count()

    def on_published(self) -> None:
        """Record publish timestamp or reset counter."""
        if self._seconds is not None:
            if self._clock is not None:
                self._last_publish_time = self._clock.now()
        else:
            self._counter = 0

    # -- internals ----------------------------------------------------------

    def _should_publish_time(self) -> bool:
        """Time-mode: check elapsed seconds since last publish."""
        if self._clock is None:
            # Not yet bound — safe fallback: always publish.
            return True
        assert self._seconds is not None  # guarded by constructor
        assert self._last_publish_time is not None  # set in _bind
        elapsed = self._clock.now() - self._last_publish_time
        return elapsed >= self._seconds

    def _should_publish_count(self) -> bool:
        """Count-mode: increment counter and check threshold."""
        self._counter += 1
        assert self._n is not None  # guarded by constructor
        return self._counter >= self._n


# ---------------------------------------------------------------------------
# OnChange
# ---------------------------------------------------------------------------


class OnChange(_StrategyBase):
    """Publish when the telemetry payload changes.

    With ``threshold=None`` (default, Phase 1), uses exact equality
    (``current != previous``).

    Args:
        threshold: Reserved for Phase 2.  If provided, a
            ``NotImplementedError`` is raised in ``should_publish``.
    """

    def __init__(
        self,
        *,
        threshold: float | dict[str, float] | None = None,
    ) -> None:
        self._threshold = threshold

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return ``True`` when the payload differs from the last publish.

        Raises:
            NotImplementedError: If ``threshold`` was set (Phase 2).
        """
        if self._threshold is not None:
            msg = (
                "Threshold-based change detection will be available in a future release"
            )
            raise NotImplementedError(msg)
        if previous is None:
            return True
        return current != previous

    def on_published(self) -> None:
        """No-op — ``OnChange`` is stateless."""


# ---------------------------------------------------------------------------
# Composites
# ---------------------------------------------------------------------------


class AnyStrategy(_StrategyBase):
    """OR-composite: publishes if **any** child says yes.

    Nested ``AnyStrategy`` instances are automatically flattened::

        AnyStrategy(AnyStrategy(a, b), c)  →  AnyStrategy(a, b, c)
    """

    def __init__(self, *children: _StrategyBase) -> None:
        self._children: list[_StrategyBase] = []
        for child in children:
            if isinstance(child, AnyStrategy):
                self._children.extend(child._children)
            else:
                self._children.append(child)

    def _bind(self, clock: ClockPort) -> None:
        """Propagate clock binding to all children."""
        for child in self._children:
            child._bind(clock)

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return ``True`` if **any** child returns ``True``.

        All children are evaluated eagerly (no short-circuit) so that
        stateful strategies like ``Every(n=N)`` always advance their
        internal counters.
        """
        results = [c.should_publish(current, previous) for c in self._children]
        return any(results)

    def on_published(self) -> None:
        """Notify all children of a publish event."""
        for child in self._children:
            child.on_published()


class AllStrategy(_StrategyBase):
    """AND-composite: publishes only if **all** children say yes.

    Nested ``AllStrategy`` instances are automatically flattened::

        AllStrategy(AllStrategy(a, b), c)  →  AllStrategy(a, b, c)
    """

    def __init__(self, *children: _StrategyBase) -> None:
        self._children: list[_StrategyBase] = []
        for child in children:
            if isinstance(child, AllStrategy):
                self._children.extend(child._children)
            else:
                self._children.append(child)

    def _bind(self, clock: ClockPort) -> None:
        """Propagate clock binding to all children."""
        for child in self._children:
            child._bind(clock)

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return ``True`` only if **all** children return ``True``.

        All children are evaluated eagerly (no short-circuit) so that
        stateful strategies like ``Every(n=N)`` always advance their
        internal counters.
        """
        results = [c.should_publish(current, previous) for c in self._children]
        return all(results)

    def on_published(self) -> None:
        """Notify all children of a publish event."""
        for child in self._children:
            child.on_published()
