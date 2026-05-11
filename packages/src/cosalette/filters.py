"""Public filter utilities — ``from cosalette.filters import Pt1Filter``.

Re-exports the ``Filter`` protocol (Python typing) and the concrete
Rust-powered filter classes from the embedded ``cosalette._filters_rs``
extension module.  See ADR-022.
"""

from __future__ import annotations

from cosalette._filters_rs import MedianFilter, OneEuroFilter, Pt1Filter
from cosalette._strategies._filters import Filter

__all__ = ["Filter", "MedianFilter", "OneEuroFilter", "Pt1Filter"]
