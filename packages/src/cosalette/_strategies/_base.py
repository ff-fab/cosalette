"""Base protocol and abstract strategy with operator support."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cosalette._clock import ClockPort

if TYPE_CHECKING:
    from cosalette._strategies._composite import AllStrategy, AnyStrategy

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
        from cosalette._strategies._composite import AnyStrategy

        return AnyStrategy(self, other)

    def __and__(self, other: _StrategyBase) -> AllStrategy:
        """Combine two strategies with AND semantics."""
        from cosalette._strategies._composite import AllStrategy

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
# Numeric helpers shared with OnChange
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
