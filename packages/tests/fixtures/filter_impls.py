"""Shared filter implementation tuples for dual-backend parametrization.

Each tuple contains ``pytest.param`` entries for the Python implementation,
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
_median_impls = [pytest.param(MedianFilter, id="python")]

try:
    from cosalette_filters_rs import (
        MedianFilter as RustMedianFilter,  # type: ignore[attr-defined]
    )

    _median_impls.append(pytest.param(RustMedianFilter, id="rust"))
except ImportError:
    pass

median_impls = tuple(_median_impls)

# -- OneEuroFilter ------------------------------------------------------------
_one_euro_impls = [pytest.param(OneEuroFilter, id="python")]

try:
    from cosalette_filters_rs import (
        OneEuroFilter as RustOneEuroFilter,  # type: ignore[attr-defined]
    )

    _one_euro_impls.append(pytest.param(RustOneEuroFilter, id="rust"))
except ImportError:
    pass

one_euro_impls = tuple(_one_euro_impls)
