"""Unit tests for the cosalette registry introspection module.

Covers: ``build_registry_snapshot()`` across empty apps, devices,
telemetry (with intervals, strategies, persist, groups), commands,
streams, periodic tasks, adapters, composite strategies/policies, and JSON
round-trip.

Test Techniques Used:
    - Specification-based Testing: Output shape and field values per
      registration type.
    - Boundary-value Testing: Zero-element (empty app) case.
    - Round-trip Testing: JSON serialization/deserialization fidelity.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import pytest

import cosalette
from cosalette._context import DeviceContext
from cosalette._mcp._introspect import build_registry_snapshot

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)

        assert len(snap["devices"]) == 1
        dev = snap["devices"][0]
        assert dev["name"] == "sensor"
        assert dev["type"] == "device"
        assert "sensor" in dev["func"]
        assert dev["is_root"] is False
        assert dev["has_init"] is False
        assert isinstance(dev["dependencies"], list)

    def test_device_state_and_payload_model_in_snapshot(self) -> None:
        """state_model and payload_model names appear in device snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        class DeviceState:
            pass

        class DevicePayload:
            pass

        @app.device("sensor", state_model=DeviceState, payload_model=DevicePayload)
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]
        assert dev["state_model"] == "DeviceState"
        assert dev["payload_model"] == "DevicePayload"

    def test_device_model_none_when_not_set(self) -> None:
        """state_model and payload_model are None when not provided."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]
        assert dev["state_model"] is None
        assert dev["payload_model"] is None


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
        assert tel["is_root"] is False


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


class TestTelemetrySettingRefInterval:
    """Telemetry with a SettingRef interval (inspectable deferred resolution).

    Technique: Specification-based Testing — verifying that SettingRef
    intervals show the field name instead of "<deferred>".
    """

    def test_setting_ref_interval_shows_field_name(self) -> None:
        """A SettingRef interval shows the field name in introspection."""
        import cosalette

        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry(
            "temp", interval=cosalette.setting_ref("mqtt.reconnect_interval")
        )
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = cosalette.build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["interval"] == "mqtt.reconnect_interval"


class TestTelemetryTimeout:
    """Telemetry timeout field serialization.

    Technique: Specification-based Testing — verifying all four timeout
    states are JSON-safe and use the expected sentinel strings.
    """

    def test_concrete_float_timeout_passthrough(self) -> None:
        """A concrete float timeout is preserved as a float in the snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=30.0, timeout=25.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["timeout"] == 25.0
        assert isinstance(snap["telemetry"][0]["timeout"], float)

    def test_unset_timeout_serializes_as_auto(self) -> None:
        """An omitted timeout (sentinel) serializes to 'auto'."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=30.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["timeout"] == "auto"

    def test_none_timeout_serializes_as_disabled(self) -> None:
        """An explicit timeout=None serializes to 'disabled'."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=30.0, timeout=None)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["timeout"] == "disabled"

    def test_callable_timeout_serializes_as_deferred(self) -> None:
        """A callable timeout serializes to '<deferred>'."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=30.0, timeout=lambda settings: 20.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        assert snap["telemetry"][0]["timeout"] == "<deferred>"


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
        assert cmd["is_root"] is False
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
        async def motor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

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
        async def motor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

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


class TestFormatRegistryJson:
    """format_registry_json output tests.

    Technique: Specification-based Testing — verifying JSON output
    structure and content.
    """

    def test_json_output_is_valid_json(self) -> None:
        """format_registry_json returns valid JSON matching the snapshot."""
        import json

        app = cosalette.App(name="jsonapp", version="2.0.0", description="JSON test")

        @app.device("sensor")
        async def sensor(ctx: cosalette.DeviceContext) -> AsyncIterator[None]:
            yield

        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_json(snapshot)
        parsed = json.loads(result)

        assert parsed == snapshot

    def test_json_output_is_indented(self) -> None:
        """format_registry_json returns indented (pretty) JSON."""
        app = cosalette.App(name="jsonapp", version="1.0.0")
        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_json(snapshot)

        assert "\n" in result  # multi-line
        assert "  " in result  # indented


class TestFormatRegistryTable:
    """format_registry_table output tests.

    Technique: Specification-based Testing — verifying plain text table
    structure and content for various registration configurations.
    """

    def test_empty_app_shows_header_only(self) -> None:
        """Empty app shows app header but no device sections."""
        app = cosalette.App(name="empty", version="0.1.0", description="Empty app")
        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_table(snapshot)

        assert "empty v0.1.0" in result
        assert "Devices" not in result
        assert "Telemetry" not in result

    def test_device_section_appears(self) -> None:
        """App with a device shows the Devices section."""
        app = cosalette.App(name="devapp", version="1.0.0")

        @app.device("heater")
        async def heater(ctx: cosalette.DeviceContext) -> AsyncIterator[None]:
            yield

        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_table(snapshot)

        assert "Devices" in result
        assert "heater" in result

    def test_telemetry_section_shows_interval(self) -> None:
        """App with telemetry shows interval in the table."""
        app = cosalette.App(name="telapp", version="1.0.0")

        @app.telemetry("temp", interval=5.0)
        async def temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
            return {"value": 42}

        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_table(snapshot)

        assert "Telemetry" in result
        assert "temp" in result
        assert "5.0" in result

    def test_skips_empty_sections(self) -> None:
        """Sections with no registrations are omitted entirely."""
        app = cosalette.App(name="partial", version="1.0.0")

        @app.device("only_device")
        async def only_device(
            ctx: cosalette.DeviceContext,
        ) -> AsyncIterator[None]:
            yield

        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_table(snapshot)

        assert "Devices" in result
        assert "Telemetry" not in result
        assert "Commands" not in result
        assert "Adapters" not in result

    def test_boolean_fields_use_checkmark(self) -> None:
        """Root=True renders as ✓, Root=False as —."""
        app = cosalette.App(name="boolapp", version="1.0.0")

        @app.device()  # name=None → root device (is_root=True)
        async def root_dev(ctx: cosalette.DeviceContext) -> AsyncIterator[None]:
            yield

        snapshot = cosalette.build_registry_snapshot(app)
        result = cosalette.format_registry_table(snapshot)

        assert "✓" in result

    def test_devices_section_shows_trigger_and_min_interval(self) -> None:
        """A local-trigger device renders its trigger source and min interval."""
        app = cosalette.App(name="trigapp", version="1.0.0")

        @app.device("pump", triggerable="local", min_interval=2.5)
        async def pump(
            ctx: cosalette.DeviceContext, trigger: cosalette.DeviceTrigger
        ) -> AsyncIterator[None]:
            yield

        result = cosalette.format_registry_table(cosalette.build_registry_snapshot(app))

        assert "Trigger" in result
        assert "Min interval" in result
        assert "local" in result
        assert "2.5" in result

    def test_telemetry_section_shows_trigger_and_min_interval(self) -> None:
        """A local-trigger telemetry entry renders its trigger and min interval."""
        app = cosalette.App(name="trigtel", version="1.0.0")

        @app.telemetry("sensor", interval=30.0, triggerable="local", min_interval=1.5)
        async def sensor(
            ctx: cosalette.DeviceContext, trigger: cosalette.DeviceTrigger
        ) -> dict[str, object]:
            return {"value": 1}

        result = cosalette.format_registry_table(cosalette.build_registry_snapshot(app))

        assert "Trigger" in result
        assert "Min interval" in result
        assert "local" in result
        assert "1.5" in result

    def test_non_triggerable_device_shows_em_dash_trigger_columns(self) -> None:
        """A plain device shows an em-dash in both trigger columns."""
        app = cosalette.App(name="plainapp", version="1.0.0")

        @app.device("valve")
        async def valve(ctx: cosalette.DeviceContext) -> AsyncIterator[None]:
            yield

        result = cosalette.format_registry_table(cosalette.build_registry_snapshot(app))
        # The Devices row for a non-triggerable device carries the em-dash
        # placeholder (\u2014) that _none() renders for a None trigger_source.
        valve_row = next(line for line in result.splitlines() if "valve" in line)
        assert "\u2014" in valve_row


class TestDeviceEnabled:
    """Device with enabled specifications.

    Technique: Specification-based Testing — verifying enabled field
    introspection for bool, callable, and SettingRef enabled specs.
    """

    def test_bool_enabled_true(self) -> None:
        """A device with enabled=True shows True in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor", enabled=True)
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]

        assert dev["enabled"] is True

    def test_callable_enabled_becomes_deferred(self) -> None:
        """A device with callable enabled shows '<deferred>' in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor", enabled=lambda settings: True)
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]

        assert dev["enabled"] == "<deferred>"

    def test_setting_ref_enabled_shows_field_name(self) -> None:
        """A device with SettingRef enabled shows the field name in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor", enabled=cosalette.setting_ref("devices.sensor_enabled"))
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]

        assert dev["enabled"] == "devices.sensor_enabled"

    def test_default_enabled_is_true(self) -> None:
        """A device without explicit enabled defaults to True."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)
        dev = snap["devices"][0]

        assert dev["enabled"] is True


class TestTelemetryEnabled:
    """Telemetry with enabled specifications.

    Technique: Specification-based Testing — verifying enabled field
    introspection for bool, callable, and SettingRef enabled specs.
    """

    def test_bool_enabled_true(self) -> None:
        """A telemetry with enabled=True shows True in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=5.0, enabled=True)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["enabled"] is True

    def test_callable_enabled_becomes_deferred(self) -> None:
        """A telemetry with callable enabled shows '<deferred>' in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=5.0, enabled=lambda settings: True)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["enabled"] == "<deferred>"

    def test_setting_ref_enabled_shows_field_name(self) -> None:
        """A telemetry with SettingRef enabled shows the field name in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry(
            "temp",
            interval=5.0,
            enabled=cosalette.setting_ref("telemetry.temp_enabled"),
        )
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["enabled"] == "telemetry.temp_enabled"

    def test_default_enabled_is_true(self) -> None:
        """A telemetry without explicit enabled defaults to True."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("temp", interval=5.0)
        async def temp() -> dict[str, object] | None:
            return {"temp": 22.5}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["enabled"] is True


class TestCommandEnabled:
    """Command with enabled specifications.

    Technique: Specification-based Testing — verifying enabled field
    introspection for bool, callable, and SettingRef enabled specs.
    """

    def test_bool_enabled_true(self) -> None:
        """A command with enabled=True shows True in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("light", enabled=True)
        async def light(payload: str) -> dict[str, object] | None:
            return {"state": payload}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["enabled"] is True

    def test_callable_enabled_becomes_deferred(self) -> None:
        """A command with callable enabled shows '<deferred>' in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("light", enabled=lambda settings: True)
        async def light(payload: str) -> dict[str, object] | None:
            return {"state": payload}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["enabled"] == "<deferred>"

    def test_setting_ref_enabled_shows_field_name(self) -> None:
        """A command with SettingRef enabled shows the field name in introspection."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("light", enabled=cosalette.setting_ref("commands.light_enabled"))
        async def light(payload: str) -> dict[str, object] | None:
            return {"state": payload}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["enabled"] == "commands.light_enabled"

    def test_default_enabled_is_true(self) -> None:
        """A command without explicit enabled defaults to True."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("light")
        async def light(payload: str) -> dict[str, object] | None:
            return {"state": payload}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["enabled"] is True


# ---------------------------------------------------------------------------
# Tests for contract metadata introspection (Phase 2)
# ---------------------------------------------------------------------------


class TestTelemetryContractIntrospection:
    """Contract metadata appears in telemetry introspection snapshot.

    Test Techniques Used:
        - Specification-based Testing: metadata field serialization
        - Equivalence Partitioning: with/without metadata scenarios
    """

    def test_telemetry_with_contract_metadata_in_snapshot(self) -> None:
        """Telemetry with contract metadata appears in registry snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        class SensorData:
            """Example state model."""

            pass

        @app.telemetry(
            "sensor",
            interval=30,
            summary="Temperature and humidity readings",
            state_model=SensorData,
            payload_model=SensorData,  # For triggerable
            behavior=["polls I2C", "filters noise"],
            effects=["triggers alerts"],
        )
        async def sensor():
            return {"temp": 25.0}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["summary"] == "Temperature and humidity readings"
        assert tel["state_model"] == "SensorData"
        assert tel["payload_model"] == "SensorData"
        assert tel["behavior"] == ["polls I2C", "filters noise"]
        assert tel["effects"] == ["triggers alerts"]

    def test_telemetry_without_metadata_shows_none(self) -> None:
        """Telemetry without contract metadata shows None for new fields."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("sensor", interval=30)
        async def sensor() -> dict[str, object] | None:
            return {"temp": 25.0}

        snap = build_registry_snapshot(app)
        tel = snap["telemetry"][0]

        assert tel["summary"] is None
        assert tel["state_model"] is None
        assert tel["payload_model"] is None
        assert tel["behavior"] is None
        assert tel["effects"] is None


class TestCommandContractIntrospection:
    """Contract metadata appears in command introspection snapshot.

    Test Techniques Used:
        - Specification-based Testing: metadata field serialization
        - Equivalence Partitioning: with/without metadata scenarios
    """

    def test_command_with_contract_metadata_in_snapshot(self) -> None:
        """Command with contract metadata appears in registry snapshot."""
        app = cosalette.App(name="test", version="0.1.0")

        class ValvePayload:
            """Example payload model."""

            pass

        @app.command(
            "valve",
            summary="Controls water valve",
            state_model=ValvePayload,
            payload_model=ValvePayload,
            behavior=["validates flow limits"],
            effects=["opens/closes valve", "logs action"],
        )
        async def valve():
            return {"state": "open"}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["summary"] == "Controls water valve"
        assert cmd["state_model"] == "ValvePayload"
        assert cmd["payload_model"] == "ValvePayload"
        assert cmd["behavior"] == ["validates flow limits"]
        assert cmd["effects"] == ["opens/closes valve", "logs action"]

    def test_command_without_metadata_shows_none(self) -> None:
        """Command without contract metadata shows None for new fields."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.command("valve")
        async def valve() -> dict[str, object] | None:
            return {"state": "open"}

        snap = build_registry_snapshot(app)
        cmd = snap["commands"][0]

        assert cmd["summary"] is None
        assert cmd["state_model"] is None
        assert cmd["payload_model"] is None
        assert cmd["behavior"] is None
        assert cmd["effects"] is None


