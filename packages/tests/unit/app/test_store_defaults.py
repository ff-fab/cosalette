"""Tests for default store path resolution and App integration (ADR-049).

Covers the resolver function _resolve_default_store_path and the
App(store=<omitted>) integration behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosalette._app import App
from cosalette._app._store_defaults import _resolve_default_store_path
from cosalette._persistence._persist import SaveOnPublish
from cosalette._persistence._stores import JsonFileStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestResolveDefaultStorePath
# ---------------------------------------------------------------------------


class TestResolveDefaultStorePath:
    """Unit tests for the _resolve_default_store_path resolver.

    Technique: Specification-based Testing — verifying the three-level
    precedence chain via monkeypatching environment variables.
    """

    def test_env_override_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """APP_STORE_PATH env var takes highest precedence."""
        monkeypatch.setenv("TESTAPP_STORE_PATH", str(tmp_path / "s.json"))
        assert _resolve_default_store_path("testapp") == tmp_path / "s.json"

    def test_xdg_state_home_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """XDG_STATE_HOME is used when env override is absent."""
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert (
            _resolve_default_store_path("testapp")
            == tmp_path / "testapp" / "store.json"
        )

    def test_home_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Falls back to ~/.local/state/<name>/store.json when XDG_STATE_HOME unset."""
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _resolve_default_store_path("testapp") == (
            tmp_path / ".local" / "state" / "testapp" / "store.json"
        )

    def test_env_var_name_normalization(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Hyphens and spaces in app name are mapped to underscores in env var name."""
        # env var: "CAL_DATES_2_STORE_PATH" (upper + hyphen/space -> underscore)
        monkeypatch.setenv("CAL_DATES_2_STORE_PATH", str(tmp_path / "x.json"))
        assert _resolve_default_store_path("cal-dates 2") == tmp_path / "x.json"

    def test_xdg_path_segment_uses_raw_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The XDG path segment preserves the raw app name (not normalized)."""
        monkeypatch.delenv("CAL_DATES_2_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = _resolve_default_store_path("cal-dates 2")
        assert result == tmp_path / "cal-dates 2" / "store.json"


# ---------------------------------------------------------------------------
# TestAppDefaultStoreIntegration
# ---------------------------------------------------------------------------


class TestAppDefaultStoreIntegration:
    """Integration tests: App(store=<omitted>) behaviour.

    Technique: Specification-based Testing — verifying the sentinel
    branch triggers auto-creation of a JsonFileStore.
    """

    def test_omitted_store_creates_json_file_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """App() with store omitted auto-creates a JsonFileStore at the default path."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        app = App(name="x")
        assert isinstance(app._store, JsonFileStore)  # noqa: SLF001
        assert app._store._path == tmp_path / "x" / "store.json"  # noqa: SLF001
        assert app._store_configured is True  # noqa: SLF001

    def test_explicit_none_disables_store(self) -> None:
        """App(store=None) opts out — _store is None and _store_configured is False."""
        app = App(name="x", store=None)
        assert app._store is None  # noqa: SLF001
        assert app._store_configured is False  # noqa: SLF001

    def test_default_store_satisfies_persist_requirement(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Registering persist=SaveOnPublish() with store omitted does not raise.

        Technique: Error Guessing — the default store satisfies _store_configured,
        so the persist requirement check should pass without explicit store= arg.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        app = App(name="x")

        # Should not raise — default store satisfies the persist requirement
        @app.telemetry("sensor", interval=10.0, persist=SaveOnPublish())
        async def sensor() -> dict[str, object]:
            return {}
