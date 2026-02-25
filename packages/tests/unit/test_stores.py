"""Unit tests for cosalette._stores — persistence store backends.

Test Techniques Used:
    - Specification-based: Protocol compliance, constructor contracts
    - Round-trip Testing: save → load fidelity for all backends
    - Error Guessing: Corruption, missing files, missing keys
    - Boundary Value Analysis: Empty dicts, empty keys, nested structures
    - Equivalence Partitioning: Different backends with same behavior
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from cosalette._stores import (
    JsonFileStore,
    MemoryStore,
    NullStore,
    SqliteStore,
    Store,
)

# =============================================================================
# NullStore
# =============================================================================


class TestNullStore:
    """NullStore is a no-op backend.

    Technique: Specification-based — verifying the null-object contract.
    """

    def test_load_returns_none(self) -> None:
        """load() always returns None regardless of key."""
        store = NullStore()

        result = store.load("any-key")

        assert result is None

    def test_save_does_not_raise(self) -> None:
        """save() silently discards data without error."""
        store = NullStore()

        store.save("key", {"value": 42})  # should not raise

    def test_save_then_load_returns_none(self) -> None:
        """Data passed to save() is never persisted.

        Technique: Round-trip Testing — confirming no round-trip.
        """
        store = NullStore()

        store.save("key", {"value": 1})
        result = store.load("key")

        assert result is None

    def test_protocol_compliance(self) -> None:
        """NullStore satisfies the Store protocol via structural subtyping."""
        assert isinstance(NullStore(), Store)


# =============================================================================
# MemoryStore
# =============================================================================


class TestMemoryStore:
    """MemoryStore — in-memory dict with deep-copy isolation.

    Technique: Round-trip + Error Guessing (mutation after save).
    """

    def test_roundtrip(self) -> None:
        """save → load returns an equal dict."""
        store = MemoryStore()
        data = {"temp": 21.5, "unit": "°C"}

        store.save("sensor", data)
        result = store.load("sensor")

        assert result == data

    def test_load_missing_key_returns_none(self) -> None:
        """Loading a key that was never saved returns None."""
        store = MemoryStore()

        assert store.load("nonexistent") is None

    def test_key_isolation(self) -> None:
        """Different keys store independent data.

        Technique: Equivalence Partitioning — each key is an independent partition.
        """
        store = MemoryStore()

        store.save("a", {"v": 1})
        store.save("b", {"v": 2})

        assert store.load("a") == {"v": 1}
        assert store.load("b") == {"v": 2}

    def test_deep_copy_on_save(self) -> None:
        """Mutating the dict after save does not alter stored data.

        Technique: Error Guessing — anticipating aliasing bugs.
        """
        store = MemoryStore()
        data: dict[str, object] = {"counter": 0}

        store.save("key", data)
        data["counter"] = 999  # mutate original

        assert store.load("key") == {"counter": 0}

    def test_deep_copy_on_load(self) -> None:
        """Mutating the dict returned by load does not alter stored data.

        Technique: Error Guessing — anticipating aliasing bugs.
        """
        store = MemoryStore()
        store.save("key", {"counter": 0})

        loaded = store.load("key")
        assert loaded is not None
        loaded["counter"] = 999  # mutate loaded copy

        assert store.load("key") == {"counter": 0}

    def test_initial_data(self) -> None:
        """Constructor accepts seed data available immediately."""
        store = MemoryStore(initial={"preset": {"x": 1}})

        assert store.load("preset") == {"x": 1}

    def test_initial_data_deep_copied(self) -> None:
        """Seed data is deep-copied — mutating the original has no effect.

        Technique: Error Guessing — constructor aliasing.
        """
        seed: dict[str, dict[str, object]] = {"k": {"v": 0}}
        store = MemoryStore(initial=seed)

        seed["k"]["v"] = 999

        assert store.load("k") == {"v": 0}

    def test_empty_dict_roundtrip(self) -> None:
        """Empty dict is a valid value.

        Technique: Boundary Value Analysis — minimal data.
        """
        store = MemoryStore()

        store.save("empty", {})

        assert store.load("empty") == {}

    def test_empty_key(self) -> None:
        """Empty string is a valid key.

        Technique: Boundary Value Analysis — minimal key.
        """
        store = MemoryStore()

        store.save("", {"data": True})

        assert store.load("") == {"data": True}

    def test_nested_structure_roundtrip(self) -> None:
        """Nested dicts and lists survive the round trip.

        Technique: Boundary Value Analysis — complex structures.
        """
        store = MemoryStore()
        data: dict[str, object] = {
            "nested": {"deep": {"list": [1, 2, 3]}},
            "flag": True,
        }

        store.save("complex", data)

        assert store.load("complex") == data

    def test_protocol_compliance(self) -> None:
        """MemoryStore satisfies the Store protocol."""
        assert isinstance(MemoryStore(), Store)


# =============================================================================
# JsonFileStore
# =============================================================================


class TestJsonFileStore:
    """JsonFileStore — single JSON file with atomic writes.

    Technique: Round-trip + Error Guessing (corruption, missing file).
    """

    def test_roundtrip(self, tmp_path: Path) -> None:
        """save → load returns an equal dict."""
        store = JsonFileStore(tmp_path / "state.json")
        data = {"temp": 21.5, "unit": "°C"}

        store.save("sensor", data)
        result = store.load("sensor")

        assert result == data

    def test_key_isolation(self, tmp_path: Path) -> None:
        """Different keys are independent within the same file."""
        store = JsonFileStore(tmp_path / "state.json")

        store.save("a", {"v": 1})
        store.save("b", {"v": 2})

        assert store.load("a") == {"v": 1}
        assert store.load("b") == {"v": 2}

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Loading from a non-existent file returns None gracefully."""
        store = JsonFileStore(tmp_path / "does-not-exist.json")

        assert store.load("any") is None

    def test_load_missing_key_returns_none(self, tmp_path: Path) -> None:
        """Loading a key not present in the file returns None."""
        store = JsonFileStore(tmp_path / "state.json")
        store.save("exists", {"v": 1})

        assert store.load("other") is None

    def test_load_corrupt_file_returns_none(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Corrupt JSON triggers a warning and returns None.

        Technique: Error Guessing — file corruption scenario.
        """
        path = tmp_path / "state.json"
        path.write_text("{broken json", encoding="utf-8")
        store = JsonFileStore(path)

        with caplog.at_level(logging.WARNING):
            result = store.load("key")

        assert result is None
        assert "Corrupt or unreadable" in caplog.text

    def test_atomic_write_no_temp_file_lingers(self, tmp_path: Path) -> None:
        """After save, no .tmp file remains on disk.

        Technique: Error Guessing — leftover temp files.
        """
        path = tmp_path / "state.json"
        store = JsonFileStore(path)

        store.save("key", {"v": 1})

        tmp_file = path.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created automatically on save."""
        path = tmp_path / "sub" / "dir" / "state.json"
        store = JsonFileStore(path)

        store.save("key", {"v": 1})

        assert store.load("key") == {"v": 1}

    def test_overwrite_existing_key(self, tmp_path: Path) -> None:
        """Saving to an existing key overwrites the previous value."""
        store = JsonFileStore(tmp_path / "state.json")

        store.save("key", {"v": 1})
        store.save("key", {"v": 2})

        assert store.load("key") == {"v": 2}

    def test_json_formatting(self, tmp_path: Path) -> None:
        """File uses indent=2 and trailing newline.

        Technique: Specification-based — matching gas2mqtt formatting.
        """
        path = tmp_path / "state.json"
        store = JsonFileStore(path)

        store.save("k", {"v": 1})

        content = path.read_text(encoding="utf-8")
        expected = json.dumps({"k": {"v": 1}}, indent=2) + "\n"
        assert content == expected

    def test_save_over_corrupt_file(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """save() overwrites a corrupt file and subsequent load succeeds.

        Technique: Error Guessing — corrupt existing file doesn't block writes.
        """
        path = tmp_path / "state.json"
        path.write_text("NOT VALID JSON {{{{", encoding="utf-8")
        store = JsonFileStore(path)

        with caplog.at_level(logging.WARNING):
            store.save("sensor", {"count": 1})

        assert "Overwriting corrupt store file" in caplog.text
        assert store.load("sensor") == {"count": 1}

    def test_save_propagates_os_error(self, tmp_path: Path) -> None:
        """save() propagates OSError when the filesystem is read-only.

        Technique: Error Guessing — permission denied during write.
        """
        path = tmp_path / "readonly" / "state.json"
        path.parent.mkdir()
        store = JsonFileStore(path)

        # Seed with valid data, then make directory read-only
        store.save("key", {"v": 1})
        path.parent.chmod(0o444)

        try:
            with pytest.raises(OSError):
                store.save("key", {"v": 2})
        finally:
            path.parent.chmod(0o755)  # restore for cleanup

    def test_load_non_dict_json_returns_none(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """load() returns None when file contains valid JSON that is not a dict.

        Technique: Error Guessing — file contains a JSON array instead of object.
        """
        path = tmp_path / "state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        store = JsonFileStore(path)

        with caplog.at_level(logging.WARNING):
            result = store.load("key")

        assert result is None
        assert "non-object JSON" in caplog.text

    def test_save_overwrites_non_dict_json(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """save() discards non-dict JSON content and writes correctly.

        Technique: Error Guessing — file contains a JSON array, save replaces it.
        """
        path = tmp_path / "state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        store = JsonFileStore(path)

        with caplog.at_level(logging.WARNING):
            store.save("sensor", {"temp": 21.5})

        assert store.load("sensor") == {"temp": 21.5}

    def test_protocol_compliance(self, tmp_path: Path) -> None:
        """JsonFileStore satisfies the Store protocol."""
        assert isinstance(JsonFileStore(tmp_path / "s.json"), Store)


# =============================================================================
# SqliteStore
# =============================================================================


class TestSqliteStore:
    """SqliteStore — SQLite with WAL mode.

    Technique: Round-trip + Specification-based (WAL, auto-create).
    """

    def test_roundtrip(self, tmp_path: Path) -> None:
        """save → load returns an equal dict."""
        store = SqliteStore(tmp_path / "store.db")
        data = {"temp": 21.5, "unit": "°C"}

        store.save("sensor", data)
        result = store.load("sensor")

        assert result == data
        store.close()

    def test_key_isolation(self, tmp_path: Path) -> None:
        """Different keys store independent data."""
        store = SqliteStore(tmp_path / "store.db")

        store.save("a", {"v": 1})
        store.save("b", {"v": 2})

        assert store.load("a") == {"v": 1}
        assert store.load("b") == {"v": 2}
        store.close()

    def test_load_missing_key_returns_none(
        self,
        tmp_path: Path,
    ) -> None:
        """Loading a key that was never saved returns None."""
        store = SqliteStore(tmp_path / "store.db")

        assert store.load("nonexistent") is None
        store.close()

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        """WAL journal mode is active after construction.

        Technique: Specification-based — verifying WAL pragma.
        """
        store = SqliteStore(tmp_path / "store.db")

        # Access the internal connection to verify WAL
        cur = store._conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]

        assert mode == "wal"
        store.close()

    def test_auto_creates_table(self, tmp_path: Path) -> None:
        """The store table exists immediately after construction.

        Technique: Specification-based — auto-creation contract.
        """
        store = SqliteStore(tmp_path / "store.db")

        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='store'"
        )

        assert cur.fetchone() is not None
        store.close()

    def test_overwrite_existing_key(self, tmp_path: Path) -> None:
        """Saving to an existing key overwrites the previous value."""
        store = SqliteStore(tmp_path / "store.db")

        store.save("key", {"v": 1})
        store.save("key", {"v": 2})

        assert store.load("key") == {"v": 2}
        store.close()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created automatically."""
        path = tmp_path / "sub" / "dir" / "store.db"
        store = SqliteStore(path)

        store.save("key", {"v": 1})

        assert store.load("key") == {"v": 1}
        store.close()

    def test_nested_structure_roundtrip(self, tmp_path: Path) -> None:
        """Nested dicts and lists survive the JSON round trip.

        Technique: Boundary Value Analysis — complex structures.
        """
        store = SqliteStore(tmp_path / "store.db")
        data: dict[str, object] = {
            "nested": {"deep": {"list": [1, 2, 3]}},
            "flag": True,
        }

        store.save("complex", data)

        assert store.load("complex") == data
        store.close()

    def test_protocol_compliance(self, tmp_path: Path) -> None:
        """SqliteStore satisfies the Store protocol."""
        store = SqliteStore(tmp_path / "s.db")
        assert isinstance(store, Store)
        store.close()


# =============================================================================
# Protocol compliance (parametrised)
# =============================================================================


class TestStoreProtocol:
    """Verify all implementations satisfy the Store protocol.

    Technique: Specification-based — structural subtyping across all backends.
    """

    @pytest.mark.parametrize(
        "store",
        [
            pytest.param(NullStore(), id="NullStore"),
            pytest.param(MemoryStore(), id="MemoryStore"),
        ],
    )
    def test_instance_check_in_memory(self, store: object) -> None:
        """In-memory stores satisfy isinstance(store, Store)."""
        assert isinstance(store, Store)

    def test_json_file_store_instance_check(self, tmp_path: Path) -> None:
        """JsonFileStore satisfies isinstance(store, Store)."""
        assert isinstance(JsonFileStore(tmp_path / "s.json"), Store)

    def test_sqlite_store_instance_check(
        self,
        tmp_path: Path,
    ) -> None:
        """SqliteStore satisfies isinstance(store, Store)."""
        store = SqliteStore(tmp_path / "s.db")
        assert isinstance(store, Store)
        store.close()
