"""Tests for cosalette._app — App orchestrator (core property tests).

Covers: heartbeat interval validation, settings property behaviour,
and lifespan registration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from cosalette._app import App
from cosalette._context import AppContext
from cosalette._registration import _noop_lifespan
from cosalette._settings import Settings

pytestmark = pytest.mark.unit

# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin

# ---------------------------------------------------------------------------
# TestHeartbeatIntervalValidation
# ---------------------------------------------------------------------------


class TestHeartbeatIntervalValidation:
    """heartbeat_interval parameter validation.

    Technique: Boundary Testing — verifying that non-positive values
    are rejected at construction time (fail-fast).
    """

    def test_rejects_zero_interval(self) -> None:
        """Zero interval would create a busy-loop and is rejected."""
        with pytest.raises(ValueError, match="positive"):
            App(name="x", heartbeat_interval=0)

    def test_rejects_negative_interval(self) -> None:
        """Negative intervals are nonsensical and rejected."""
        with pytest.raises(ValueError, match="positive"):
            App(name="x", heartbeat_interval=-1.0)

    def test_accepts_positive_interval(self) -> None:
        """Positive values are accepted without error."""
        app = App(name="x", heartbeat_interval=30.0)
        assert app._heartbeat_interval == 30.0  # noqa: SLF001

    def test_accepts_none_interval(self) -> None:
        """None disables heartbeats — no error raised."""
        app = App(name="x", heartbeat_interval=None)
        assert app._heartbeat_interval is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# TestSettingsProperty
# ---------------------------------------------------------------------------


class TestSettingsProperty:
    """app.settings property tests.

    Technique: Specification-based Testing — verifying eager
    instantiation at construction time.
    """

    def test_settings_available_after_construction(self) -> None:
        """app.settings returns a Settings instance immediately."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        assert isinstance(app.settings, Settings)

    def test_settings_reflects_settings_class(self) -> None:
        """app.settings is an instance of the provided settings_class."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        assert isinstance(app.settings, _IsolatedSettings)

    def test_settings_usable_in_decorator_args(self) -> None:
        """Settings values can be used as decorator arguments."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        # Use a settings value as the interval — this is the primary use case
        interval = app.settings.mqtt.reconnect_interval

        @app.telemetry("sensor", interval=interval)
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        assert app._telemetry[0].interval == app.settings.mqtt.reconnect_interval

    def test_settings_none_when_validation_fails(self) -> None:
        """Construction succeeds when settings_class has missing required fields.

        The ValidationError is caught and deferred — app._settings
        stores None instead of crashing. This supports --env-file
        workflows where required fields are only in a CLI-specified file.
        """
        from pydantic_settings import BaseSettings

        class NeedsField(BaseSettings):
            required_field: str  # no default → validation error

        app = App(
            name="testapp",
            version="0.0.1",
            settings_class=NeedsField,  # type: ignore[arg-type]
        )
        assert app._settings is None  # noqa: SLF001

    def test_settings_property_raises_when_none(self) -> None:
        """app.settings raises RuntimeError with guidance when deferred."""
        from pydantic_settings import BaseSettings

        class NeedsField(BaseSettings):
            required_field: str

        app = App(
            name="testapp",
            version="0.0.1",
            settings_class=NeedsField,  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError, match="could not be instantiated"):
            _ = app.settings


# ---------------------------------------------------------------------------
# TestLifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    """Lifespan context manager registration tests.

    Technique: Specification-based Testing — verifying that
    ``App(lifespan=...)`` stores a custom lifespan, and that the
    default is the no-op lifespan.
    """

    async def test_default_lifespan_is_noop(self, app: App) -> None:
        """When no lifespan is provided, the no-op default is used."""
        assert app._lifespan is _noop_lifespan  # noqa: SLF001

    async def test_custom_lifespan_stored(self) -> None:
        """A custom lifespan function is stored on the App."""

        @asynccontextmanager
        async def my_lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield

        app = App(name="testapp", version="1.0.0", lifespan=my_lifespan)
        assert app._lifespan is my_lifespan  # noqa: SLF001
