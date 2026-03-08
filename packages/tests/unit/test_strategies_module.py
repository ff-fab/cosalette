"""Unit tests for cosalette.strategies — public re-export module.

Test Techniques Used:
    - Specification-based Testing: Public API surface, ``__all__``
      completeness against the documented strategy types.
    - Identity Testing: Re-exported symbols are the *same* objects
      as the originals in ``cosalette._strategies``.
    - Representation Testing: ``__repr__`` correctness for all
      strategy classes including composites.

See Also:
    ADR-006 — Hexagonal architecture (public exports).
"""

from __future__ import annotations

import pytest

import cosalette._strategies as _impl
import cosalette.strategies as strategies_mod
from cosalette._strategies import Every, OnChange

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


class TestStrategyRepr:
    """``__repr__`` produces reconstructable descriptions.

    Technique: Specification-based — repr output matches constructor form.
    """

    def test_every_seconds(self) -> None:
        """Every(seconds=...) includes the seconds value."""
        assert repr(Every(seconds=5.0)) == "Every(seconds=5.0)"

    def test_every_n(self) -> None:
        """Every(n=...) includes the count value."""
        assert repr(Every(n=3)) == "Every(n=3)"

    def test_on_change_no_threshold(self) -> None:
        """OnChange() with no threshold shows empty parens."""
        assert repr(OnChange()) == "OnChange()"

    def test_on_change_float_threshold(self) -> None:
        """OnChange(threshold=float) includes the threshold."""
        assert repr(OnChange(threshold=0.5)) == "OnChange(threshold=0.5)"

    def test_on_change_dict_threshold(self) -> None:
        """OnChange(threshold=dict) includes the per-field thresholds."""
        result = repr(OnChange(threshold={"temp": 0.5}))
        assert result == "OnChange(threshold={'temp': 0.5})"

    def test_any_strategy_via_or(self) -> None:
        """OR-composite repr lists children."""
        s = Every(seconds=5.0) | OnChange()
        assert repr(s) == "AnyStrategy(Every(seconds=5.0), OnChange())"

    def test_all_strategy_via_and(self) -> None:
        """AND-composite repr lists children."""
        s = Every(seconds=5.0) & OnChange()
        assert repr(s) == "AllStrategy(Every(seconds=5.0), OnChange())"
