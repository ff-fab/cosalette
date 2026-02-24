"""Unit tests for DeviceStore — per-device scoped persistence wrapper.

Test Techniques Used:
    - Specification-based: MutableMapping contract, lifecycle methods
    - Round-trip Testing: load → modify → save → reload fidelity
    - State Transition: clean → dirty → clean lifecycle
    - Error Guessing: Missing keys, empty store, nested mutation
"""

from __future__ import annotations

import pytest

from cosalette._stores import DeviceStore, MemoryStore

# =============================================================================
# Lifecycle
# =============================================================================


class TestDeviceStoreLifecycle:
    """DeviceStore lifecycle: load, save, and reload.

    Technique: Round-trip Testing + State Transition.
    """

    def test_load_populates_from_backend(self) -> None:
        """load() reads existing data from the backend store."""
        backend = MemoryStore(initial={"sensor": {"temp": 21.5}})
        store = DeviceStore(backend, "sensor")

        store.load()

        assert store["temp"] == 21.5

    def test_save_persists_to_backend(self) -> None:
        """save() writes current data back to the backend."""
        backend = MemoryStore()
        store = DeviceStore(backend, "sensor")
        store.load()
        store["temp"] = 22.0

        store.save()

        assert backend.load("sensor") == {"temp": 22.0}

    def test_load_from_empty_backend_gives_empty_dict(self) -> None:
        """Loading from a backend with no saved data yields empty state.

        Technique: Error Guessing — missing key in backend.
        """
        backend = MemoryStore()
        store = DeviceStore(backend, "sensor")

        store.load()

        assert len(store) == 0

    def test_save_then_reload_preserves_data(self) -> None:
        """Full round-trip: save → create new DeviceStore → load.

        Technique: Round-trip Testing.
        """
        backend = MemoryStore()
        store = DeviceStore(backend, "sensor")
        store.load()
        store["count"] = 42

        store.save()

        store2 = DeviceStore(backend, "sensor")
        store2.load()
        assert store2["count"] == 42

    def test_dirty_flag_lifecycle(self) -> None:
        """Dirty flag transitions: clean after load, dirty after set, clean after save.

        Technique: State Transition.
        """
        backend = MemoryStore()
        store = DeviceStore(backend, "sensor")

        store.load()
        assert not store.dirty

        store["x"] = 1
        assert store.dirty

        store.save()
        assert not store.dirty


# =============================================================================
# Dict-like access
# =============================================================================


class TestDeviceStoreDictAccess:
    """DeviceStore MutableMapping contract.

    Technique: Specification-based — verifying dict interface.
    """

    @pytest.fixture
    def store(self) -> DeviceStore:
        """A loaded DeviceStore with seed data."""
        backend = MemoryStore(initial={"dev": {"a": 1, "b": 2}})
        s = DeviceStore(backend, "dev")
        s.load()
        return s

    def test_getitem(self, store: DeviceStore) -> None:
        """__getitem__ returns the value for an existing key."""
        assert store["a"] == 1

    def test_getitem_missing_raises_key_error(self, store: DeviceStore) -> None:
        """__getitem__ raises KeyError for a missing key.

        Technique: Error Guessing.
        """
        with pytest.raises(KeyError):
            store["missing"]

    def test_setitem(self, store: DeviceStore) -> None:
        """__setitem__ adds or updates a key."""
        store["c"] = 3
        assert store["c"] == 3

    def test_delitem(self, store: DeviceStore) -> None:
        """__delitem__ removes a key."""
        del store["a"]
        assert "a" not in store

    def test_delitem_missing_raises_key_error(self, store: DeviceStore) -> None:
        """__delitem__ raises KeyError for a missing key."""
        with pytest.raises(KeyError):
            del store["missing"]

    def test_contains(self, store: DeviceStore) -> None:
        """__contains__ returns True for existing keys, False otherwise."""
        assert "a" in store
        assert "missing" not in store

    def test_len(self, store: DeviceStore) -> None:
        """__len__ returns the number of items."""
        assert len(store) == 2

    def test_iter(self, store: DeviceStore) -> None:
        """__iter__ yields all keys."""
        assert set(store) == {"a", "b"}

    def test_get_with_default(self, store: DeviceStore) -> None:
        """get() returns default value for missing keys."""
        assert store.get("missing", 99) == 99
        assert store.get("a") == 1

    def test_setdefault_new_key(self, store: DeviceStore) -> None:
        """setdefault() inserts and returns default for missing key."""
        result = store.setdefault("c", 3)
        assert result == 3
        assert store["c"] == 3

    def test_setdefault_existing_key(self, store: DeviceStore) -> None:
        """setdefault() returns existing value without overwriting."""
        result = store.setdefault("a", 99)
        assert result == 1

    def test_update(self, store: DeviceStore) -> None:
        """update() merges a dict into the store."""
        store.update({"c": 3, "d": 4})
        assert store["c"] == 3
        assert store["d"] == 4

    def test_update_with_kwargs(self, store: DeviceStore) -> None:
        """update() accepts keyword arguments."""
        store.update(x=10, y=20)
        assert store["x"] == 10
        assert store["y"] == 20

    def test_to_dict_returns_copy(self, store: DeviceStore) -> None:
        """to_dict() returns a shallow copy — mutation doesn't affect store."""
        d = store.to_dict()
        d["a"] = 999
        assert store["a"] == 1

    def test_keys(self, store: DeviceStore) -> None:
        """keys() returns a view of the store's keys."""
        assert set(store.keys()) == {"a", "b"}

    def test_values(self, store: DeviceStore) -> None:
        """values() returns a view of the store's values."""
        assert set(store.values()) == {1, 2}

    def test_items(self, store: DeviceStore) -> None:
        """items() returns a view of key-value pairs."""
        assert set(store.items()) == {("a", 1), ("b", 2)}

    def test_repr(self) -> None:
        """__repr__ includes key and dirty flag."""
        store = DeviceStore(MemoryStore(), "mydev")
        assert repr(store) == "DeviceStore(key='mydev', dirty=False)"


