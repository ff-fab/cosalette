"""Tests for default store path resolution and App integration (ADR-049).

Covers the resolver function _resolve_default_store_path and the
App(store=<omitted>) integration behavior.

Test Techniques Used:
- Specification-based Testing: verifying the three-level precedence chain
- Boundary Value Analysis: empty/relative XDG_STATE_HOME, empty env override
- Error Guessing: path-traversal names, persist= with store=None, default
  store satisfies persist=
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from cosalette._app import App
from cosalette._app._store_defaults import (
    _default_store_is_ephemeral,
    _normalize_env_name,
    _resolve_default_store_path,
    set_default_store_backend,
)
from cosalette._persistence._persist import SaveOnPublish
from cosalette._persistence._stores import JsonFileStore, SqliteStore
from cosalette.testing import MockMqttClient, make_settings

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
        # The autouse _isolate_default_store_path fixture sets XDG_STATE_HOME;
        # clear it here to exercise the ~/.local/state fallback path.
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

    def test_env_var_name_normalization_dot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Dots in app name are mapped to underscores in env var name."""
        # env var: "SENSOR_HUB_STORE_PATH" (upper + dot -> underscore)
        monkeypatch.setenv("SENSOR_HUB_STORE_PATH", str(tmp_path / "hub.json"))
        assert _resolve_default_store_path("sensor.hub") == tmp_path / "hub.json"

    def test_xdg_path_segment_uses_raw_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The XDG path segment preserves the raw app name (not normalized)."""
        monkeypatch.delenv("CAL_DATES_2_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = _resolve_default_store_path("cal-dates 2")
        assert result == tmp_path / "cal-dates 2" / "store.json"

    def test_empty_xdg_falls_back_to_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty XDG_STATE_HOME is treated as unset (per XDG spec, must be absolute)."""
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", "")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _resolve_default_store_path("testapp") == (
            tmp_path / ".local" / "state" / "testapp" / "store.json"
        )

    def test_relative_xdg_falls_back_to_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Relative XDG_STATE_HOME is ignored (per XDG spec, must be absolute)."""
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", "relative/path")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert _resolve_default_store_path("testapp") == (
            tmp_path / ".local" / "state" / "testapp" / "store.json"
        )

    def test_empty_env_override_falls_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty <APP>_STORE_PATH is treated as unset (falls through to XDG/home)."""
        monkeypatch.setenv("TESTAPP_STORE_PATH", "")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert _resolve_default_store_path("testapp") == (
            tmp_path / "testapp" / "store.json"
        )

    def test_dotdot_name_raises(self) -> None:
        """App name '..' raises ValueError.

        Path traversal would escape the base directory.
        """
        with pytest.raises(ValueError, match="path-traversal"):
            _resolve_default_store_path("..")

    def test_dot_name_raises(self) -> None:
        """App name '.' raises ValueError — ambiguous path segment is rejected."""
        with pytest.raises(ValueError, match="path-traversal"):
            _resolve_default_store_path(".")


# ---------------------------------------------------------------------------
# TestNormalizeEnvName
# ---------------------------------------------------------------------------


class TestNormalizeEnvName:
    """Unit tests for _normalize_env_name."""

    def test_hyphen_replaced(self) -> None:
        """Hyphens become underscores."""
        assert _normalize_env_name("my-app") == "MY_APP"

    def test_space_replaced(self) -> None:
        """Spaces become underscores."""
        assert _normalize_env_name("my app") == "MY_APP"

    def test_dot_replaced(self) -> None:
        """Dots become underscores."""
        assert _normalize_env_name("sensor.hub") == "SENSOR_HUB"

    def test_alphanumeric_unchanged(self) -> None:
        """Alphanumeric characters and underscores are kept as-is."""
        assert _normalize_env_name("MY_APP_2") == "MY_APP_2"

    def test_multiple_special_chars(self) -> None:
        """Multiple special characters are all replaced."""
        assert _normalize_env_name("a.b-c d") == "A_B_C_D"


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

    def test_persist_with_explicit_none_store_raises(self) -> None:
        """persist= with store=None raises ValueError (matches ADR-049 + help)."""
        app = App(name="x", store=None)
        with pytest.raises(ValueError, match="persist.*requires.*store"):

            @app.telemetry("sensor", interval=10.0, persist=SaveOnPublish())
            async def sensor() -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# TestSetDefaultStoreBackend
# ---------------------------------------------------------------------------


