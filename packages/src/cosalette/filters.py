"""Public filter utilities — ``from cosalette.filters import Pt1Filter``."""

from __future__ import annotations

from cosalette._filters import Filter
from cosalette._filters import MedianFilter as _PythonMedianFilter
from cosalette._filters import OneEuroFilter as _PythonOneEuroFilter
from cosalette._filters import Pt1Filter as _PythonPt1Filter

try:
    from cosalette_filters_rs import MedianFilter as _RustMedianFilter
    from cosalette_filters_rs import OneEuroFilter as _RustOneEuroFilter
    from cosalette_filters_rs import Pt1Filter as _RustPt1Filter

    _HAS_RUST_FILTERS = True
except ImportError:
    _HAS_RUST_FILTERS = False

Pt1Filter = _RustPt1Filter if _HAS_RUST_FILTERS else _PythonPt1Filter
MedianFilter = _RustMedianFilter if _HAS_RUST_FILTERS else _PythonMedianFilter
OneEuroFilter = _RustOneEuroFilter if _HAS_RUST_FILTERS else _PythonOneEuroFilter

__all__ = ["Filter", "MedianFilter", "OneEuroFilter", "Pt1Filter"]
