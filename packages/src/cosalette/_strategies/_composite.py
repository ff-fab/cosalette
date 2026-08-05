"""Boolean composite strategies (OR / AND)."""

from __future__ import annotations

from typing import override

from cosalette._clock import ClockPort
from cosalette._strategies._base import _StrategyBase


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

    @override
    def _bind(self, clock: ClockPort) -> None:
        """Propagate clock binding to all children."""
        for child in self._children:
            child._bind(clock)

    @override
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

    @override
    def on_published(self) -> None:
        """Notify all children of a publish event."""
        for child in self._children:
            child.on_published()

    @override
    def __repr__(self) -> str:
        children = ", ".join(repr(c) for c in self._children)
        return f"AnyStrategy({children})"


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

    @override
    def _bind(self, clock: ClockPort) -> None:
        """Propagate clock binding to all children."""
        for child in self._children:
            child._bind(clock)

    @override
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

    @override
    def on_published(self) -> None:
        """Notify all children of a publish event."""
        for child in self._children:
            child.on_published()

    @override
    def __repr__(self) -> str:
        children = ", ".join(repr(c) for c in self._children)
        return f"AllStrategy({children})"
