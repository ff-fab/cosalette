"""Unit tests for cosalette.strategies — public re-export module.

Verifies that all strategy types are importable via the convenience
module ``cosalette.strategies`` and that re-exported symbols are the
same objects as their originals in ``cosalette._strategies``.
"""

from __future__ import annotations

import pytest

import cosalette._strategies as _impl
import cosalette.strategies as strategies_mod

pytestmark = pytest.mark.unit


class TestStrategiesModule:
    """cosalette.strategies re-exports all public strategy types."""

    EXPECTED_NAMES = {
        "AllStrategy",
        "AnyStrategy",
        "Every",
        "OnChange",
        "PublishStrategy",
    }

    def test_all_contains_expected_symbols(self) -> None:
        """``__all__`` matches the documented public API."""
        assert set(strategies_mod.__all__) == self.EXPECTED_NAMES

    def test_all_symbols_importable(self) -> None:
        """Every name in ``__all__`` resolves to an attribute."""
        for name in strategies_mod.__all__:
            assert hasattr(strategies_mod, name), f"{name} not found on module"

    @pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
    def test_reexport_identity(self, name: str) -> None:
        """Re-exported symbol is the same object as the private original."""
        assert getattr(strategies_mod, name) is getattr(_impl, name)
