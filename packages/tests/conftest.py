"""Pytest configuration and shared fixtures."""

# The cosalette testing plugin is registered via a ``pytest11`` entry
# point (pyproject.toml) for external consumers.  In our own test
# suite we disable it (``-p no:cosalette``) and load explicitly here
# instead, because conftest-based loading is processed during
# ``pytest_load_initial_conftests`` — after ``pytest-cov`` starts
# coverage tracing — so the cosalette import chain is measured.
from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["cosalette.testing._plugin"]


@pytest.fixture(autouse=True)
def _isolate_default_store_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sandbox default store resolution so tests never write to ~/.local/state.

    App(store=<omitted>) now auto-resolves a JsonFileStore under XDG_STATE_HOME;
    point that at a per-test temp dir for hermeticity (see ADR-049).
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
