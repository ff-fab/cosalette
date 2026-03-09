"""Public filter utilities — ``from cosalette.filters import Pt1Filter``."""

from __future__ import annotations

from cosalette._filters import Filter

try:
    from cosalette_filters_rs import (
        MedianFilter,
        OneEuroFilter,
        Pt1Filter,
    )
except ImportError:
    from cosalette._filters import (  # type: ignore[assignment]
        MedianFilter,
        OneEuroFilter,
        Pt1Filter,
    )

__all__ = ["Filter", "MedianFilter", "OneEuroFilter", "Pt1Filter"]
