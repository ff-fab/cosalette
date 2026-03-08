"""Unit tests for the cosalette registry introspection module.

Covers: ``build_registry_snapshot()`` across empty apps, devices,
telemetry (with intervals, strategies, persist, groups), commands,
adapters, composite strategies/policies, and JSON round-trip.

Test Techniques Used:
    - Specification-based Testing: Output shape and field values per
      registration type.
    - Boundary-value Testing: Zero-element (empty app) case.
    - Round-trip Testing: JSON serialization/deserialization fidelity.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

import pytest

import cosalette
from cosalette._context import DeviceContext
from cosalette._introspect import build_registry_snapshot

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — port protocols and adapter stubs for adapter tests
# ---------------------------------------------------------------------------


@runtime_checkable
class _TestPort(Protocol):
    """Dummy port protocol for adapter introspection tests."""

    def do_thing(self) -> str: ...


class _TestImpl:
    """Concrete adapter for introspection tests."""

    def do_thing(self) -> str:
        return "real"


class _TestDryRun:
    """Dry-run adapter for introspection tests."""

    def do_thing(self) -> str:
        return "dry"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyApp:
    """Snapshot of an app with no registrations.

    Technique: Boundary-value Testing — zero-element case.
    """

    def test_empty_app_has_correct_structure(self) -> None:
        """An empty app produces empty lists for all registration types."""
        app = cosalette.App(name="empty", version="0.1.0")
        snap = build_registry_snapshot(app)

        assert snap["app"] == {
            "name": "empty",
            "version": "0.1.0",
            "description": "IoT-to-MQTT bridge",
        }
        assert snap["devices"] == []
        assert snap["telemetry"] == []
        assert snap["commands"] == []
        assert snap["adapters"] == []


class TestDeviceRegistration:
    """Snapshot of a registered device.

    Technique: Specification-based Testing — verifying output shape
    and values for a single device registration.
    """

    def test_device_entry_in_snapshot(self) -> None:
        """A registered device appears in the snapshot with correct fields."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None: ...

        snap = build_registry_snapshot(app)

        assert len(snap["devices"]) == 1
        dev = snap["devices"][0]
        assert dev["name"] == "sensor"
        assert dev["type"] == "device"
        assert "sensor" in dev["func"]
        assert dev["has_init"] is False
        assert isinstance(dev["dependencies"], list)


class TestTelemetryResolvedInterval:
    """Telemetry with a float interval.

    Technique: Specification-based Testing — verifying concrete
    interval values pass through as floats.
    """

    def test_float_interval_passthrough(self) -> None:
        """A float interval is preserved as a float in the snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=5.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["interval"] == 5.0
        assert isinstance(tel["interval"], float)


class TestTelemetryDeferredInterval:
    """Telemetry with a callable interval (deferred resolution).

    Technique: Specification-based Testing — verifying that callable
    intervals are described as ``"<deferred>"``.
    """

    def test_callable_interval_becomes_deferred(self) -> None:
        """A callable interval is represented as '<deferred>'."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=lambda settings: 5.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["interval"] == "<deferred>"


class TestTelemetryWithStrategy:
    """Telemetry with a publish strategy.

    Technique: Specification-based Testing — verifying strategy
    description strings for leaf strategies.
    """

    def test_every_seconds_strategy(self) -> None:
        """Every(seconds=5.0) is described correctly."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0, publish=cosalette.Every(seconds=5.0))
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] == "Every(seconds=5.0)"

    def test_every_n_strategy(self) -> None:
        """Every(n=3) is described correctly."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0, publish=cosalette.Every(n=3))
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] == "Every(n=3)"

    def test_on_change_no_threshold(self) -> None:
        """OnChange() with no threshold is described correctly."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0, publish=cosalette.OnChange())
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] == "OnChange()"

    def test_on_change_with_float_threshold(self) -> None:
        """OnChange(threshold=0.5) is described correctly."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0, publish=cosalette.OnChange(threshold=0.5))
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] == "OnChange(threshold=0.5)"

    def test_on_change_with_dict_threshold(self) -> None:
        """OnChange(threshold={'temp': 0.5}) is described correctly."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry(
            "temp",
            interval=1.0,
            publish=cosalette.OnChange(threshold={"temp": 0.5}),
        )
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] == "OnChange(threshold={'temp': 0.5})"

    def test_no_strategy_is_none(self) -> None:
        """Telemetry without strategy has strategy=None."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["strategy"] is None


