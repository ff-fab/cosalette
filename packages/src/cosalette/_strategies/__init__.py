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

from cosalette._strategies._base import (
    PublishStrategy,
    _is_numeric,
    _numeric_changed,
    _StrategyBase,
)
from cosalette._strategies._composite import AllStrategy, AnyStrategy
from cosalette._strategies._every import Every
from cosalette._strategies._onchange import OnChange

__all__ = [
    "AllStrategy",
    "AnyStrategy",
    "Every",
    "OnChange",
    "PublishStrategy",
    "_StrategyBase",
    "_is_numeric",
    "_numeric_changed",
]
