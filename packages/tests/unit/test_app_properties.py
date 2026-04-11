"""Tests for App public collection properties (cos-lt1)."""

from __future__ import annotations

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext


@pytest.fixture
def app_with_registrations() -> App:
    """App with one device, one telemetry, and one command registered."""
    app = App(name="test", version="0.1.0")

    @app.device("sensor")
    async def _sensor(ctx: DeviceContext) -> None:
        pass

    @app.telemetry("temp", interval=10)
    async def _temp() -> dict[str, object]:
        return {"c": 22}

    @app.command("reset")
    async def _reset(ctx: DeviceContext) -> None:
        pass

    return app


class TestAppCollectionProperties:
    """Verify public read-only collection properties on App."""

    def test_devices_returns_registered_devices(
        self, app_with_registrations: App
    ) -> None:
        assert len(app_with_registrations.devices) == 1
        assert app_with_registrations.devices[0].name == "sensor"

    def test_telemetry_registrations_returns_telemetry(
        self, app_with_registrations: App
    ) -> None:
        assert len(app_with_registrations.telemetry_registrations) == 1
        assert app_with_registrations.telemetry_registrations[0].name == "temp"

    def test_commands_returns_registered_commands(
        self, app_with_registrations: App
    ) -> None:
        assert len(app_with_registrations.commands) == 1
        assert app_with_registrations.commands[0].name == "reset"

    def test_adapters_returns_empty_when_none(
        self, app_with_registrations: App
    ) -> None:
        assert app_with_registrations.adapters == {}
