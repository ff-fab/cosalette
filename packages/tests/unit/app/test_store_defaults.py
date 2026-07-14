"""Tests for default store path resolution and App integration (ADR-049).

Covers the resolver function _resolve_default_store_path and the
App(store=<omitted>) integration behavior.

Test Techniques Used:
- Specification-based Testing: verifying the three-level precedence chain
- Boundary Value Analysis: empty/relative XDG_STATE_HOME, empty env override
- Error Guessing: path-traversal names, persist= with store=None, default
  store satisfies persist=
- Branch/Condition Coverage: _has_dynamic_entity_set predicate branches
  (TestRetainedCleanupMayApply)
- State Verification: MemoryStore snapshot key presence after bootstrap
  (TestCleanupStoreGate)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cosalette._app import App
from cosalette._app._store_defaults import (
    _default_store_is_ephemeral,
    _normalize_env_name,
    _resolve_default_store_path,
    set_default_store_backend,
)
from cosalette._context import DeviceContext
from cosalette._persistence._persist import SaveOnPublish
from cosalette._persistence._stores import JsonFileStore, MemoryStore, SqliteStore
from cosalette._runners._stream_types import Stream
from cosalette.testing import MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


async def _run_app_with_shutdown(app: App, mock_mqtt: MockMqttClient) -> None:
    """Bootstrap an app then immediately shut down (for gate/warning tests)."""
    shutdown = asyncio.Event()
    shutdown.set()
    await asyncio.wait_for(
        app._run_async(  # noqa: SLF001
            settings=make_settings(),
            shutdown_event=shutdown,
            mqtt=mock_mqtt,
        ),
        timeout=5.0,
    )


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

    def test_leading_digit_prefixed(self) -> None:
        """Names starting with a digit get a '_' prefix for POSIX shell safety."""
        assert _normalize_env_name("1sensor") == "_1SENSOR"


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

    def _arrange_ephemeral_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Set up monkeypatches for a default-store app in an ephemeral container.

        Sets XDG_STATE_HOME to tmp_path, clears TESTAPP_STORE_PATH, patches
        _in_container to return True, and stubs configure_logging so caplog
        works.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setattr(_CONTAINER_TARGET, lambda: True)
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )

    async def test_warning_emitted_exactly_once_for_dynamic_default_store_in_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Warning is logged when default store is auto-resolved inside a container.

        The app must have a dynamic registration so that retained-topic cleanup
        could actually have something to clean across restarts.
        """
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(name=lambda s: ["sensor"], interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        ephemeral_warnings = [
            r
            for r in caplog.records
            if "ephemeral" in r.message and r.levelno == logging.WARNING
        ]
        assert len(ephemeral_warnings) == 1

    async def test_no_warning_when_not_in_container(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning when not running inside a container.

        The autouse _no_container_by_default fixture ensures
        _in_container() returns False.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        monkeypatch.setattr(
            "cosalette._app._lifecycle.configure_logging", lambda *a, **k: None
        )
        app = App(name="testapp", version="1.0.0")
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert not any(
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
            await _run_app_with_shutdown(app, mock_mqtt)

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
            await _run_app_with_shutdown(app, mock_mqtt)

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

        @app.on_configure
        def _setup() -> None: ...

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)

    # --- 3b: scoping tests — warning fires vs. suppressed ---

    async def test_warning_fires_for_dynamic_name_telemetry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Warning fires when telemetry uses a dynamic name= callable."""
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(name=lambda s: ["sensor"], interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert any("ephemeral" in r.message for r in caplog.records)

    async def test_warning_fires_for_callable_enabled_telemetry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Warning fires when telemetry has a callable enabled= spec."""
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sun", interval=60.0, enabled=lambda s: True)
        async def _sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert any("ephemeral" in r.message for r in caplog.records)

    async def test_warning_fires_for_on_configure_hook(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Warning fires when app has an @app.on_configure hook (no other regs)."""
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.on_configure
        def _setup() -> None: ...

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert any("ephemeral" in r.message for r in caplog.records)

    async def test_no_warning_for_static_telemetry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning for an app with a single static-name telemetry (suncast case).

        In-container + default store + no STORE_PATH override: the ONLY reason
        the warning is suppressed is the new predicate (_has_dynamic_entity_set
        returns False), proving the gate.
        """
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sun", interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)

    async def test_no_warning_for_static_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning for an app with only a static command (wallpanel-control case).

        In-container + default store + no STORE_PATH override: the ONLY reason
        the warning is suppressed is _has_dynamic_entity_set returning False.
        """
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")

        @app.command("lights")
        async def _lights(topic: str, payload: str) -> None: ...

        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)

    async def test_no_warning_for_bare_app(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """No warning for a bare app with zero registrations.

        In-container + default store + no STORE_PATH override: the ONLY reason
        the warning is suppressed is _has_dynamic_entity_set returning False.
        """
        self._arrange_ephemeral_container(monkeypatch, tmp_path)
        app = App(name="testapp", version="1.0.0")
        with caplog.at_level(logging.WARNING, logger="cosalette._app._lifecycle"):
            await _run_app_with_shutdown(app, mock_mqtt)

        assert not any("ephemeral" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestRetainedCleanupMayApply
# ---------------------------------------------------------------------------


class TestRetainedCleanupMayApply:
    """Fast unit tests for App._has_dynamic_entity_set().

    Technique: Branch Coverage — each predicate branch exercised directly
    without running the async lifecycle.
    """

    def test_bare_app_returns_false(self) -> None:
        """A bare app (no registrations, no hooks) returns False."""
        app = App(name="x")
        assert app._has_dynamic_entity_set() is False  # noqa: SLF001

    def test_static_telemetry_returns_false(self) -> None:
        """Static-name telemetry with literal enabled= returns False."""
        app = App(name="x")

        @app.telemetry("sun", interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        assert app._has_dynamic_entity_set() is False  # noqa: SLF001

    def test_static_command_returns_false(self) -> None:
        """Static-name command with literal enabled= returns False."""
        app = App(name="x")

        @app.command("lights")
        async def _lights(topic: str, payload: str) -> None: ...

        assert app._has_dynamic_entity_set() is False  # noqa: SLF001

    def test_dynamic_name_callable_returns_true(self) -> None:
        """A dynamic name= callable on any registration returns True."""
        app = App(name="x")

        @app.telemetry(name=lambda s: ["sensor"], interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_callable_enabled_returns_true(self) -> None:
        """A callable enabled= on any registration returns True."""
        app = App(name="x")

        @app.telemetry("sun", interval=60.0, enabled=lambda s: True)
        async def _sensor() -> dict[str, object]:
            return {}

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_on_configure_hook_returns_true(self) -> None:
        """An @app.on_configure hook makes the app dynamic regardless of regs."""
        app = App(name="x")

        @app.on_configure
        def _setup() -> None: ...

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_static_device_returns_false(self) -> None:
        """Static-name device returns False — no dynamic features."""
        app = App(name="x")

        @app.device("pump")
        async def _pump(ctx: DeviceContext) -> AsyncIterator[dict[str, object]]:
            yield {}

        assert app._has_dynamic_entity_set() is False  # noqa: SLF001

    def test_dynamic_device_name_callable_returns_true(self) -> None:
        """A dynamic name= callable on a device registration returns True."""
        app = App(name="x")

        @app.device(name=lambda s: ["pump"])
        async def _pump(ctx: DeviceContext) -> AsyncIterator[dict[str, object]]:
            yield {}

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_callable_enabled_command_returns_true(self) -> None:
        """A callable enabled= on a command registration returns True."""
        app = App(name="x")

        @app.command("lights", enabled=lambda s: True)
        async def _lights(topic: str, payload: str) -> None: ...

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_mixed_static_and_dynamic_returns_true(self) -> None:
        """One static + one dynamic registration: predicate returns True.

        Confirms any() short-circuit: a single dynamic entry flips the result
        even when other registrations are static.
        """
        app = App(name="x")

        @app.telemetry("sun", interval=60.0)
        async def _static() -> dict[str, object]:
            return {}

        @app.telemetry(name=lambda s: ["extra"], interval=60.0)
        async def _dynamic() -> dict[str, object]:
            return {}

        assert app._has_dynamic_entity_set() is True  # noqa: SLF001

    def test_periodic_only_returns_false(self) -> None:
        """App with only @app.periodic registration returns False.

        Periodic tasks carry no config-removable retained topics (ADR-048),
        so they never make the entity set dynamic.
        """
        app = App(name="x")

        @app.periodic("cache-refresh", interval=60.0)
        async def _refresh() -> None: ...

        assert app._has_dynamic_entity_set() is False  # noqa: SLF001

    def test_stream_only_returns_false(self) -> None:
        """App with only @app.stream registration returns False.

        Stream handlers carry no config-removable retained topics (ADR-048),
        so they never make the entity set dynamic.
        """
        app = App(name="x")

        @app.stream("readings")
        async def _handle(stream: Stream[dict]) -> None:  # type: ignore[type-arg]
            async for _ in stream:  # type: ignore[attr-defined]
                pass

        assert app._has_dynamic_entity_set() is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# TestCleanupStoreGate
# ---------------------------------------------------------------------------


class TestCleanupStoreGate:
    """Tests for ADR-049 Option B: cleanup-store gate.

    Verifies that ADR-048 snapshot I/O (store.save) is skipped for static apps
    and kept for dynamic apps, while persist= usage is unaffected.

    Technique: State verification via MemoryStore — inject a MemoryStore as
    an explicit store= argument to observe whether ADR-048 snapshot I/O fires.
    """

    async def test_cleanup_store_skips_snapshot_write_for_static_app(
        self, mock_mqtt: MockMqttClient
    ) -> None:
        """Static app with auto-default store: snapshot not written (Option B gate)."""
        set_default_store_backend(lambda _path: MemoryStore())
        app = App(name="testapp", version="1.0.0")
        store = app._store  # noqa: SLF001
        assert isinstance(store, MemoryStore)

        @app.telemetry("sun", interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        await _run_app_with_shutdown(app, mock_mqtt)

        # No snapshot key should be written — cleanup store was None for this
        # static app with auto-default store (ADR-049 Option B gate).
        snapshot = store.load("__cosalette_entity_snapshot__testapp")
        assert snapshot is None, f"Expected no snapshot in store, but found: {snapshot}"

    async def test_cleanup_store_writes_snapshot_for_callable_name(
        self, mock_mqtt: MockMqttClient
    ) -> None:
        """Dynamic app (callable name=): ADR-048 snapshot IS written to store."""
        store = MemoryStore()
        app = App(name="testapp", version="1.0.0", store=store)

        @app.telemetry(name=lambda s: ["sensor"], interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        await _run_app_with_shutdown(app, mock_mqtt)

        snapshot = store.load("__cosalette_entity_snapshot__testapp")
        assert snapshot is not None and "schema_version" in snapshot, (
            f"Expected snapshot in store, but found: {snapshot}"
        )

    async def test_cleanup_store_writes_snapshot_for_on_configure_hook(
        self, mock_mqtt: MockMqttClient
    ) -> None:
        """App with on_configure hook: snapshot IS written (conservative)."""
        store = MemoryStore()
        app = App(name="testapp", version="1.0.0", store=store)

        @app.on_configure
        def _setup() -> None: ...

        await _run_app_with_shutdown(app, mock_mqtt)

        snapshot = store.load("__cosalette_entity_snapshot__testapp")
        assert snapshot is not None and "schema_version" in snapshot, (
            f"Expected snapshot in store, but found: {snapshot}"
        )

    async def test_cleanup_store_unaffected_when_store_is_none(
        self, mock_mqtt: MockMqttClient
    ) -> None:
        """store=None is never changed by the gate — cleanup remains no-op."""
        app = App(name="testapp", version="1.0.0", store=None)

        @app.on_configure
        def _setup() -> None: ...

        # Must not raise — store=None with a configure hook is valid.
        await _run_app_with_shutdown(app, mock_mqtt)

        assert app._store is None  # noqa: SLF001

    async def test_cleanup_store_skips_file_creation_for_static_default_store(
        self,
        mock_mqtt: MockMqttClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Static app with auto-default JsonFileStore: no store.json file created."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.delenv("TESTAPP_STORE_PATH", raising=False)
        # _no_container_by_default fixture ensures _in_container() -> False
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sun", interval=60.0)
        async def _sensor() -> dict[str, object]:
            return {}

        await _run_app_with_shutdown(app, mock_mqtt)

        store_path = tmp_path / "testapp" / "store.json"
        assert not store_path.exists(), (
            f"Expected no store.json at {store_path}, but file was created"
        )

    async def test_cleanup_store_writes_snapshot_for_callable_enabled(
        self, mock_mqtt: MockMqttClient
    ) -> None:
        """Dynamic app (callable enabled=): ADR-048 snapshot IS written to store."""
        store = MemoryStore()
        app = App(name="testapp", version="1.0.0", store=store)

        @app.telemetry("sun", interval=60.0, enabled=lambda s: True)
        async def _sensor() -> dict[str, object]:
            return {}

        await _run_app_with_shutdown(app, mock_mqtt)

        snapshot = store.load("__cosalette_entity_snapshot__testapp")
        assert snapshot is not None and "schema_version" in snapshot, (
            f"Expected snapshot in store, but found: {snapshot}"
        )
