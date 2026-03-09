"""Shared filter implementation lists for dual-backend parametrization.

Each list contains ``pytest.param`` entries for the Python implementation,
plus the Rust implementation when ``cosalette_filters_rs`` is installed.
Import these in test modules and pass them to ``@pytest.mark.parametrize``.
"""

from __future__ import annotations

import pytest

from cosalette._filters import MedianFilter, OneEuroFilter, Pt1Filter

# -- Pt1Filter ---------------------------------------------------------------
_pt1_impls = [pytest.param(Pt1Filter, id="python")]

try:
    from cosalette_filters_rs import (
        Pt1Filter as RustPt1Filter,  # type: ignore[attr-defined]
    )

    _pt1_impls.append(pytest.param(RustPt1Filter, id="rust"))
except ImportError:
    pass

pt1_impls = tuple(_pt1_impls)

# -- MedianFilter ------------------------------------------------------------
median_impls = (pytest.param(MedianFilter, id="python"),)

# Future: append RustMedianFilter when cosalette_filters_rs exposes it

# -- OneEuroFilter ------------------------------------------------------------
one_euro_impls = (pytest.param(OneEuroFilter, id="python"),)

# Future: append RustOneEuroFilter when cosalette_filters_rs exposes it
