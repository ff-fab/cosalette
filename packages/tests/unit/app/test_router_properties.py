"""Tests for Router public collection properties.

Test Techniques Used:
    - Specification-based Testing: property contracts and return types
    - Equivalence Partitioning: empty vs populated collections
    - Contract Testing: introspection-surface parity with App
    - Error Guessing: TypeError raised by immutable container types
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from cosalette import Router
from cosalette._context import DeviceContext
from cosalette._runners._stream_types import Stream
from cosalette._wiring._adapter_lifecycle import _AdapterEntry

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.unit


@runtime_checkable
class _StubPort(Protocol):
    """Minimal protocol for adapter property test."""

    def read(self) -> str: ...


class _StubImpl:
    """Concrete adapter for _StubPort."""

    def read(self) -> str:
        return "ok"


class _RouterStreamItem:
    """Module-level type used as Stream item in router introspection tests."""


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
        assert adapters[_StubPort].impl is _StubImpl

    @pytest.mark.parametrize(
        "prop",
        [
            "devices",
            "telemetry_registrations",
            "commands",
            "periodic_registrations",
            "adapters",
        ],
    )
    def test_empty_when_none_registered(self, prop: str) -> None:
        """Each collection property returns an empty container for a fresh Router."""
        router = Router()
        assert len(getattr(router, prop)) == 0

    @pytest.mark.parametrize(
        "attr,key",
        [
            pytest.param("devices", 0, id="devices"),
            pytest.param("telemetry_registrations", 0, id="telemetry"),
            pytest.param("commands", 0, id="commands"),
            pytest.param("periodic_registrations", 0, id="periodic"),
            pytest.param("adapters", object, id="adapters"),
        ],
    )
    def test_collections_are_immutable(
        self, attr: str, key: object, router_with_registrations: Router
    ) -> None:
        """Each returned collection refuses item assignment.

        Technique: Error Guessing — TypeError raised by tuple.__setitem__
        and MappingProxyType.__setitem__.
        """
        coll = getattr(router_with_registrations, attr)
        with pytest.raises(TypeError):
            coll[key] = None  # type: ignore[index]

    @pytest.mark.parametrize(
        "prop,private",
        [
            ("commands", "_commands"),
            ("devices", "_devices"),
            ("telemetry_registrations", "_telemetry"),
            ("periodic_registrations", "_periodic"),
        ],
    )
    def test_public_property_returns_same_objects_as_private_list(
        self, prop: str, private: str, router_with_registrations: Router
    ) -> None:
        """Public property contains the same registration objects as the private list.

        Uses ``is`` (identity) not ``==`` (equality) because the properties wrap the
        private list in a tuple but must not copy or transform the registration objects.
        ``len`` is checked first to guard against silent truncation by ``zip``.
        """
        public = getattr(router_with_registrations, prop)
        private_list = getattr(router_with_registrations, private)  # noqa: SLF001
        assert len(public) == len(private_list), "length mismatch before identity check"
        assert all(a is b for a, b in zip(public, private_list, strict=True))

    def test_adapters_property_reflects_live_state(self) -> None:
        """adapters returns a live MappingProxyType view, not a point-in-time snapshot.

        Unlike the four tuple-based properties, MappingProxyType wraps the live
        underlying dict.  Entries added after obtaining the proxy remain visible.
        """
        router = Router()
        view = router.adapters
        assert _StubPort not in view  # sanity: empty before mutation

        router._adapters[_StubPort] = _AdapterEntry(  # noqa: SLF001
            impl=_StubImpl, dry_run=None
        )

        assert _StubPort in view

    def test_stream_registrations_returns_registered_streams(self) -> None:
        """stream_registrations reflects handlers added via @router.stream.

        Parity test: Router exposes the same stream_registrations property
        as App.
        """
        router = Router()

        @router.stream("readings")
        async def _handle(stream: Stream[_RouterStreamItem]) -> None:
            async for _ in stream:
                pass

        assert len(router.stream_registrations) == 1
        assert router.stream_registrations[0].name == "readings"
        assert router.stream_registrations[0].func is _handle

    def test_stream_registrations_empty_by_default(self) -> None:
        """stream_registrations returns empty tuple on a fresh Router."""
        router = Router()
        assert len(router.stream_registrations) == 0

    def test_stream_registrations_is_snapshot(self) -> None:
        """stream_registrations returns a tuple (immutable snapshot)."""
        router = Router()

        @router.stream("readings")
        async def _handle(stream: Stream[_RouterStreamItem]) -> None:
            async for _ in stream:
                pass

        snapshot = router.stream_registrations
        assert snapshot is not router._streams  # type: ignore[attr-defined]  # noqa: SLF001
        with pytest.raises(TypeError):
            snapshot[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