# =============================================================================
# Dirty tracking
# =============================================================================


class TestDeviceStoreDirtyTracking:
    """DeviceStore dirty-tracking state transitions.

    Technique: State Transition Testing.
    """

    def test_clean_after_load(self) -> None:
        """Store is clean immediately after load."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()
        assert not store.dirty

    def test_dirty_after_setitem(self) -> None:
        """__setitem__ marks the store dirty."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()
        store["x"] = 1
        assert store.dirty

    def test_dirty_after_delitem(self) -> None:
        """__delitem__ marks the store dirty."""
        backend = MemoryStore(initial={"dev": {"x": 1}})
        store = DeviceStore(backend, "dev")
        store.load()

        del store["x"]

        assert store.dirty

    def test_dirty_after_setdefault_new_key(self) -> None:
        """setdefault() for a new key marks dirty."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()

        store.setdefault("x", 1)

        assert store.dirty

    def test_not_dirty_after_setdefault_existing_key(self) -> None:
        """setdefault() for an existing key does NOT mark dirty."""
        backend = MemoryStore(initial={"dev": {"x": 1}})
        store = DeviceStore(backend, "dev")
        store.load()

        store.setdefault("x", 99)

        assert not store.dirty

    def test_dirty_after_update(self) -> None:
        """update() marks the store dirty."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()

        store.update({"x": 1})

        assert store.dirty

    def test_dirty_after_mark_dirty(self) -> None:
        """mark_dirty() explicitly sets the dirty flag."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()

        store.mark_dirty()

        assert store.dirty

    def test_clean_after_save(self) -> None:
        """save() clears the dirty flag."""
        store = DeviceStore(MemoryStore(), "dev")
        store.load()
        store["x"] = 1
        assert store.dirty

        store.save()

        assert not store.dirty

    def test_mark_dirty_escape_hatch_for_nested_mutation(self) -> None:
        """mark_dirty() lets callers flag nested mutations.

        Technique: Error Guessing — store can't detect list.append().
        """
        store = DeviceStore(MemoryStore(), "dev")
        store.load()
        store["items"] = [1, 2]
        store.save()
        assert not store.dirty

        store["items"].append(3)  # type: ignore[union-attr]
        # Store can't detect this — still clean
        assert not store.dirty

        store.mark_dirty()
        assert store.dirty


# =============================================================================
# Integration with MemoryStore backend
# =============================================================================


class TestDeviceStoreIntegration:
    """Integration tests for DeviceStore with a real MemoryStore backend.

    Technique: Round-trip Testing + Equivalence Partitioning (key isolation).
    """

    def test_full_lifecycle(self) -> None:
        """Full lifecycle: create → load → modify → save → reload → verify."""
        backend = MemoryStore()

        # First session
        store1 = DeviceStore(backend, "sensor")
        store1.load()
        store1["count"] = 0
        store1["count"] = store1["count"] + 1
        store1.save()

        # Second session — fresh DeviceStore, same backend
        store2 = DeviceStore(backend, "sensor")
        store2.load()
        assert store2["count"] == 1
        store2["count"] = store2["count"] + 1
        store2.save()

        # Verify backend state
        assert backend.load("sensor") == {"count": 2}

    def test_key_isolation(self) -> None:
        """Two DeviceStores on the same backend with different keys don't interfere.

        Technique: Equivalence Partitioning — each key = independent partition.
        """
        backend = MemoryStore()

        store_a = DeviceStore(backend, "sensor_a")
        store_a.load()
        store_a["value"] = 100
        store_a.save()

        store_b = DeviceStore(backend, "sensor_b")
        store_b.load()
        store_b["value"] = 200
        store_b.save()

        # Reload and verify isolation
        check_a = DeviceStore(backend, "sensor_a")
        check_a.load()
        assert check_a["value"] == 100

        check_b = DeviceStore(backend, "sensor_b")
        check_b.load()
        assert check_b["value"] == 200
