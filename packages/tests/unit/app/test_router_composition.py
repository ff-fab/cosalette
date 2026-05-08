"""Tests for Router composition API and App.include_router.

Covers: Router class, decorator registration, include_router with prefix/tags/adapters,
snapshot semantics, multiple inclusion, and dependency rejection.

Test Techniques Used:
- Specification-based: Router constructor contracts and include_router semantics.
- Equivalence Partitioning: Prefix combos (none/router/include/both) as input classes.
- Boundary Value Analysis: Empty prefix, single-segment prefix, multi-segment rejection.
- Error Guessing: Name collision, adapter conflict, unregistered state type, invalid
  MQTT characters, multiple Stream parameters.
- State Transition: Snapshot semantics (frozen at include_router call time).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import pytest

from cosalette import App, Router
from cosalette._context import DeviceContext
from cosalette._stream import Stream

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test adapter ports for adapter conflict detection
# ---------------------------------------------------------------------------


class SensorReading:
    """Test type for stream items."""

    def __init__(self, value: float) -> None:
        self.value = value


class DummyPort(Protocol):
    """Test protocol."""

    def read(self) -> int: ...


class DummyImpl:
    """Test implementation."""

    def read(self) -> int:
        return 42


class AnotherImpl:
    """Another test implementation."""

    def read(self) -> int:
        return 99


# ---------------------------------------------------------------------------
# Router export and construction
# ---------------------------------------------------------------------------


class TestRouterExport:
    """Router is exported from cosalette public API."""

    def test_router_exported_from_main_module(self) -> None:
        """Router can be imported from cosalette."""
        from cosalette import Router as ImportedRouter

        assert ImportedRouter is Router


class TestRouterConstruction:
    """Router constructor accepts prefix, tags, dependencies, adapters."""

    def test_router_with_no_args(self) -> None:
        """Router() with no arguments constructs successfully."""
        router = Router()
        assert router._prefix is None
        assert router._tags == []

    def test_router_with_prefix(self) -> None:
        """Router(prefix='segment') stores the prefix."""
        router = Router(prefix="sensors")
        assert router._prefix == "sensors"

    def test_router_with_tags(self) -> None:
        """Router(tags=['a', 'b']) stores the tags."""
        router = Router(tags=["environment", "production"])
        assert router._tags == ["environment", "production"]

    def test_router_rejects_invalid_prefix(self) -> None:
        """Router(prefix='foo/bar') raises ValueError for MQTT special chars."""
        with pytest.raises(ValueError, match="invalid MQTT characters"):
            Router(prefix="sensors/room1")

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            Router(prefix="device+")

    def test_router_rejects_nonempty_dependencies(self) -> None:
        """Router(dependencies=[...]) raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="cos-ebc epic"):
            Router(dependencies=["fake_dep"])

    def test_router_accepts_none_dependencies(self) -> None:
        """Router(dependencies=None) is allowed."""
        router = Router(dependencies=None)
        assert router._dependencies is None

    def test_router_with_adapters(self) -> None:
        """Router(adapters={...}) stores adapter declarations."""
        router = Router(adapters={DummyPort: DummyImpl})
        assert DummyPort in router._adapters


# ---------------------------------------------------------------------------
# Router decorators
# ---------------------------------------------------------------------------


