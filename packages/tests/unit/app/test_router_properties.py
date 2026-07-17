"""Tests for Router public collection properties (cos-kjr).

Test Techniques Used:
    - Specification-based Testing: property contracts and return types
    - Equivalence Partitioning: empty vs populated collections
    - Contract Testing: introspection-surface parity with App
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from cosalette import Router
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
def router_with_registrations() -> Router:
    """Router with one device, telemetry, command, periodic, and one adapter."""
    router = Router(adapters={_StubPort: _StubImpl})

    @router.device("sensor")
    async def _sensor(ctx: DeviceContext) -> AsyncIterator[None]:
        yield

    @router.telemetry("temp", interval=10)
    async def _temp() -> dict[str, object]:
        return {"c": 22}

    @router.command("reset")
    async def _reset() -> None:
        pass

    @router.periodic("heartbeat", interval=30)
    async def _heartbeat() -> None:
        pass

    return router


class TestRouterCollectionProperties:
    """Verify public read-only collection properties on Router (App parity)."""

    def test_devices_returns_registered_devices(
        self, router_with_registrations: Router
    ) -> None:
        assert len(router_with_registrations.devices) == 1
        assert router_with_registrations.devices[0].name == "sensor"

    def test_telemetry_registrations_returns_telemetry(
        self, router_with_registrations: Router
    ) -> None:
        assert len(router_with_registrations.telemetry_registrations) == 1
        assert router_with_registrations.telemetry_registrations[0].name == "temp"

    def test_commands_returns_registered_commands(
        self, router_with_registrations: Router
    ) -> None:
        assert len(router_with_registrations.commands) == 1
        assert router_with_registrations.commands[0].name == "reset"

    def test_periodic_registrations_returns_periodic(
        self, router_with_registrations: Router
    ) -> None:
        assert len(router_with_registrations.periodic_registrations) == 1
        assert router_with_registrations.periodic_registrations[0].name == "heartbeat"

    def test_adapters_returns_registered_adapters(
        self, router_with_registrations: Router
    ) -> None:
        adapters = router_with_registrations.adapters
        assert _StubPort in adapters

    def test_devices_empty_when_none_registered(self) -> None:
        router = Router()
        assert len(router.devices) == 0

    def test_telemetry_empty_when_none_registered(self) -> None:
        router = Router()
        assert len(router.telemetry_registrations) == 0

    def test_commands_empty_when_none_registered(self) -> None:
        router = Router()
        assert len(router.commands) == 0

    def test_periodic_empty_when_none_registered(self) -> None:
        router = Router()
        assert len(router.periodic_registrations) == 0

    def test_adapters_empty_when_none_registered(self) -> None:
        router = Router()
        assert len(router.adapters) == 0

    def test_collections_are_immutable(self, router_with_registrations: Router) -> None:
        """Returned collections cannot mutate Router internals."""
        devices: Sequence = router_with_registrations.devices
        telemetry: Sequence = router_with_registrations.telemetry_registrations
        commands: Sequence = router_with_registrations.commands
        periodic: Sequence = router_with_registrations.periodic_registrations
        adapters: Mapping = router_with_registrations.adapters
        # tuple and MappingProxyType don't support mutation
        with pytest.raises(TypeError):
            devices[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            telemetry[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            commands[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            periodic[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            adapters[object] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]

    def test_public_property_matches_private_list(
        self, router_with_registrations: Router
    ) -> None:
        """Public commands property returns the same objects as the private list."""
        assert tuple(router_with_registrations.commands) == tuple(
            router_with_registrations._commands  # noqa: SLF001
        )
