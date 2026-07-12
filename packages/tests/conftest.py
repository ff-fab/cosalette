"""Pytest configuration and shared fixtures."""

# The cosalette testing plugin is registered via a ``pytest11`` entry
# point (pyproject.toml) for external consumers.  In our own test
# suite we disable it (``-p no:cosalette``) and load explicitly here
# instead, because conftest-based loading is processed during
# ``pytest_load_initial_conftests`` — after ``pytest-cov`` starts
# coverage tracing — so the cosalette import chain is measured.
from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from cosalette import set_default_store_backend

pytest_plugins = ["cosalette.testing._plugin"]


@pytest.fixture(scope="session")
def _xdg_isolation_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped base directory for XDG_STATE_HOME sandboxing.

    One real directory per test session instead of one per test; the
    per-test subdirectory paths are derived (not created) since
    JsonFileStore creates parent directories lazily on first save.
    """
    return tmp_path_factory.mktemp("xdg-isolation")


@pytest.fixture(autouse=True)
def _isolate_default_store_path(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    _xdg_isolation_base: Path,
) -> None:
    """Sandbox default store resolution so tests never write to ~/.local/state.

    App(store=<omitted>) auto-resolves a JsonFileStore under XDG_STATE_HOME.
    Points that at a unique hash-derived subdirectory of a session-scoped base
    — the subdirectory is not pre-created (JsonFileStore creates dirs lazily on
    save), reducing fixture overhead to ~1 mkdir/session (see ADR-049).
    """
    test_hash = hashlib.md5(request.node.nodeid.encode()).hexdigest()[:12]
    monkeypatch.setenv("XDG_STATE_HOME", str(_xdg_isolation_base / test_hash))


@pytest.fixture(autouse=True)
def _reset_default_store_backend() -> Iterator[None]:
    """Reset the global default store backend after each test."""
    yield
    set_default_store_backend(None)


@pytest.fixture(autouse=True)
def _no_container_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default _in_container to False so devcontainer's /.dockerenv is ignored."""
    monkeypatch.setattr("cosalette._app._store_defaults._in_container", lambda: False)