class TestRouterDecorators:
    """Router provides telemetry, command, device, stream, periodic decorators."""

    async def test_router_device_decorator(self) -> None:
        """@router.device registers a device on the router."""
        router = Router()

        @router.device("valve")
        async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        assert len(router._devices) == 1
        assert router._devices[0].name == "valve"

    async def test_router_telemetry_decorator(self) -> None:
        """@router.telemetry registers telemetry on the router."""
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        assert len(router._telemetry) == 1
        assert router._telemetry[0].name == "temp"
        assert router._telemetry[0].interval == 30

    async def test_router_command_decorator(self) -> None:
        """@router.command registers a command on the router."""
        router = Router()

        @router.command("calibrate")
        async def calibrate(payload: bytes) -> None:
            pass

        assert len(router._commands) == 1
        assert router._commands[0].name == "calibrate"

    async def test_router_periodic_decorator(self) -> None:
        """@router.periodic registers a periodic task on the router."""
        router = Router()

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        assert len(router._periodic) == 1
        assert router._periodic[0].name == "heartbeat"

    def test_router_decorator_rejects_nonempty_dependencies(self) -> None:
        """Router decorators reject dependencies=[...] with NotImplementedError."""
        router = Router()

        with pytest.raises(NotImplementedError, match="cos-ebc epic"):

            @router.device("valve", dependencies=["fake"])
            async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
                yield

        with pytest.raises(NotImplementedError, match="cos-ebc epic"):

            @router.telemetry("temp", interval=30, dependencies=["fake"])
            async def temp() -> dict:
                return {}

        with pytest.raises(NotImplementedError, match="cos-ebc epic"):

            @router.command("cmd", dependencies=["fake"])
            async def cmd() -> None:
                pass


# ---------------------------------------------------------------------------
# App.include_router
# ---------------------------------------------------------------------------


class TestIncludeRouter:
    """App.include_router merges router registrations into the app."""

    def test_include_router_with_no_prefix(self) -> None:
        """App.include_router(router) with no prefix merges registrations as-is."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router)

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "temp"

    def test_include_router_with_router_prefix(self) -> None:
        """Router(prefix='sensors') applies prefix at include time."""
        app = App(name="bridge", version="1.0.0")
        router = Router(prefix="sensors")

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router)

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "sensors/temp"

    def test_include_router_with_include_prefix(self) -> None:
        """include_router(prefix='room1') applies prefix at include time."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router, prefix="room1")

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "room1/temp"

    def test_include_router_with_combined_prefix(self) -> None:
        """Router(prefix='sensors') + include_router(prefix='room1')
        combines prefixes.
        """
        app = App(name="bridge", version="1.0.0")
        router = Router(prefix="sensors")

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router, prefix="room1")

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "room1/sensors/temp"

    def test_include_router_rejects_invalid_prefix(self) -> None:
        """include_router(prefix='foo/bar') raises ValueError."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            app.include_router(router, prefix="room1/sensors")

    def test_include_router_rejects_nonempty_dependencies(self) -> None:
        """include_router(dependencies=[...]) raises NotImplementedError."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        with pytest.raises(NotImplementedError, match="cos-ebc epic"):
            app.include_router(router, dependencies=["fake_dep"])


# ---------------------------------------------------------------------------
# Tag accumulation
# ---------------------------------------------------------------------------