class TestTelemetryWithPersistPolicy:
    """Telemetry with persist policies.

    Technique: Specification-based Testing — verifying persist policy
    description strings for leaf policies.
    """

    def test_save_on_publish(self) -> None:
        """SaveOnPublish() is described correctly."""
        app = cosalette.App(name="test", version="0.1.0", store=cosalette.MemoryStore())

        @app.telemetry("temp", interval=1.0, persist=cosalette.SaveOnPublish())
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["persist"] == "SaveOnPublish()"

    def test_save_on_change(self) -> None:
        """SaveOnChange() is described correctly."""
        app = cosalette.App(name="test", version="0.1.0", store=cosalette.MemoryStore())

        @app.telemetry("temp", interval=1.0, persist=cosalette.SaveOnChange())
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["persist"] == "SaveOnChange()"

    def test_save_on_shutdown(self) -> None:
        """SaveOnShutdown() is described correctly."""
        app = cosalette.App(name="test", version="0.1.0", store=cosalette.MemoryStore())

        @app.telemetry("temp", interval=1.0, persist=cosalette.SaveOnShutdown())
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["persist"] == "SaveOnShutdown()"

    def test_no_persist_is_none(self) -> None:
        """Telemetry without persist has persist=None."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["persist"] is None


class TestTelemetryWithGroup:
    """Telemetry with coalescing group.

    Technique: Specification-based Testing — verifying group field.
    """

    def test_group_field_present(self) -> None:
        """Group name is captured in the snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0, group="sensors")
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["group"] == "sensors"

    def test_no_group_is_none(self) -> None:
        """Telemetry without group has group=None."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=1.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["group"] is None


class TestCommandRegistration:
    """Snapshot of a registered command.

    Technique: Specification-based Testing — verifying command entry
    shape and mqtt_params sorting.
    """

    def test_command_entry_in_snapshot(self) -> None:
        """A registered command appears with sorted mqtt_params."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("light")
        async def light(topic: str, payload: str) -> dict[str, object] | None:
            return {"state": "on"}

        snap = build_registry_snapshot(app)

        assert len(snap["commands"]) == 1
        cmd = snap["commands"][0]
        assert cmd["name"] == "light"
        assert cmd["type"] == "command"
        assert cmd["mqtt_params"] == ["payload", "topic"]
        assert cmd["has_init"] is False


class TestAdapterRegistration:
    """Snapshot of registered adapters.

    Technique: Specification-based Testing — verifying port/impl/dry_run
    fields for adapters.
    """

    def test_adapter_entry_in_snapshot(self) -> None:
        """A registered adapter appears with port, impl, and dry_run."""
        app = cosalette.App(name="test", version="0.1.0")
        app.adapter(_TestPort, _TestImpl, dry_run=_TestDryRun)

        snap = build_registry_snapshot(app)

        assert len(snap["adapters"]) == 1
        adp = snap["adapters"][0]
        assert adp["port"] == "_TestPort"
        assert adp["impl"] == "_TestImpl"
        assert adp["dry_run"] == "_TestDryRun"

    def test_adapter_without_dry_run(self) -> None:
        """An adapter without dry_run has dry_run=None."""
        app = cosalette.App(name="test", version="0.1.0")
        app.adapter(cosalette.MqttPort, cosalette.NullMqttClient)

        snap = build_registry_snapshot(app)

        adp = snap["adapters"][0]
        assert adp["port"] == "MqttPort"
        assert adp["impl"] == "NullMqttClient"
        assert adp["dry_run"] is None


class TestAdapterStringImport:
    """Adapter with a string import path.

    Technique: Specification-based Testing — verifying string passthrough.
    """

    def test_string_import_passthrough(self) -> None:
        """A string import path is preserved as-is in the snapshot."""
        app = cosalette.App(name="test", version="0.1.0")
        app.adapter(_TestPort, "mypackage.adapters:MyImpl")

        snap = build_registry_snapshot(app)

        adp = snap["adapters"][0]
        assert adp["impl"] == "mypackage.adapters:MyImpl"


class TestAdapterCallableFactory:
    """Adapter with a callable factory function.

    Technique: Specification-based Testing — verifying __qualname__
    extraction for callable adapter impls.
    """

    def test_callable_factory_uses_qualname(self) -> None:
        """A factory function's __qualname__ is used as the impl description."""

        def my_adapter_factory() -> _TestImpl:
            return _TestImpl()  # pragma: no cover

        app = cosalette.App(name="test", version="0.1.0")
        app.adapter(_TestPort, my_adapter_factory)

        snap = build_registry_snapshot(app)

        adp = snap["adapters"][0]
        assert "my_adapter_factory" in adp["impl"]


class TestCompositeStrategies:
    """Composite strategy descriptions.

    Technique: Specification-based Testing — verifying recursive
    description for OR/AND composites.
    """

    def test_any_strategy_description(self) -> None:
        """Every(seconds=5.0) | OnChange() → AnyStrategy description."""
        app = cosalette.App(name="test", version="0.1.0")
        strategy = cosalette.Every(seconds=5.0) | cosalette.OnChange()

        @app.telemetry("temp", interval=1.0, publish=strategy)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert (
            snap["telemetry"][0]["strategy"]
            == "AnyStrategy(Every(seconds=5.0), OnChange())"
        )

    def test_all_strategy_description(self) -> None:
        """Every(seconds=5.0) & OnChange() → AllStrategy description."""
        app = cosalette.App(name="test", version="0.1.0")
        strategy = cosalette.Every(seconds=5.0) & cosalette.OnChange()

        @app.telemetry("temp", interval=1.0, publish=strategy)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert (
            snap["telemetry"][0]["strategy"]
            == "AllStrategy(Every(seconds=5.0), OnChange())"
        )


