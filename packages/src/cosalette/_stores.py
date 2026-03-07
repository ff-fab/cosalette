"""Persistence store backends for device state.

Provides key-value storage primitives for persisting device state
across restarts.  Each backend stores string keys mapped to
JSON-serializable dicts.

Stores provided:
    - ``NullStore`` — no-op (disabled persistence / dry-run)
    - ``MemoryStore`` — in-memory dict with deep-copy isolation (testing)
    - ``JsonFileStore`` — single JSON file with atomic writes
    - ``SqliteStore`` — SQLite database with WAL mode
"""

from __future__ import annotations

import copy
import logging
import os
import sqlite3
from collections.abc import ItemsView, Iterator, KeysView, ValuesView
from pathlib import Path
from typing import Protocol, runtime_checkable

from cosalette._json import JSONDecodeError, dumps_pretty, loads

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Store(Protocol):
    """Key-value persistence for device state.

    Each key maps to a JSON-serializable dict.  Implementations
    must handle missing keys (return ``None``) and create storage
    locations as needed.
    """

    def load(self, key: str) -> dict[str, object] | None:
        """Load state for the given key.  Returns ``None`` if not found."""
        ...

    def save(self, key: str, data: dict[str, object]) -> None:
        """Persist state for the given key."""
        ...


# ---------------------------------------------------------------------------
# Null backend
# ---------------------------------------------------------------------------


class NullStore:
    """No-op store — ``load`` always returns ``None``, ``save`` is silent.

    Use when persistence is disabled or for dry-run modes.
    """

    def load(self, key: str) -> dict[str, object] | None:  # noqa: ARG002
        """Always returns ``None``."""
        return None

    def save(self, key: str, data: dict[str, object]) -> None:  # noqa: ARG002
        """Does nothing."""

    def __repr__(self) -> str:
        return "NullStore()"


# ---------------------------------------------------------------------------
# Memory backend
# ---------------------------------------------------------------------------


