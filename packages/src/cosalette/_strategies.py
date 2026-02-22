"""Publish strategies for controlling when telemetry is published.

Implements the Strategy pattern (GoF) for publish-decision logic in the
device loop. Each strategy encapsulates a single rule; composites combine
rules with boolean operators.

See ADR-013 for design rationale and phase plan.

Strategies provided:
    - ``Every(seconds=N)`` — time-based throttle (requires ClockPort)
    - ``Every(n=N)`` — count-based throttle
    - ``OnChange()`` — exact-equality change detection
    - ``OnChange(threshold=T)`` — numeric dead-band change detection
    - ``OnChange(threshold={field: T})`` — per-field dead-band thresholds
    - ``AnyStrategy`` / ``AllStrategy`` — boolean composites via ``|`` / ``&``
"""

from __future__ import annotations

import math
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


def _is_numeric(value: object) -> bool:
    """Return ``True`` if *value* is int or float but **not** bool.

    ``bool`` is a subclass of ``int`` in Python, so we must exclude it
    explicitly to prevent ``True``/``False`` from being treated as
    ``1``/``0`` during numeric threshold comparison.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_changed(cur: int | float, prev: int | float, threshold: float) -> bool:
    """Return ``True`` if two numeric values differ beyond *threshold*.

    Handles ``NaN`` explicitly: a transition to or from ``NaN``
    always counts as a change, while ``NaN`` → ``NaN`` is treated
    as unchanged.
    """
    cur_nan = math.isnan(cur)
    prev_nan = math.isnan(prev)
    if cur_nan or prev_nan:
        # NaN mismatch → changed; both NaN → unchanged
        return cur_nan != prev_nan
    return abs(cur - prev) > threshold


class OnChange(_StrategyBase):
    """Publish when the telemetry payload changes.

    With ``threshold=None`` (default), uses exact equality
    (``current != previous``).

    When *threshold* is a ``float``, it acts as a **global** numeric
    dead-band: a leaf field must change by more than *threshold*
    (strict ``>``) to trigger a publish.  Non-numeric fields fall
    back to ``!=``.

    When *threshold* is a ``dict[str, float]``, each key names a
    leaf field with its own dead-band.  Use **dot-notation** for
    nested fields (e.g. ``{"sensor.temp": 0.5}``).  Fields not
    listed in the dict use exact equality.

    Thresholds are applied to **leaf values only**.  Nested dicts
    are traversed recursively — ``{"sensor": {"temp": 22.5}}``
    compares ``temp`` numerically, not the intermediate ``sensor``
    dict as a whole.

    In both threshold modes, structural changes (added or removed
    keys at any nesting level) always trigger a publish, and fields
    are combined with **OR** semantics — any single leaf field
    exceeding its threshold is sufficient.

    Args:
        threshold: Optional dead-band for numeric change detection.
            ``None`` → exact equality, ``float`` → global threshold,
            ``dict[str, float]`` → per-field thresholds (dot-notation
            for nested keys).
    """

    def __init__(
        self,
        *,
        threshold: float | dict[str, float] | None = None,
    ) -> None:
        if isinstance(threshold, dict):
            for field, value in threshold.items():
                if isinstance(value, bool):
                    msg = f"Threshold for '{field}' must be a number, got bool"
                    raise TypeError(msg)
                if value < 0:
                    msg = f"Threshold for '{field}' must be non-negative, got {value}"
                    raise ValueError(msg)
        elif isinstance(threshold, bool):
            msg = "Threshold must be a number, got bool"
            raise TypeError(msg)
        elif isinstance(threshold, (int, float)) and threshold < 0:
            msg = f"Threshold must be non-negative, got {threshold}"
            raise ValueError(msg)
        self._threshold = threshold

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return ``True`` when the payload differs from the last publish.

        When a threshold is configured, numeric fields are compared
        using ``abs(current - previous) > threshold`` (strict
        inequality).  Non-numeric fields and structural changes always
        use exact equality.
        """
        if previous is None:
            return True
        if self._threshold is None:
            return current != previous
        return self._check_with_threshold(current, previous)

    # -- internals ----------------------------------------------------------

    def _check_with_threshold(
        self,
        current: dict[str, object],
        previous: dict[str, object],
    ) -> bool:
        """Compare payloads using numeric dead-band thresholds.

        Recurses into nested dicts so that thresholds apply to
        **leaf** values only.  A top-level ``{"sensor": {"temp": 22.5}}``
        compares ``temp`` numerically — the intermediate ``"sensor"``
        dict is traversed, not compared as a whole.
        """
        return self._compare_dicts(current, previous, prefix="")

    def _compare_dicts(
        self,
        current: dict[str, object],
        previous: dict[str, object],
        prefix: str,
    ) -> bool:
        """Recursively compare two dicts, returning True if any field changed."""
        # Structural change at this level → always publish
        if current.keys() != previous.keys():
            return True

        for key in current:
            cur_val = current[key]
            prev_val = previous[key]
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"

            # Both dicts → recurse into the nested structure
            if isinstance(cur_val, dict) and isinstance(prev_val, dict):
                if self._compare_dicts(cur_val, prev_val, prefix=full_key):
                    return True
                continue

            if self._leaf_changed(cur_val, prev_val, full_key):
                return True

        return False

    def _leaf_changed(
        self,
        cur_val: object,
        prev_val: object,
        key: str,
    ) -> bool:
        """Return True if a single leaf field has changed beyond its threshold."""
        field_threshold = self._threshold_for(key)

        if (
            field_threshold is not None
            and _is_numeric(cur_val)
            and _is_numeric(prev_val)
        ):
            # Both numeric with a threshold — use dead-band
            assert isinstance(cur_val, (int, float))  # narrowing for mypy
            assert isinstance(prev_val, (int, float))
            return _numeric_changed(cur_val, prev_val, field_threshold)

        # Non-numeric or no threshold for this field — exact equality
        return cur_val != prev_val

    def _threshold_for(self, key: str) -> float | None:
        """Look up the threshold for *key*.

        *key* is a dot-notation path (e.g. ``"sensor.temp"``) for
        nested fields.  Returns the global float, the per-field
        value, or ``None`` if the field has no threshold entry.
        """
        if isinstance(self._threshold, dict):
            return self._threshold.get(key)
        # Global float threshold
        return self._threshold

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
        if not self._children:
            msg = "AnyStrategy requires at least one child strategy"
            raise ValueError(msg)

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
        # IMPORTANT: list comprehension, not generator — eager evaluation
        # ensures stateful children (e.g. Every(n=N)) always advance.
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
        if not self._children:
            msg = "AllStrategy requires at least one child strategy"
            raise ValueError(msg)

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
        # IMPORTANT: list comprehension, not generator — eager evaluation
        # ensures stateful children (e.g. Every(n=N)) always advance.
        results = [c.should_publish(current, previous) for c in self._children]
        return all(results)

    def on_published(self) -> None:
        """Notify all children of a publish event."""
        for child in self._children:
            child.on_published()
