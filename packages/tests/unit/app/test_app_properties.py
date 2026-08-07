"""Tests for App public collection properties (cos-lt1).

Test Techniques Used:
    - Specification-based Testing: property contracts and return types
    - Equivalence Partitioning: empty vs populated collections
    - State Verification: store configuration reflected in public accessors
    - Specification-based Testing: registration type alias identity
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._runners._stream_types import Stream

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


class _StreamItem:
    """Module-level type used as Stream item in introspection tests."""


class _StateCounter:
    """Module-level type used as @app.state return type in introspection tests."""


@pytest.fixture
def app_with_registrations() -> App:
    """App with one device, telemetry, command, periodic, and one adapter."""
    app = App(
        name="test",
        version="0.1.0",
        store=None,
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

    @app.periodic("heartbeat", interval=30)
    async def _heartbeat() -> None:
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
        devices: Sequence[object] = app_with_registrations.devices
        telemetry: Sequence[object] = app_with_registrations.telemetry_registrations
        commands: Sequence[object] = app_with_registrations.commands
        adapters: Mapping[type, object] = app_with_registrations.adapters
        # tuple and MappingProxyType don't support mutation
        with pytest.raises(TypeError):
            devices[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            telemetry[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            commands[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
            adapters[object] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]


class TestAppStoreProperties:
    """Verify public store-related properties on App.

    Technique: Specification-based Testing — property contracts for store
    accessor and store_is_default flag.
    """

    def test_store_returns_concrete_instance_when_explicit(self) -> None:
        """app.store returns the explicitly-passed Store instance."""
        from cosalette import MemoryStore

        store = MemoryStore()
        app = App(name="test", store=store)
        assert app.store is store

    def test_store_returns_none_when_opted_out(self) -> None:
        """app.store is None when store=None was passed."""
        app = App(name="test", store=None)
        assert app.store is None

    def test_store_returns_default_store_when_omitted(
        self, monkeypatch, tmp_path
    ) -> None:
        """app.store is a non-None JsonFileStore when store= is omitted."""
        from cosalette import JsonFileStore

        monkeypatch.delenv("TEST_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        app = App(name="test")
        assert app.store is not None
        assert isinstance(app.store, JsonFileStore)

    def test_store_is_default_true_when_omitted(self, monkeypatch, tmp_path) -> None:
        """app.store_is_default is True when store= was omitted."""
        monkeypatch.delenv("TEST_STORE_PATH", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        app = App(name="test")
        assert app.store_is_default is True

    def test_store_is_default_false_when_explicit(self) -> None:
        """app.store_is_default is False when an explicit Store was passed."""
        from cosalette import MemoryStore

        app = App(name="test", store=MemoryStore())
        assert app.store_is_default is False

    def test_store_is_default_false_when_opted_out(self) -> None:
        """app.store_is_default is False when store=None was passed."""
        app = App(name="test", store=None)
        assert app.store_is_default is False

    def test_store_returns_none_before_bootstrap_when_factory(self) -> None:
        """app.store is None when store=<callable> is passed before bootstrap."""
        from cosalette import MemoryStore

        app = App(name="test", store=lambda: MemoryStore())
        assert app.store is None

    def test_store_is_default_false_when_factory(self) -> None:
        """app.store_is_default is False when a callable factory is passed."""
        from cosalette import MemoryStore

        app = App(name="test", store=lambda: MemoryStore())
        assert app.store_is_default is False


class TestAppHasDynamicEntities:
    """Verify has_dynamic_entities property on App.

    Technique: Branch/Condition Coverage — each branch of _has_dynamic_entity_set.
    """

    def test_bare_app_returns_false(self) -> None:
        """App with no registrations: has_dynamic_entities is False."""
        app = App(name="test")
        assert app.has_dynamic_entities is False

    def test_static_app_returns_false(self) -> None:
        """App with static string name= telemetry: has_dynamic_entities is False."""
        app = App(name="test")

        @app.telemetry("temp", interval=10)
        async def _temp() -> dict[str, object]:
            return {}

        assert app.has_dynamic_entities is False

    def test_dynamic_name_callable_returns_true(self) -> None:
        """App with callable name= on telemetry: has_dynamic_entities is True."""

        app = App(name="test")

        @app.telemetry(name=lambda s: ["a", "b"], interval=10)
        async def _temp() -> dict[str, object]:
            return {}

        assert app.has_dynamic_entities is True

    def test_callable_enabled_returns_true(self) -> None:
        """App with callable enabled= on telemetry: has_dynamic_entities is True."""
        app = App(name="test")

        @app.telemetry("temp", interval=10, enabled=lambda s: True)
        async def _temp() -> dict[str, object]:
            return {}

        assert app.has_dynamic_entities is True

    def test_on_configure_hook_returns_true(self) -> None:
        """App with @on_configure hook: has_dynamic_entities is True."""
        app = App(name="test")

        @app.on_configure
        def _configure(ctx: object) -> None:
            pass

        assert app.has_dynamic_entities is True

    def test_device_callable_enabled_returns_true(self) -> None:
        """App with callable enabled= on device: has_dynamic_entities is True."""
        app = App(name="test", store=None)

        @app.device("sensor", enabled=lambda s: True)
        async def _sensor(ctx: DeviceContext) -> None:
            pass

        assert app.has_dynamic_entities is True

    def test_command_callable_enabled_returns_true(self) -> None:
        """App with callable enabled= on command: has_dynamic_entities is True."""
        app = App(name="test", store=None)

        @app.command("reset", enabled=lambda s: True)
        async def _reset(ctx: DeviceContext) -> None:
            pass

        assert app.has_dynamic_entities is True


class TestRegistrationTypeAliases:
    """Verify public registration type aliases are exported and correct.

    Technique: Specification-based Testing — alias identity and isinstance checks.
    """

    def test_aliases_exported_from_top_level(self) -> None:
        """All four aliases are importable from cosalette."""
        from cosalette import (
            CommandRegistration,
            DeviceRegistration,
            PeriodicRegistration,
            TelemetryRegistration,
        )

        assert CommandRegistration is not None
        assert DeviceRegistration is not None
        assert PeriodicRegistration is not None
        assert TelemetryRegistration is not None

    def test_aliases_are_same_class_as_private(self) -> None:
        """Public aliases are identical to the private implementation classes.

        Implementation note: this test deliberately imports the private classes
        to assert object identity (´is´). This couples the test to internal module
        structure, but is an explicit tradeoff — it catches re-export mistakes
        (e.g. PeriodicRegistration accidentally pointing at a different class)
        that pure isinstance or importability tests would miss.
        """
        from cosalette import (
            CommandRegistration,
            DeviceRegistration,
            PeriodicRegistration,
            TelemetryRegistration,
        )
        from cosalette._registration._model import (
            _CommandRegistration,
            _DeviceRegistration,
            _TelemetryRegistration,
        )
        from cosalette._runners._periodic import _PeriodicRegistration

        assert TelemetryRegistration is _TelemetryRegistration
        assert CommandRegistration is _CommandRegistration
        assert DeviceRegistration is _DeviceRegistration
        assert PeriodicRegistration is _PeriodicRegistration

    def test_isinstance_check_works_with_public_alias(
        self, app_with_registrations: App
    ) -> None:
        """Registration objects satisfy isinstance checks against public aliases."""
        from cosalette import (
            CommandRegistration,
            DeviceRegistration,
            PeriodicRegistration,
            TelemetryRegistration,
        )

        assert isinstance(app_with_registrations.devices[0], DeviceRegistration)
        assert isinstance(
            app_with_registrations.telemetry_registrations[0], TelemetryRegistration
        )
        assert isinstance(app_with_registrations.commands[0], CommandRegistration)
        assert isinstance(
            app_with_registrations.periodic_registrations[0], PeriodicRegistration
        )

    def test_stream_registration_alias_exported(self) -> None:
        """StreamRegistration is importable from cosalette."""
        from cosalette import StreamRegistration

        assert StreamRegistration is not None

    def test_stream_registration_alias_is_private_class(self) -> None:
        """StreamRegistration is identical to _StreamRegistration."""
        from cosalette import StreamRegistration
        from cosalette._registration._model import _StreamRegistration

        assert StreamRegistration is _StreamRegistration

    def test_state_registration_alias_exported(self) -> None:
        """StateRegistration is importable from cosalette."""
        from cosalette import StateRegistration

        assert StateRegistration is not None

    def test_state_registration_alias_is_private_class(self) -> None:
        """StateRegistration exported from cosalette is the canonical dataclass."""
        from cosalette import StateRegistration
        from cosalette._persistence._state import (
            StateRegistration as _StateRegistration,
        )

        assert StateRegistration is _StateRegistration


class TestAppNewIntrospectionProperties:
    """Verify stream_registrations, settings_class, and state_factories on App.

    Technique: Specification-based Testing — property contracts and snapshot semantics.
    """

    def test_stream_registrations_empty_by_default(self) -> None:
        """stream_registrations returns empty tuple on a fresh App."""
        app = App(name="test", store=None)
        assert len(app.stream_registrations) == 0

    def test_stream_registrations_returns_registered_streams(self) -> None:
        """stream_registrations reflects handlers added via @app.stream."""
        app = App(name="test", store=None)

        @app.stream("readings")
        async def _handle(stream: Stream[_StreamItem]) -> None:
            async for _ in stream:
                pass

        assert len(app.stream_registrations) == 1
        assert app.stream_registrations[0].name == "readings"
        assert app.stream_registrations[0].func is _handle

    def test_stream_registrations_is_snapshot(self) -> None:
        """stream_registrations returns a tuple snapshot, not the private list."""
        app = App(name="test", store=None)

        @app.stream("readings")
        async def _handle(stream: Stream[_StreamItem]) -> None:
            async for _ in stream:
                pass

        snapshot = app.stream_registrations
        assert snapshot is not app._streams  # type: ignore[attr-defined]  # noqa: SLF001
        with pytest.raises(TypeError):
            snapshot[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]

    def test_settings_class_returns_default_settings(self) -> None:
        """settings_class returns Settings when no custom class is passed."""
        from cosalette import Settings

        app = App(name="test", store=None)
        assert app.settings_class is Settings

    def test_settings_class_returns_custom_subclass(self) -> None:
        """settings_class returns the concrete subclass passed at construction."""
        from cosalette import Settings

        class _MySettings(Settings):
            pass

        app = App(name="test", store=None, settings_class=_MySettings)
        assert app.settings_class is _MySettings

    def test_state_factories_empty_by_default(self) -> None:
        """state_factories returns empty tuple on a fresh App."""
        app = App(name="test", store=None)
        assert app.state_factories == ()

    def test_state_factories_reflects_registered_factories(self) -> None:
        """state_factories contains a StateRegistration after @app.state."""
        from cosalette import StateRegistration

        app = App(name="test", store=None)

        @app.state
        def _make_counter() -> _StateCounter:
            return _StateCounter()

        assert len(app.state_factories) == 1
        assert isinstance(app.state_factories[0], StateRegistration)
        assert app.state_factories[0].state_type is _StateCounter

    def test_state_factories_is_snapshot(self) -> None:
        """state_factories returns a tuple snapshot, not the private list."""
        app = App(name="test", store=None)

        @app.state
        def _make_counter() -> _StateCounter:
            return _StateCounter()

        snapshot = app.state_factories
        assert snapshot is not app._state_factories  # type: ignore[attr-defined]  # noqa: SLF001
        with pytest.raises(TypeError):
            snapshot[0] = None  # type: ignore[index]  # ty: ignore[invalid-assignment]


class _DomainError(Exception):
    """App-owned domain exception for error_type_map tests."""


class TestErrorTypeMap:
    """App.error_type_map construction, validation, and accessor (cos-ooj)."""

    def test_defaults_to_empty(self) -> None:
        """A fresh App with no error_type_map exposes an empty map."""
        app = App(name="test", store=None)
        assert app.error_type_map == {}

    def test_stores_provided_entries(self) -> None:
        """Provided entries are exposed via the read-only accessor."""
        app = App(
            name="test",
            store=None,
            error_type_map={_DomainError: "domain_error"},
        )
        assert app.error_type_map == {_DomainError: "domain_error"}

    def test_accessor_returns_a_copy(self) -> None:
        """error_type_map returns a copy, not the private dict."""
        app = App(
            name="test",
            store=None,
            error_type_map={_DomainError: "domain_error"},
        )
        snapshot = app.error_type_map
        snapshot[_DomainError] = "mutated"
        assert app.error_type_map[_DomainError] == "domain_error"

    def test_rejects_non_exception_key(self) -> None:
        """A non-exception key is rejected loudly at construction."""
        with pytest.raises(TypeError, match="exception classes"):
            App(
                name="test",
                store=None,
                error_type_map={str: "nope"},  # ty: ignore[invalid-argument-type]
            )

    def test_rejects_exception_instance_key(self) -> None:
        """An exception instance (not a class) is rejected."""
        with pytest.raises(TypeError, match="exception classes"):
            App(
                name="test",
                store=None,
                error_type_map={_DomainError("x"): "nope"},  # ty: ignore[invalid-argument-type]
            )

    def test_rejects_baseexception_only_key(self) -> None:
        """A BaseException that is not an Exception can never match, so reject it.

        The publisher only handles ``except Exception``; a ``BaseException``-only
        key (e.g. ``KeyboardInterrupt``) would be dead config rather than a live
        opt-in, which is the silent degradation the loud validation prevents.
        """
        with pytest.raises(TypeError, match="exception classes"):
            App(
                name="test",
                store=None,
                error_type_map={KeyboardInterrupt: "nope"},  # ty: ignore[invalid-argument-type]
            )

    def test_rejects_non_str_value(self) -> None:
        """A non-string value is rejected loudly at construction."""
        with pytest.raises(TypeError, match="strings"):
            App(
                name="test",
                store=None,
                error_type_map={_DomainError: 123},  # ty: ignore[invalid-argument-type]
            )