class TestStreamIntrospection:
    """Stream registrations appear in the snapshot, the table, AND AsyncAPI (ADR-054).

    Since ADR-054, ``@app.stream`` emits a channel with
    ``x-cosalette-archetype: stream`` into the AsyncAPI document (same state
    channel as a device: ``{app}/{name}/state``).  Contract metadata
    (``summary``/``state_model``/``behavior``/``effects``) is therefore
    available via both the registry snapshot AND AsyncAPI.

    Test Techniques Used:
        - Specification-based Testing: snapshot key presence and field values.
        - Equivalence Partitioning: metadata declared vs. omitted.
        - Round-trip Testing: the new sections stay JSON-serializable.
    """

    class _Item:
        """Stream item type."""

    class _State:
        """Stream state contract."""

    def test_snapshot_has_streams_key_when_empty(self) -> None:
        """The streams key exists even with no stream registrations."""
        app = cosalette.App(name="empty", version="1.0.0")

        snapshot = build_registry_snapshot(app)

        assert snapshot["streams"] == []

    def test_stream_metadata_appears_in_snapshot(self) -> None:
        """summary/state_model/behavior/effects reach the snapshot."""
        # Arrange
        app = cosalette.App(name="strapp", version="1.0.0")

        @app.stream(
            "rx",
            summary="Reads sensor frames",
            state_model=self._State,
            behavior=["decodes frames"],
            effects=["publishes rx/state"],
        )
        async def rx(
            stream: cosalette.Stream[TestStreamIntrospection._Item],
        ) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        # Act
        entry = build_registry_snapshot(app)["streams"][0]

        # Assert
        assert entry["name"] == "rx"
        assert entry["type"] == "stream"
        assert entry["summary"] == "Reads sensor frames"
        assert entry["state_model"] == "_State"
        assert entry["behavior"] == ["decodes frames"]
        assert entry["effects"] == ["publishes rx/state"]

    def test_stream_buffer_settings_appear_in_snapshot(self) -> None:
        """maxsize and backpressure are introspectable."""
        app = cosalette.App(name="strapp", version="1.0.0")

        @app.stream("rx", maxsize=5, backpressure="drop_oldest")
        async def rx(
            stream: cosalette.Stream[TestStreamIntrospection._Item],
        ) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        entry = build_registry_snapshot(app)["streams"][0]

        assert entry["maxsize"] == 5
        assert entry["backpressure"] == "drop_oldest"

    def test_stream_without_metadata_reports_none(self) -> None:
        """Undeclared metadata is None, not missing or empty-string."""
        app = cosalette.App(name="strapp", version="1.0.0")

        @app.stream("rx")
        async def rx(
            stream: cosalette.Stream[TestStreamIntrospection._Item],
        ) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        entry = build_registry_snapshot(app)["streams"][0]

        assert entry["summary"] is None
        assert entry["state_model"] is None
        assert entry["behavior"] is None
        assert entry["effects"] is None

    def test_streams_section_appears_in_table(self) -> None:
        """format_registry_table renders a Streams section."""
        app = cosalette.App(name="strapp", version="1.0.0")

        @app.stream("rx", state_model=self._State)
        async def rx(
            stream: cosalette.Stream[TestStreamIntrospection._Item],
        ) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        result = cosalette.format_registry_table(build_registry_snapshot(app))

        assert "Streams" in result
        assert "rx" in result
        assert "_State" in result

    def test_streams_section_omitted_when_no_streams(self) -> None:
        """Empty sections stay omitted (existing table convention)."""
        app = cosalette.App(name="devapp", version="1.0.0")

        @app.device("heater")
        async def heater(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        result = cosalette.format_registry_table(build_registry_snapshot(app))

        assert "Streams" not in result

    def test_stream_entry_is_json_serializable(self) -> None:
        """The new section survives a JSON round-trip.

        Technique: Round-trip Testing.
        """
        app = cosalette.App(name="strapp", version="1.0.0")

        @app.stream("rx", summary="s", state_model=self._State, behavior=["b"])
        async def rx(
            stream: cosalette.Stream[TestStreamIntrospection._Item],
        ) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        restored = json.loads(json.dumps(build_registry_snapshot(app)))

        assert restored["streams"][0]["state_model"] == "_State"
        assert restored["streams"][0]["summary"] == "s"


class TestPeriodicIntrospection:
    """Periodic registrations appear in the snapshot and the table.

    Periodic tasks have no MQTT presence by design (ADR-041), so they carry only
    ``summary`` and ``behavior`` — no ``state_model``, ``payload_model``, or
    ``effects``.

    Test Techniques Used:
        - Specification-based Testing: snapshot key presence and field values.
        - Equivalence Partitioning: metadata declared vs. omitted.
    """

    def test_snapshot_has_periodic_key_when_empty(self) -> None:
        """The periodic key exists even with no periodic registrations."""
        app = cosalette.App(name="empty", version="1.0.0")

        snapshot = build_registry_snapshot(app)

        assert snapshot["periodic"] == []

    def test_periodic_metadata_appears_in_snapshot(self) -> None:
        """summary and behavior reach the snapshot."""
        # Arrange
        app = cosalette.App(name="peri", version="1.0.0")

        @app.periodic(
            interval=60.0,
            summary="Refreshes the cache",
            behavior=["evicts stale entries"],
        )
        async def refresh() -> None: ...

        # Act
        entry = build_registry_snapshot(app)["periodic"][0]

        # Assert
        assert entry["name"] == "refresh"
        assert entry["type"] == "periodic"
        assert entry["interval"] == 60.0
        assert entry["summary"] == "Refreshes the cache"
        assert entry["behavior"] == ["evicts stale entries"]

    def test_periodic_carries_no_mqtt_contract_fields(self) -> None:
        """No state_model/payload_model/effects keys (ADR-041 — no MQTT presence)."""
        app = cosalette.App(name="peri", version="1.0.0")

        @app.periodic(interval=60.0)
        async def refresh() -> None: ...

        entry = build_registry_snapshot(app)["periodic"][0]

        assert "state_model" not in entry
        assert "payload_model" not in entry
        assert "effects" not in entry

    def test_periodic_without_metadata_reports_none(self) -> None:
        """Undeclared metadata is None."""
        app = cosalette.App(name="peri", version="1.0.0")

        @app.periodic(interval=60.0)
        async def refresh() -> None: ...

        entry = build_registry_snapshot(app)["periodic"][0]

        assert entry["summary"] is None
        assert entry["behavior"] is None

    def test_periodic_section_appears_in_table(self) -> None:
        """format_registry_table renders a Periodic section with the summary."""
        app = cosalette.App(name="peri", version="1.0.0")

        @app.periodic(interval=60.0, summary="Refreshes the cache")
        async def refresh() -> None: ...

        result = cosalette.format_registry_table(build_registry_snapshot(app))

        assert "Periodic" in result
        assert "refresh" in result
        assert "Refreshes the cache" in result

    def test_periodic_section_omitted_when_no_tasks(self) -> None:
        """Empty sections stay omitted."""
        app = cosalette.App(name="devapp", version="1.0.0")

        @app.device("heater")
        async def heater(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        result = cosalette.format_registry_table(build_registry_snapshot(app))

        assert "Periodic" not in result

    def test_deferred_periodic_interval_is_described(self) -> None:
        """A settings-derived interval renders as <deferred>, not a repr."""
        app = cosalette.App(name="peri", version="1.0.0")

        @app.periodic(interval=lambda s: 30.0)
        async def refresh() -> None: ...

        entry = build_registry_snapshot(app)["periodic"][0]

        assert entry["interval"] == "<deferred>"


class TestMinIntervalInSnapshot:
    """The ADR-066 storm throttle is visible to introspection.

    Technique: Specification-based Testing — the snapshot is the
    contract the MCP tools and AI-content surfaces read, so the field
    must appear for both archetypes, set and unset.
    """

    def test_telemetry_snapshot_exposes_min_interval(self) -> None:
        """A throttled telemetry entity reports its min_interval."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("sensor", interval=30.0, triggerable="local", min_interval=2.5)
        async def sensor() -> dict[str, object]:
            return {}

        entry = build_registry_snapshot(app)["telemetry"][0]

        assert entry["min_interval"] == 2.5
        assert entry["triggerable"] is True
        assert entry["trigger_source"] == "local"

    def test_device_snapshot_exposes_min_interval(self) -> None:
        """A throttled device reports its min_interval."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.device("gadget", triggerable="local", min_interval=5.0)
        async def gadget(trigger: cosalette.DeviceTrigger) -> AsyncIterator[None]:
            await trigger.wait()
            yield

        entry = build_registry_snapshot(app)["devices"][0]

        assert entry["min_interval"] == 5.0
        assert entry["triggerable"] is True
        assert entry["trigger_source"] == "local"

    def test_min_interval_is_none_when_unthrottled(self) -> None:
        """An untouched registration reports None for both archetypes."""
        app = cosalette.App(name="test", version="0.1.0")

        @app.telemetry("sensor", interval=30.0)
        async def sensor() -> dict[str, object]:
            return {}

        @app.device("gadget")
        async def gadget(ctx: DeviceContext) -> AsyncIterator[None]:
            yield

        snap = build_registry_snapshot(app)

        assert snap["telemetry"][0]["triggerable"] is False
        assert snap["telemetry"][0]["trigger_source"] is None
        assert snap["devices"][0]["triggerable"] is False
        assert snap["devices"][0]["trigger_source"] is None
        assert snap["telemetry"][0]["min_interval"] is None
        assert snap["devices"][0]["min_interval"] is None
