"""Tests for App public collection properties (cos-lt1).

Test Techniques Used:
    - Specification-based Testing: property contracts and return types
    - Equivalence Partitioning: empty vs populated collections
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = pytest.mark.unit


@runtime_checkable
class _StubPort(Protocol):
    """Minimal protocol for adapter property test."""

    def read(self) -> str: ...


class _StubImpl:
    """Concrete adapter for _StubPort."""

    def read(self) -> str:
        return "ok"


@pytest.fixture
def app_with_registrations() -> App:
    """App with one device, one telemetry, one command, and one adapter."""
    app = App(
        name="test",
        version="0.1.0",
        adapters={_StubPort: _StubImpl()},  # ty: ignore[invalid-argument-type]
    )

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

    def test_adapters_returns_registered_adapters(
        self, app_with_registrations: App
    ) -> None:
        adapters = app_with_registrations.adapters
        assert _StubPort in adapters

    def test_devices_empty_when_none_registered(self) -> None:
        app = App(name="bare", version="0.1.0")
        assert len(app.devices) == 0

    def test_telemetry_empty_when_none_registered(self) -> None:
        app = App(name="bare", version="0.1.0")
        assert len(app.telemetry_registrations) == 0

    def test_commands_empty_when_none_registered(self) -> None:
        app = App(name="bare", version="0.1.0")
        assert len(app.commands) == 0

    def test_adapters_empty_when_none_registered(self) -> None:
        app = App(name="bare", version="0.1.0")
        assert len(app.adapters) == 0

    def test_collections_are_immutable(self, app_with_registrations: App) -> None:
        """Returned collections cannot mutate App internals."""
        devices: Sequence = app_with_registrations.devices
        telemetry: Sequence = app_with_registrations.telemetry_registrations
        commands: Sequence = app_with_registrations.commands
        adapters: Mapping = app_with_registrations.adapters
        # tuple and MappingProxyType don't support mutation
        with pytest.raises(TypeError):
            devices[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            telemetry[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            commands[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            adapters[object] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