class TestTagAccumulation:
    """Tags accumulate: Router constructor → include_router → operation."""

    def test_router_constructor_tags_attached(self) -> None:
        """Router(tags=['env']) attaches tags to registrations."""
        app = App(name="bridge", version="1.0.0")
        router = Router(tags=["environment"])

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router)

        # Tags stored on the registration record after include_router
        assert len(app.telemetry_registrations) == 1
        assert "environment" in app.telemetry_registrations[0].tags

    def test_include_router_tags_accumulate(self) -> None:
        """include_router(tags=['prod']) adds tags to router tags."""
        app = App(name="bridge", version="1.0.0")
        router = Router(tags=["environment"])

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router, tags=["production"])

        # Tags are accumulated during include_router and stored on registration
        assert len(app.telemetry_registrations) == 1
        tags = list(app.telemetry_registrations[0].tags)
        assert "environment" in tags
        assert "production" in tags

    def test_operation_tags_accumulate(self) -> None:
        """Operation-level tags accumulate for all registration types."""
        app = App(name="bridge", version="1.0.0")
        router = Router(tags=["environment"])

        @router.telemetry("temp", interval=30, tags=["sensor"])
        async def temp2() -> dict:
            return {"celsius": 22.5}

        @router.device("valve", tags=["actuator"])
        async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        @router.command("calibrate", tags=["admin"])
        async def calibrate() -> None:
            pass

        @router.periodic("heartbeat", interval=60, tags=["monitoring"])
        async def heartbeat() -> None:
            pass

        app.include_router(router, tags=["production"])

        # Telemetry: tags accumulated on registration record
        assert len(app.telemetry_registrations) == 1
        telemetry_tags = list(app.telemetry_registrations[0].tags)
        assert "environment" in telemetry_tags
        assert "sensor" in telemetry_tags
        assert "production" in telemetry_tags
        # Order preserved: router constructor, include_router, then operation
        assert telemetry_tags.index("environment") < telemetry_tags.index("production")
        assert telemetry_tags.index("production") < telemetry_tags.index("sensor")

        # Device: tags accumulated on registration record
        assert len(app.devices) == 1
        device_tags = list(app.devices[0].tags)
        assert "environment" in device_tags
        assert "actuator" in device_tags
        assert "production" in device_tags
        assert device_tags.index("environment") < device_tags.index("production")
        assert device_tags.index("production") < device_tags.index("actuator")

        # Command: tags accumulated on registration record
        assert len(app.commands) == 1
        command_tags = list(app.commands[0].tags)
        assert "environment" in command_tags
        assert "admin" in command_tags
        assert "production" in command_tags
        assert command_tags.index("environment") < command_tags.index("production")
        assert command_tags.index("production") < command_tags.index("admin")

        # Periodic: tags accumulated on registration record
        assert len(app.periodic_registrations) == 1
        periodic_tags = list(app.periodic_registrations[0].tags)
        assert "environment" in periodic_tags
        assert "monitoring" in periodic_tags
        assert "production" in periodic_tags
        assert periodic_tags.index("environment") < periodic_tags.index("production")
        assert periodic_tags.index("production") < periodic_tags.index("monitoring")

    def test_tags_deduplicated_preserving_first_occurrence(self) -> None:
        """Duplicate tags are removed, keeping first occurrence."""
        app = App(name="bridge", version="1.0.0")
        router = Router(tags=["shared", "router"])

        @router.telemetry("temp", interval=30, tags=["shared", "operation"])
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router, tags=["shared", "include"])

        # Tags deduplicated on the registration record after include_router
        assert len(app.telemetry_registrations) == 1
        tags = list(app.telemetry_registrations[0].tags)
        # "shared" appears only once, at first position
        assert tags.count("shared") == 1
        assert tags[0] == "shared"
        assert "router" in tags
        assert "include" in tags
        assert "operation" in tags


# ---------------------------------------------------------------------------
# Snapshot semantics
# ---------------------------------------------------------------------------


class TestSnapshotSemantics:
    """include_router captures registrations at call time."""

    def test_later_registrations_not_included(self) -> None:
        """Operations registered after include_router are not merged."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router)
        assert len(app._telemetry) == 1

        # Register another telemetry after include
        @router.telemetry("humidity", interval=30)
        async def humidity() -> dict:
            return {"percent": 50}

        # App still has only the first registration
        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "temp"


# ---------------------------------------------------------------------------
# Multiple inclusion
# ---------------------------------------------------------------------------


class TestMultipleInclusion:
    """A router can be included multiple times with different prefixes."""

    def test_include_router_twice_with_different_prefixes(self) -> None:
        """include_router(router, prefix='room1') + (router, prefix='room2')."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {"celsius": 22.5}

        app.include_router(router, prefix="room1")
        app.include_router(router, prefix="room2")

        assert len(app._telemetry) == 2
        names = {reg.name for reg in app._telemetry}
        assert names == {"room1/temp", "room2/temp"}


# ---------------------------------------------------------------------------
# Adapter merging and conflict detection
# ---------------------------------------------------------------------------


