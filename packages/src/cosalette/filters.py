"""Public filter utilities — ``from cosalette.filters import Pt1Filter``."""

from __future__ import annotations

from cosalette._filters import Filter, MedianFilter, OneEuroFilter
from cosalette._filters import Pt1Filter as _PythonPt1Filter

try:
    from cosalette_filters_rs import Pt1Filter as _RustPt1Filter

    _HAS_RUST_FILTERS = True
except ImportError:
    _HAS_RUST_FILTERS = False

Pt1Filter = _RustPt1Filter if _HAS_RUST_FILTERS else _PythonPt1Filter

__all__ = ["Filter", "MedianFilter", "OneEuroFilter", "Pt1Filter"]
