"""Unit tests for public re-export facade modules.

Verifies that ``cosalette.persist``, ``cosalette.stores``, and
``cosalette.strategies`` correctly re-export their advertised APIs.

Test Techniques Used:
- Specification-based Testing: Verify public __all__ contract
- Round-trip Testing: Import path equivalence
"""

from __future__ import annotations

import pytest

import cosalette.persist as persist_mod
import cosalette.stores as stores_mod
import cosalette.strategies as strategies_mod

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
        """SaveOnChange available via public path.

        Technique: Round-trip — import equivalence.
        """
        from cosalette.persist import SaveOnChange

        assert SaveOnChange is not None


class TestStoresFacade:
    """cosalette.stores re-exports."""

    def test_all_stores_symbols_importable(self) -> None:
        """Every name in stores.__all__ resolves to an object.

        Technique: Specification-based — verify contract.
        """
        for name in stores_mod.__all__:
            assert hasattr(stores_mod, name), f"stores.{name} not found"

    def test_stores_exports_json_file_store(self) -> None:
        """JsonFileStore available via public path.

        Technique: Round-trip — import equivalence.
        """
        from cosalette.stores import JsonFileStore

        assert JsonFileStore is not None


class TestStrategiesFacade:
    """cosalette.strategies re-exports."""

    def test_all_strategies_symbols_importable(self) -> None:
        """Every name in strategies.__all__ resolves to an object.

        Technique: Specification-based — verify contract.
        """
        for name in strategies_mod.__all__:
            assert hasattr(strategies_mod, name), f"strategies.{name} not found"

    def test_strategies_exports_every(self) -> None:
        """Every available via public path.

        Technique: Round-trip — import equivalence.
        """
        from cosalette.strategies import Every

        assert Every is not None