class TestAdapterMerging:
    """Adapters from Router and include_router are merged with conflict detection."""

    def test_router_adapters_merged_into_app(self) -> None:
        """Router(adapters={...}) declarations are merged at include time."""
        app = App(name="bridge", version="1.0.0")
        router = Router(adapters={DummyPort: DummyImpl})

        app.include_router(router)

        assert DummyPort in app._adapters

    def test_include_router_adapters_merged(self) -> None:
        """include_router(adapters={...}) declarations are merged."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        app.include_router(router, adapters={DummyPort: DummyImpl})

        assert DummyPort in app._adapters

    def test_adapter_conflict_raises_at_include_time(self) -> None:
        """Router adapter conflicts with app adapter → ValueError."""
        app = App(name="bridge", version="1.0.0", adapters={DummyPort: DummyImpl})
        router = Router(adapters={DummyPort: AnotherImpl})

        with pytest.raises(ValueError, match="Adapter conflict"):
            app.include_router(router)

    def test_include_router_adapter_conflict_with_app(self) -> None:
        """include_router(adapters={...}) conflicts with app adapter → ValueError."""
        app = App(name="bridge", version="1.0.0", adapters={DummyPort: DummyImpl})
        router = Router()

        # This should succeed since we're passing adapters to include_router, not Router
        # But the adapter() method call inside include_router will detect the conflict
        with pytest.raises(ValueError, match="already registered"):
            app.include_router(router, adapters={DummyPort: AnotherImpl})


# ---------------------------------------------------------------------------
# Registered names tracking
# ---------------------------------------------------------------------------


class TestRegisteredNames:
    """Router.registered_names tracks all operation names."""

    def test_registered_names_empty_for_new_router(self) -> None:
        """Router() with no registrations has empty registered_names."""
        router = Router()
        assert router.registered_names == frozenset()

    def test_registered_names_includes_all_operation_types(self) -> None:
        """registered_names includes device, telemetry, command, periodic."""
        router = Router()

        @router.device("valve")
        async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {}

        @router.command("calibrate")
        async def calibrate() -> None:
            pass

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        names = router.registered_names
        assert names == {"valve", "temp", "calibrate", "heartbeat"}


# ---------------------------------------------------------------------------
# Operation type coverage
# ---------------------------------------------------------------------------


class TestOperationTypeCoverage:
    """Router supports device, telemetry, command, stream, periodic operations."""

    def test_router_device_registration(self) -> None:
        """@router.device stores _DeviceRegistration."""
        router = Router()

        @router.device("valve")
        async def valve(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)
        assert len(app._devices) == 1

    def test_router_telemetry_registration(self) -> None:
        """@router.telemetry stores _TelemetryRegistration."""
        router = Router()

        @router.telemetry("temp", interval=30)
        async def temp() -> dict:
            return {}

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)
        assert len(app._telemetry) == 1

    def test_router_command_registration(self) -> None:
        """@router.command stores _CommandRegistration."""
        router = Router()

        @router.command("calibrate")
        async def calibrate() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)
        assert len(app._commands) == 1

    def test_router_periodic_registration(self) -> None:
        """@router.periodic stores _PeriodicRegistration."""
        router = Router()

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)
        assert len(app._periodic) == 1


# ---------------------------------------------------------------------------
# Stream decorator and composition
# ---------------------------------------------------------------------------


class TestRouterStream:
    """Router.stream decorator and lazy adapter validation (cos-s2q.4)."""

    def test_router_stream_decorator_without_adapter(self) -> None:
        """@router.stream registers without requiring adapter at decoration time."""
        router = Router()

        # Should succeed without adapter being registered
        @router.stream("sensor_stream")
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        assert len(router._streams) == 1
        assert router._streams[0].name == "sensor_stream"

    def test_router_stream_included_without_adapter(self) -> None:
        """include_router succeeds for streams without adapters (cos-s2q.4)."""
        router = Router()

        @router.stream("sensor_stream")
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        app = App(name="bridge", version="1.0.0")
        # Should succeed without adapter
        app.include_router(router)

        assert len(app._streams) == 1
        assert app._streams[0].name == "sensor_stream"

    def test_router_stream_with_prefix(self) -> None:
        """Router(prefix='sensors') applies prefix to stream names."""
        router = Router(prefix="sensors")

        @router.stream("temperature")
        async def handle_temp_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)

        assert len(app._streams) == 1
        assert app._streams[0].name == "sensors/temperature"

    def test_router_stream_with_tags(self) -> None:
        """Router stream tags accumulate from router constructor and operation."""
        router = Router(tags=["environment"])

        @router.stream("sensor_stream", tags=["ble"])
        async def handle_sensor_stream(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router, tags=["production"])

        assert len(app._streams) == 1
        tags = list(app._streams[0].tags)
        assert "environment" in tags
        assert "production" in tags
        assert "ble" in tags

    def test_router_stream_immediate_vs_enabled_callable_equivalent(self) -> None:
        """Router.stream immediate and enabled=callable are equivalent.

        Both paths share validate_stream_signature() and produce identical
        _StreamRegistration fields except enabled_spec value.
        """
        # Immediate path: enabled=True (static)
        router_immediate = Router()

        @router_immediate.stream("sensor_immediate")
        async def handle_immediate(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Deferred path: enabled=callable
        router_deferred = Router()

        @router_deferred.stream("sensor_deferred", enabled=lambda ctx: True)
        async def handle_deferred(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        # Both should register exactly one stream
        assert len(router_immediate._streams) == 1
        assert len(router_deferred._streams) == 1

        reg_immediate = router_immediate._streams[0]
        reg_deferred = router_deferred._streams[0]

        # All fields equivalent except name and enabled_spec
        assert reg_immediate.name == "sensor_immediate"
        assert reg_deferred.name == "sensor_deferred"
        assert reg_immediate.maxsize == reg_deferred.maxsize
        assert reg_immediate.backpressure == reg_deferred.backpressure
        assert reg_immediate.is_root == reg_deferred.is_root
        assert reg_immediate.tags == reg_deferred.tags

        # enabled_spec differs: True vs callable
        assert reg_immediate.enabled_spec is True
        assert callable(reg_deferred.enabled_spec)


# ---------------------------------------------------------------------------
# React decorator
# ---------------------------------------------------------------------------


class TestRouterReact:
    """Router.react decorator and include_router reactor merging."""

    def test_router_react_decorator(self) -> None:
        """@router.react registers a reactor on the router."""
        router = Router()

        @router.react(int)
        async def handle_events() -> None:
            pass

        assert len(router._reactors) == 1
        assert router._reactors[0].state_type is int

    def test_router_react_rejects_non_async(self) -> None:
        """@router.react raises TypeError for non-async functions."""
        router = Router()

        with pytest.raises(TypeError, match="must be async"):

            @router.react(int)
            def handle_events() -> None:
                pass

    def test_include_router_merges_reactors_with_validation(self) -> None:
        """include_router validates state_type is registered via @app.state."""
        app = App(name="bridge", version="1.0.0")

        # Register a state factory first
        @app.state
        def make_state() -> int:
            return 42

        router = Router()

        @router.react(int)
        async def handle_events() -> None:
            pass

        app.include_router(router)

        assert len(app._reactors) == 1
        assert app._reactors[0].state_type is int

    def test_include_router_rejects_unregistered_state_type(self) -> None:
        """include_router raises ValueError when state_type is not registered."""
        app = App(name="bridge", version="1.0.0")
        router = Router()

        @router.react(int)
        async def handle_events() -> None:
            pass

        with pytest.raises(ValueError, match="not registered via @app.state"):
            app.include_router(router)

    def test_router_react_with_custom_drain(self) -> None:
        """@router.react accepts custom drain callable."""
        router = Router()

        def custom_drain(state: int) -> None:
            pass

        @router.react(int, drain=custom_drain)
        async def handle_events() -> None:
            pass

        assert len(router._reactors) == 1
        assert router._reactors[0].drain is custom_drain

    def test_router_react_detects_events_parameter(self) -> None:
        """@router.react detects 'events' parameter and skips it from DI."""
        router = Router()

        @router.react(int)
        async def handle_events(events: list) -> None:
            pass

        assert len(router._reactors) == 1
        assert router._reactors[0].events_param == "events"


# ---------------------------------------------------------------------------
# Stream guard: enabled=False and multiple Stream params
# ---------------------------------------------------------------------------


class TestRouterStreamGuards:
    """Edge cases for Router.stream: enabled=False and multi-Stream rejection."""

    def test_router_stream_enabled_false_skips_registration(self) -> None:
        """@router.stream(enabled=False) returns func without registering."""
        router = Router()

        @router.stream("disabled_stream", enabled=False)
        async def handle_disabled(stream: Stream[SensorReading]) -> None:
            async for _ in stream:
                pass

        assert len(router._streams) == 0

    def test_router_stream_rejects_multiple_stream_params(self) -> None:
        """@router.stream raises TypeError when handler declares two Stream params."""
        router = Router()

        with pytest.raises(TypeError, match="multiple"):

            @router.stream("bad_stream")
            async def handle_two_streams(
                s1: Stream[SensorReading], s2: Stream[SensorReading]
            ) -> None:
                async for _ in s1:
                    pass


# ---------------------------------------------------------------------------
# Name collision detection on include_router
# ---------------------------------------------------------------------------


class TestIncludeRouterCollision:
    """include_router raises ValueError when names collide with existing registrations."""  # noqa: E501

    def test_include_router_raises_on_standard_name_collision(self) -> None:
        """include_router raises ValueError when router name matches existing app registration."""  # noqa: E501
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("temperature", interval=30)
        async def existing_temp() -> dict:
            return {"celsius": 22.5}

        router = Router()

        @router.telemetry("temperature", interval=30)
        async def router_temp() -> dict:
            return {"celsius": 22.5}

        with pytest.raises(ValueError, match="already registered"):
            app.include_router(router)

    def test_include_router_raises_on_periodic_name_collision(self) -> None:
        """include_router raises ValueError when periodic name collides with existing registration."""  # noqa: E501
        app = App(name="bridge", version="1.0.0")

        @app.telemetry("heartbeat", interval=60)
        async def existing_heartbeat() -> dict:
            return {}

        router = Router()

        @router.periodic("heartbeat", interval=60)
        async def router_heartbeat() -> None:
            pass

        with pytest.raises(ValueError, match="already registered"):
            app.include_router(router)

    def test_include_router_raises_on_collision_with_prefix(self) -> None:
        """include_router raises ValueError when two routers produce the same prefixed name."""  # noqa: E501
        app = App(name="bridge", version="1.0.0")
        router = Router(prefix="sensors")

        @router.telemetry("temperature", interval=30)
        async def router_temp() -> dict:
            return {"celsius": 22.5}

        # First inclusion succeeds: registers "sensors/temperature"
        app.include_router(router)

        # Second router with same prefix+name collides with "sensors/temperature"
        router2 = Router(prefix="sensors")

        @router2.telemetry("temperature", interval=30)
        async def router2_temp() -> dict:
            return {"celsius": 22.5}

        with pytest.raises(ValueError, match="already registered"):
            app.include_router(router2)


# ---------------------------------------------------------------------------
# Periodic prefix application
# ---------------------------------------------------------------------------


class TestIncludeRouterPeriodicPrefix:
    """include_router correctly applies prefix to periodic registrations (C1 regression)."""  # noqa: E501

    def test_periodic_with_router_prefix(self) -> None:
        """Router(prefix='sensors') prefixes periodic names on include_router."""
        router = Router(prefix="sensors")

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)

        assert len(app._periodic) == 1
        assert app._periodic[0].name == "sensors/heartbeat"

    def test_periodic_with_include_prefix(self) -> None:
        """include_router(prefix='floor1') prefixes periodic names."""
        router = Router()

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router, prefix="floor1")

        assert len(app._periodic) == 1
        assert app._periodic[0].name == "floor1/heartbeat"

    def test_periodic_with_combined_router_and_include_prefix(self) -> None:
        """Router(prefix='sensors') + include_router(prefix='floor1') combines both prefixes."""  # noqa: E501
        router = Router(prefix="sensors")

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router, prefix="floor1")

        assert len(app._periodic) == 1
        assert app._periodic[0].name == "floor1/sensors/heartbeat"

    def test_periodic_no_prefix_unchanged(self) -> None:
        """Periodic name is unchanged when neither router nor include has a prefix."""
        router = Router()

        @router.periodic("heartbeat", interval=60)
        async def heartbeat() -> None:
            pass

        app = App(name="bridge", version="1.0.0")
        app.include_router(router)

        assert len(app._periodic) == 1
        assert app._periodic[0].name == "heartbeat"