class MemoryStore:
    """In-memory store backed by a plain ``dict``.

    Both ``load`` and ``save`` deep-copy data so that callers cannot
    mutate internal state by accident.  Designed for tests — mirrors
    the ``FakeStorage`` pattern from gas2mqtt.

    Parameters
    ----------
    initial:
        Optional seed data.  The mapping is deep-copied on construction.
    """

    def __init__(
        self,
        initial: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self._data: dict[str, dict[str, object]] = (
            copy.deepcopy(initial) if initial else {}
        )

    def load(self, key: str) -> dict[str, object] | None:
        """Return a deep copy of the stored dict, or ``None``."""
        value = self._data.get(key)
        if value is None:
            return None
        return copy.deepcopy(value)

    def save(self, key: str, data: dict[str, object]) -> None:
        """Store a deep copy of *data*."""
        self._data[key] = copy.deepcopy(data)

    def __repr__(self) -> str:
        return f"MemoryStore(keys={list(self._data.keys())})"


# ---------------------------------------------------------------------------
# JSON file backend
# ---------------------------------------------------------------------------


class JsonFileStore:
    """Single-file JSON store with atomic writes.

    All keys live as top-level keys in one JSON object.  Writes use
    a *write-to-temp + os.replace* pattern for atomicity so that a
    crash mid-write never corrupts the file.

    Parameters
    ----------
    path:
        Path to the JSON file.  Parent directories are created
        automatically on the first ``save``.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self, key: str) -> dict[str, object] | None:
        """Load a key from the JSON file.

        Returns ``None`` when the file does not exist, the key is
        missing, or the file contains invalid JSON (a warning is
        logged in the latter case).
        """
        if not self._path.exists():
            return None

        try:
            text = self._path.read_text(encoding="utf-8")
            data = loads(text)
        except (JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt or unreadable store file %s: %s", self._path, exc)
            return None

        if not isinstance(data, dict):
            logger.warning(
                "Store file %s contains non-object JSON, treating as empty",
                self._path,
            )
            return None

        return data.get(key)

    def save(self, key: str, data: dict[str, object]) -> None:
        """Persist *data* under *key* using an atomic write.

        The full JSON object is read (if it exists), the key is
        updated, and the result is written to a temporary file
        before being atomically moved into place.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing content (if any)
        existing: dict[str, object] = {}
        if self._path.exists():
            try:
                text = self._path.read_text(encoding="utf-8")
                parsed = loads(text)
                if isinstance(parsed, dict):
                    existing = parsed
                else:
                    logger.warning(
                        "Overwriting non-object JSON in store file %s",
                        self._path,
                    )
            except (JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Overwriting corrupt store file %s: %s",
                    self._path,
                    exc,
                )

        existing[key] = data

        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(
            dumps_pretty(existing) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, self._path)

    def __repr__(self) -> str:
        return f"JsonFileStore(path={self._path!r})"


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


class SqliteStore:
    """SQLite-backed store using WAL mode for power-loss resistance.

    Each key is a row in a ``store`` table; the value is stored as a
    JSON text column.  The table is auto-created on first use.

    Parameters
    ----------
    path:
        Path to the SQLite database file.  Parent directories are
        created automatically.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS store "
            "(key TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._conn.commit()

    def load(self, key: str) -> dict[str, object] | None:
        """Load JSON data for *key*, or ``None`` if not present."""
        cur = self._conn.execute("SELECT data FROM store WHERE key = ?", (key,))
        row = cur.fetchone()
        if row is None:
            return None
        return loads(row[0])  # type: ignore[no-any-return]

    def save(self, key: str, data: dict[str, object]) -> None:
        """Insert or replace *data* for *key*."""
        self._conn.execute(
            "INSERT OR REPLACE INTO store (key, data) VALUES (?, ?)",
            (key, dumps_pretty(data)),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __repr__(self) -> str:
        return f"SqliteStore(path={self._path!r})"


# ---------------------------------------------------------------------------
# Per-device scoped wrapper
# ---------------------------------------------------------------------------


class DeviceStore:
    """Per-device scoped store with dirty tracking.

    Wraps a :class:`Store` backend, automatically keyed by device name.
    Behaves like a dict (``MutableMapping`` interface) — handlers read
    and write state naturally via ``store["key"] = value``.

    The framework creates one ``DeviceStore`` per device and manages
    its lifecycle:

    1. ``load()`` — called before the first handler invocation.
    2. Handler reads/writes via dict-like access.
    3. ``save()`` — always called on shutdown (safety net).

    **Dirty tracking:** after ``load`` or ``save``, the store is
    "clean".  Any ``__setitem__`` or ``__delitem__`` marks it "dirty".
    For nested mutations the store cannot detect automatically, call
    :meth:`mark_dirty` explicitly.
    """

    def __init__(self, backend: Store, key: str) -> None:
        self._backend = backend
        self._key = key
        self._data: dict[str, object] = {}
        self._dirty = False
        self._loaded = False

    # --- Lifecycle (called by framework) -----------------------------------

    def load(self) -> None:
        """Load state from backend.  Called by framework before first use."""
        saved = self._backend.load(self._key)
        self._data = saved if saved is not None else {}
        self._dirty = False
        self._loaded = True

    def save(self) -> None:
        """Persist current state to backend."""
        self._backend.save(self._key, dict(self._data))
        self._dirty = False

    # --- Dirty tracking ----------------------------------------------------

    @property
    def dirty(self) -> bool:
        """True if state has been modified since last load/save."""
        return self._dirty

    def mark_dirty(self) -> None:
        """Explicitly mark the store as dirty.

        Use when mutating nested structures that ``__setitem__``
        can't detect (e.g. ``store["list"].append(x)``).
        """
        self._dirty = True

    # --- Dict-like access (MutableMapping interface) -----------------------

    def _check_loaded(self) -> None:
        """Raise if :meth:`load` has not been called yet."""
        if not self._loaded:
            msg = "DeviceStore.load() must be called before accessing data"
            raise RuntimeError(msg)

    def __getitem__(self, key: str) -> object:
        self._check_loaded()
        return self._data[key]

    def __setitem__(self, key: str, value: object) -> None:
        self._check_loaded()
        self._data[key] = value
        self._dirty = True

    def __delitem__(self, key: str) -> None:
        self._check_loaded()
        del self._data[key]
        self._dirty = True

    def __contains__(self, key: object) -> bool:
        self._check_loaded()
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        self._check_loaded()
        return iter(self._data)

    def __len__(self) -> int:
        self._check_loaded()
        return len(self._data)

    def __repr__(self) -> str:
        return f"DeviceStore(key={self._key!r}, dirty={self._dirty})"

    # --- Convenience methods -----------------------------------------------

    def get(self, key: str, default: object = None) -> object:
        """Return the value for *key*, or *default* if not present."""
        self._check_loaded()
        return self._data.get(key, default)

    def setdefault(self, key: str, default: object = None) -> object:
        """Return ``self[key]`` if present, else set and return *default*."""
        self._check_loaded()
        if key not in self._data:
            self._data[key] = default
            self._dirty = True
        return self._data[key]

    def update(self, other: dict[str, object] | None = None, **kwargs: object) -> None:
        """Update the store from a dict and/or keyword arguments."""
        self._check_loaded()
        if other:
            self._data.update(other)
            self._dirty = True
        if kwargs:
            self._data.update(kwargs)
            self._dirty = True

    def to_dict(self) -> dict[str, object]:
        """Return a shallow copy of the underlying data dict.

        Useful when returning state from a telemetry handler
        (the handler returns this dict for MQTT publishing).
        """
        self._check_loaded()
        return dict(self._data)

    def keys(self) -> KeysView[str]:
        """Return a view of the store's keys."""
        self._check_loaded()
        return self._data.keys()

    def values(self) -> ValuesView[object]:
        """Return a view of the store's values."""
        self._check_loaded()
        return self._data.values()

    def items(self) -> ItemsView[str, object]:
        """Return a view of the store's items."""
        self._check_loaded()
        return self._data.items()