class TestCompositePersistPolicies:
    """Composite persist-policy descriptions.

    Technique: Specification-based Testing — verifying recursive
    description for OR/AND policy composites.
    """

    def test_any_save_policy_description(self) -> None:
        """SaveOnPublish() | SaveOnChange() → AnySavePolicy description."""
        app = cosalette.App(name="test", version="0.1.0", store=cosalette.MemoryStore())
        policy = cosalette.SaveOnPublish() | cosalette.SaveOnChange()

        @app.telemetry("temp", interval=1.0, persist=policy)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert (
            snap["telemetry"][0]["persist"]
            == "AnySavePolicy(SaveOnPublish(), SaveOnChange())"
        )

    def test_all_save_policy_description(self) -> None:
        """SaveOnPublish() & SaveOnChange() → AllSavePolicy description."""
        app = cosalette.App(name="test", version="0.1.0", store=cosalette.MemoryStore())
        policy = cosalette.SaveOnPublish() & cosalette.SaveOnChange()

        @app.telemetry("temp", interval=1.0, persist=policy)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert (
            snap["telemetry"][0]["persist"]
            == "AllSavePolicy(SaveOnPublish(), SaveOnChange())"
        )


class TestTelemetryWithInit:
    """Telemetry with an init callback.

    Technique: Specification-based Testing — verifying has_init flag
    for telemetry with init callback.
    """

    def test_has_init_true_when_init_provided(self) -> None:
        """has_init is True when init= is provided."""
        app = cosalette.App(name="test", version="0.1.0")

        def make_filter() -> float:
            return 1.0

        @app.telemetry("temp", interval=1.0, init=make_filter)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["has_init"] is True


class TestDeviceWithInitAndDependencies:
    """Device with init callback and injected dependencies.

    Technique: Specification-based Testing — verifying has_init and
    non-empty dependencies for device and command registrations.
    """

    def test_device_has_init_and_dependencies(self) -> None:
        """A device with init= and injected context shows both fields."""
        app = cosalette.App(name="test", version="0.1.0")

        def setup() -> float:
            return 1.0

        @app.device("motor", init=setup)
        async def motor(ctx: DeviceContext) -> None: ...

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]
        assert dev["has_init"] is True
        assert ["ctx", "DeviceContext"] in dev["dependencies"]

    def test_command_has_init_and_dependencies(self) -> None:
        """A command with init= and injected params shows both fields."""
        app = cosalette.App(name="test", version="0.1.0")

        def setup() -> float:
            return 1.0

        @app.command("valve", init=setup)
        async def valve(payload: str) -> dict[str, object] | None:
            return {"state": payload}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]
        assert cmd["has_init"] is True


class TestFullAppSnapshot:
    """Full app snapshot JSON round-trip.

    Technique: Integration Testing — verifying the snapshot is
    JSON-serializable and survives a round-trip.
    """

    def test_json_round_trip(self) -> None:
        """A full app snapshot is JSON-serializable and round-trips."""
        app = cosalette.App(
            name="myapp",
            version="1.2.3",
            description="My IoT bridge",
            store=cosalette.MemoryStore(),
        )

        @app.device("motor")
        async def motor(ctx: DeviceContext) -> None: ...

        @app.telemetry(
            "temp",
            interval=5.0,
            publish=cosalette.Every(seconds=10.0) | cosalette.OnChange(),
            persist=cosalette.SaveOnPublish(),
            group="sensors",
        )
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        @app.telemetry("humidity", interval=lambda settings: 10.0)
        async def humidity() -> dict[str, object] | None:
            return {"humidity": 45.0}

        @app.command("light")
        async def light(topic: str, payload: str) -> dict[str, object] | None:
            return {"state": "on"}

        app.adapter(cosalette.MqttPort, cosalette.NullMqttClient)

        snap = build_registry_snapshot(app)

        # Must be JSON-serializable
        json_str = json.dumps(snap)
        assert isinstance(json_str, str)

        # Round-trip
        restored = json.loads(json_str)
        assert restored["app"]["name"] == "myapp"
        assert restored["app"]["version"] == "1.2.3"
        assert restored["app"]["description"] == "My IoT bridge"
        assert len(restored["devices"]) == 1
        assert len(restored["telemetry"]) == 2
        assert len(restored["commands"]) == 1
        assert len(restored["adapters"]) == 1

        # Deferred interval survives round-trip
        humidity_entry = next(
            t for t in restored["telemetry"] if t["name"] == "humidity"
        )
        assert humidity_entry["interval"] == "<deferred>"

        # Strategy description survives round-trip
        temp_entry = next(t for t in restored["telemetry"] if t["name"] == "temp")
        assert "AnyStrategy" in temp_entry["strategy"]
