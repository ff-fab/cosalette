"""Unit tests for public re-export facade modules.

Verifies that ``cosalette.persist``, ``cosalette.stores``, and
``cosalette.strategies`` correctly re-export their advertised APIs.

Test Techniques Used:
- Specification-based Testing: Verify public __all__ contract
- Round-trip Testing: Import path equivalence (identity check)
"""

from __future__ import annotations

import pytest

import cosalette.persist as persist_mod
import cosalette.stores as stores_mod
import cosalette.strategies as strategies_mod
from cosalette._persist import SaveOnChange as _SaveOnChange
from cosalette._stores import JsonFileStore as _JsonFileStore
from cosalette._strategies import Every as _Every

pytestmark = pytest.mark.unit


class TestPersistFacade:
    """cosalette.persist re-exports."""

    def test_all_persist_symbols_importable(self) -> None:
        """Every name in persist.__all__ resolves to an object.

        Technique: Specification-based — verify contract.
        """
        for name in persist_mod.__all__:
            assert hasattr(persist_mod, name), f"persist.{name} not found"

    def test_persist_exports_save_on_change(self) -> None:
        """SaveOnChange re-export is the canonical implementation.

        Technique: Round-trip — import identity.
        """
        assert persist_mod.SaveOnChange is _SaveOnChange


class TestStoresFacade:
    """cosalette.stores re-exports."""

    def test_all_stores_symbols_importable(self) -> None:
        """Every name in stores.__all__ resolves to an object.

        Technique: Specification-based — verify contract.
        """
        for name in stores_mod.__all__:
            assert hasattr(stores_mod, name), f"stores.{name} not found"

    def test_stores_exports_json_file_store(self) -> None:
        """JsonFileStore re-export is the canonical implementation.

        Technique: Round-trip — import identity.
        """
        assert stores_mod.JsonFileStore is _JsonFileStore


class TestStrategiesFacade:
    """cosalette.strategies re-exports."""

    def test_all_strategies_symbols_importable(self) -> None:
        """Every name in strategies.__all__ resolves to an object.

        Technique: Specification-based — verify contract.
        """
        for name in strategies_mod.__all__:
            assert hasattr(strategies_mod, name), f"strategies.{name} not found"

    def test_strategies_exports_every(self) -> None:
        """Every re-export is the canonical implementation.

        Technique: Round-trip — import identity.
        """
        assert strategies_mod.Every is _Every