class TestSetDefaultStoreBackend:
    """Tests for the process-wide configurable default store backend.

    Technique: Specification-based Testing — verify factory override and reset.
    Global state is restored by the autouse _reset_default_store_backend fixture.
    """

    def test_sqlite_backend_creates_sqlite_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Setting SqliteStore backend makes App() produce a SqliteStore."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        set_default_store_backend(SqliteStore)
        app = App(name="myapp")
        assert isinstance(app._store, SqliteStore)  # noqa: SLF001
        app._store.close()  # noqa: SLF001  # prevent unraisable on GC

    def test_reset_to_none_restores_json_file_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Resetting via set_default_store_backend(None) restores JsonFileStore."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        set_default_store_backend(SqliteStore)
        app_sqlite = App(name="myapp")
        assert isinstance(app_sqlite._store, SqliteStore)  # noqa: SLF001
        app_sqlite._store.close()  # noqa: SLF001
        set_default_store_backend(None)
        app = App(name="myapp")
        assert isinstance(app._store, JsonFileStore)  # noqa: SLF001

    def test_explicit_store_unaffected_by_backend(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Explicit store= argument bypasses the configured default backend."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        set_default_store_backend(SqliteStore)
        explicit = JsonFileStore(tmp_path / "explicit.json")
        app = App(name="myapp", store=explicit)
        assert app._store is explicit  # noqa: SLF001

    def test_store_is_default_flag_set_for_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_store_is_default is True when store= is omitted."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        app = App(name="myapp")
        assert app._store_is_default is True  # noqa: SLF001

    def test_store_is_default_false_for_explicit_none(self) -> None:
        """_store_is_default is False when store=None."""
        app = App(name="myapp", store=None)
        assert app._store_is_default is False  # noqa: SLF001

    def test_store_is_default_false_for_explicit_store(self, tmp_path: Path) -> None:
        """_store_is_default is False when an explicit Store is passed."""
        app = App(name="myapp", store=JsonFileStore(tmp_path / "x.json"))
        assert app._store_is_default is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# TestInContainerAndEphemeral
# ---------------------------------------------------------------------------

_CONTAINER_TARGET = "cosalette._app._store_defaults._in_container"


class TestInContainerAndEphemeral:
    """Tests for _default_store_is_ephemeral.

    Technique: Equivalence Partitioning — env override present vs absent,
    container detected vs not detected.

    Note: The autouse _no_container_by_default fixture patches _in_container
    to return False for all tests. Individual tests override via monkeypatch.
    """

    def test_ephemeral_false_when_env_override_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When <NAME>_STORE_PATH is set, store is durable regardless of container."""
        monkeypatch.setenv("MYAPP_STORE_PATH", str(tmp_path / "store.json"))
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        assert _default_store_is_ephemeral("myapp") is False

    def test_ephemeral_false_when_not_in_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When not in a container, default store is not ephemeral."""
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        # _no_container_by_default fixture ensures _in_container() -> False
        assert _default_store_is_ephemeral("testapp") is False

    def test_ephemeral_true_in_container_without_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Container + no env override -> ephemeral."""
        monkeypatch.delenv("MYAPP_STORE_PATH", raising=False)
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        assert _default_store_is_ephemeral("myapp") is True


# ---------------------------------------------------------------------------
# TestEphemeralWarning
# ---------------------------------------------------------------------------


class TestEphemeralWarning:
    """Tests for the ephemeral default-store startup warning.

    Technique: Lightweight bootstrap testing — _run_async with MockMqttClient
    and an immediate shutdown event, capturing log output via caplog.
    """

    async def _run_with_shutdown(self, app: App, mock_mqtt: MockMqttClient) -> None:
        """Helper: run app to bootstrap then immediately shut down."""
        shutdown = asyncio.Event()
        shutdown.set()
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
            ),
            timeout=5.0,
        )

    async def test_warning_emitted_for_default_store_in_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Warning is logged when default store is auto-resolved inside a container."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        # configure_logging replaces root logger handlers, removing caplog's handler.
        # Stub it out to preserve caplog's handler so records are captured.
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )
        app = App(name="testapp", version="1.0.0")
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await self._run_with_shutdown(app, mock_mqtt)

        assert any(
            "ephemeral" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        )

    async def test_no_warning_when_store_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No warning when store=None (user opted out of persistence)."""
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )
        app = App(name="testapp", version="1.0.0", store=None)
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await self._run_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)

    async def test_no_warning_with_explicit_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning when an explicit Store is passed."""
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )
        app = App(
            name="testapp",
            version="1.0.0",
            store=JsonFileStore(tmp_path / "x.json"),
        )
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await self._run_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)

    async def test_no_warning_when_store_path_env_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning when <NAME>_STORE_PATH is configured (durable path)."""
        monkeypatch.setenv("TESTAPP_STORE_PATH", str(tmp_path / "s.json"))
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )
        app = App(name="testapp", version="1.0.0")
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await self._run_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)
