"""Tests for App registration — contract metadata fields.

Covers: summary, state_model, payload_model, behavior, effects on
@app.telemetry, @app.command, @app.device, and their imperative equivalents.

Test Techniques Used:
    - Specification-based Testing: Contract metadata fields (summary,
      state_model, payload_model, behavior) stored and retrievable.
    - Contract Testing: Verifies the contract metadata API matches the
      AsyncAPI document generation contract (ADR-011).
    - Equivalence Partitioning: metadata present vs. absent (None defaults)
      for each registration type.
"""

from __future__ import annotations

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests for contract metadata (Phase 2)
# ---------------------------------------------------------------------------


class TestContractMetadata:
    """Validate contract metadata fields on telemetry and command registrations.

    Test Techniques Used:
        - Specification-based Testing: metadata field presence and values
        - Equivalence Partitioning: with/without metadata scenarios
    """

    def test_telemetry_with_contract_metadata(self) -> None:
        """@app.telemetry can accept contract metadata parameters."""
        app = App(name="test", version="1.0.0")

        class SensorReading:
            temperature: float
            humidity: float

        @app.telemetry(
            "sensor",
            interval=30,
            summary="Temperature and humidity sensor",
            state_model=SensorReading,
            payload_model=SensorReading,  # For triggerable telemetry
            behavior=["reads I2C", "filters outliers"],
            effects=["triggers alerts"],
        )
        async def sensor() -> dict[str, object]:
            return {"temp": 25.0, "humidity": 60.0}

        telemetry_regs = app.telemetry_registrations
        assert len(telemetry_regs) == 1

        reg = telemetry_regs[0]
        assert reg.summary == "Temperature and humidity sensor"
        assert reg.state_model is SensorReading
        assert reg.payload_model is SensorReading
        assert reg.behavior == ["reads I2C", "filters outliers"]
        assert reg.effects == ["triggers alerts"]

    def test_command_with_contract_metadata(self) -> None:
        """@app.command can accept contract metadata parameters."""
        app = App(name="test", version="1.0.0")

        class ValveCommand:
            action: str
            flow_rate: float

        @app.command(
            "valve",
            summary="Controls irrigation valve",
            state_model=ValveCommand,
            payload_model=ValveCommand,
            behavior=["validates constraints"],
            effects=["mutates valve state", "logs to audit"],
        )
        async def valve() -> dict[str, object]:
            return {"status": "opened"}

        commands = app.commands
        assert len(commands) == 1

        reg = commands[0]
        assert reg.summary == "Controls irrigation valve"
        assert reg.state_model is ValveCommand
        assert reg.payload_model is ValveCommand
        assert reg.behavior == ["validates constraints"]
        assert reg.effects == ["mutates valve state", "logs to audit"]

    def test_telemetry_without_metadata_unchanged(self) -> None:
        """@app.telemetry without metadata should work exactly as before."""
        app = App(name="test", version="1.0.0")

        @app.telemetry("sensor", interval=30)
        async def sensor() -> dict[str, object]:
            return {"temp": 25.0}

        telemetry_regs = app.telemetry_registrations
        assert len(telemetry_regs) == 1

        reg = telemetry_regs[0]
        assert reg.summary is None
        assert reg.state_model is None
        assert reg.payload_model is None
        assert reg.behavior is None
        assert reg.effects is None

    def test_command_without_metadata_unchanged(self) -> None:
        """@app.command without metadata should work exactly as before."""
        app = App(name="test", version="1.0.0")

        @app.command("valve")
        async def valve() -> dict[str, object]:
            return {"status": "opened"}

        commands = app.commands
        assert len(commands) == 1

        reg = commands[0]
        assert reg.summary is None
        assert reg.state_model is None
        assert reg.payload_model is None
        assert reg.behavior is None
        assert reg.effects is None

    def test_add_telemetry_with_metadata(self) -> None:
        """add_telemetry() imperative API accepts contract metadata."""
        app = App(name="test", version="1.0.0")

        class TempReading:
            celsius: float

        async def temp_func() -> dict[str, object]:
            return {"celsius": 22.0}

        app.add_telemetry(
            "temp",
            temp_func,
            interval=60,
            summary="Temperature sensor reading",
            state_model=TempReading,
            payload_model=TempReading,  # For triggerable
            behavior=["polls sensor", "converts units"],
            effects=["triggers HVAC"],
        )

        telemetry_regs = app.telemetry_registrations
        assert len(telemetry_regs) == 1

        reg = telemetry_regs[0]
        assert reg.summary == "Temperature sensor reading"
        assert reg.state_model is TempReading
        assert reg.payload_model is TempReading
        assert reg.behavior == ["polls sensor", "converts units"]
        assert reg.effects == ["triggers HVAC"]

    def test_add_command_with_metadata(self) -> None:
        """add_command() imperative API accepts contract metadata."""
        app = App(name="test", version="1.0.0")

        class SwitchCommand:
            state: bool

        async def switch_func() -> dict[str, object]:
            return {"active": True}

        app.add_command(
            "switch",
            switch_func,
            summary="Toggle switch state",
            state_model=SwitchCommand,
            payload_model=SwitchCommand,
            behavior=["validates input"],
            effects=["changes relay state"],
        )

        commands = app.commands
        assert len(commands) == 1

        reg = commands[0]
        assert reg.summary == "Toggle switch state"
        assert reg.state_model is SwitchCommand
        assert reg.payload_model is SwitchCommand
        assert reg.behavior == ["validates input"]
        assert reg.effects == ["changes relay state"]

    def test_empty_behavior_list_stored_as_empty(self) -> None:
        """behavior=[] (empty list) is stored as-is, not coerced to None."""
        app = App(name="test", version="1.0.0")

        @app.telemetry("sensor", interval=60, behavior=[], effects=[])
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        reg = app.telemetry_registrations[0]
        assert reg.behavior == []
        assert reg.effects == []

    def test_empty_behavior_list_on_command(self) -> None:
        """behavior=[] and effects=[] on @app.command are stored as-is."""
        app = App(name="test", version="1.0.0")

        @app.command("ctrl", behavior=[], effects=[])
        async def ctrl() -> dict[str, object]:
            return {}

        reg = app.commands[0]
        assert reg.behavior == []
        assert reg.effects == []


class TestDeviceContractMetadata:
    """Tests for contract metadata on @app.device() and add_device().

    Test Techniques Used:
        - Specification-based Testing: metadata field presence and values
        - Equivalence Partitioning: with/without metadata scenarios
    """

    def test_device_decorator_with_metadata(self) -> None:
        """@app.device() stores summary, behavior, effects."""
        app = App("test", "1.0.0")

        @app.device(
            "sensor",
            summary="Reads sensor data",
            behavior=["polls hardware", "publishes state"],
            effects=["updates sensor/state"],
        )
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app.devices[0]
        assert reg.summary == "Reads sensor data"
        assert reg.behavior == ["polls hardware", "publishes state"]
        assert reg.effects == ["updates sensor/state"]

    def test_device_decorator_metadata_defaults_to_none(self) -> None:
        """@app.device() without metadata has None fields."""
        app = App("test", "1.0.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app.devices[0]
        assert reg.summary is None
        assert reg.behavior is None
        assert reg.effects is None

    def test_add_device_with_metadata(self) -> None:
        """add_device() stores summary, behavior, effects."""
        app = App("test", "1.0.0")

        async def cover(ctx: DeviceContext) -> None:
            pass

        app.add_device(
            "cover",
            cover,
            summary="Controls cover position",
            behavior=["subscribes to set topic", "sends motor commands"],
            effects=["updates cover/state"],
        )

        reg = app.devices[0]
        assert reg.summary == "Controls cover position"
        assert reg.behavior == ["subscribes to set topic", "sends motor commands"]
        assert reg.effects == ["updates cover/state"]

    def test_device_metadata_in_snapshot(self) -> None:
        """Device metadata appears in build_registry_snapshot() output."""
        from cosalette._mcp._introspect import build_registry_snapshot

        app = App("test", "1.0.0")

        @app.device(
            "sensor",
            summary="Test sensor",
            behavior=["step1"],
            effects=["side-effect1"],
        )
        async def sensor(ctx: DeviceContext) -> None:
            pass

        snapshot = build_registry_snapshot(app)
        device_desc = snapshot["devices"][0]
        assert device_desc["summary"] == "Test sensor"
        assert device_desc["behavior"] == ["step1"]
        assert device_desc["effects"] == ["side-effect1"]

    def test_device_deferred_enabled_preserves_metadata(self) -> None:
        """Deferred (callable-enabled) @app.device() preserves contract metadata.

        Regression guard: _register_deferred_device forwards summary/behavior/effects
        into the stored _DeviceRegistration so metadata is not lost on the deferred
        path.
        """
        app = App("test", "1.0.0")

        @app.device(
            "sensor",
            enabled=lambda s: True,
            summary="Deferred sensor",
            behavior=["reads data"],
            effects=["publishes sensor/state"],
        )
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app._devices[0]  # noqa: SLF001
        assert callable(reg.enabled_spec)
        assert reg.summary == "Deferred sensor"
        assert reg.behavior == ["reads data"]
        assert reg.effects == ["publishes sensor/state"]

    def test_device_decorator_with_state_and_payload_model(self) -> None:
        """@app.device() stores state_model and payload_model."""
        app = App("test", "1.0.0")

        class SensorState:
            pass

        class SensorPayload:
            pass

        @app.device(
            "sensor",
            state_model=SensorState,
            payload_model=SensorPayload,
        )
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app.devices[0]
        assert reg.state_model is SensorState
        assert reg.payload_model is SensorPayload

    def test_device_decorator_model_defaults_to_none(self) -> None:
        """@app.device() without models has None state_model and payload_model."""
        app = App("test", "1.0.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app.devices[0]
        assert reg.state_model is None
        assert reg.payload_model is None

    def test_add_device_with_state_and_payload_model(self) -> None:
        """add_device() stores state_model and payload_model."""
        app = App("test", "1.0.0")

        class CoverState:
            pass

        class CoverPayload:
            pass

        async def cover(ctx: DeviceContext) -> None:
            pass

        app.add_device(
            "cover",
            cover,
            state_model=CoverState,
            payload_model=CoverPayload,
        )

        reg = app.devices[0]
        assert reg.state_model is CoverState
        assert reg.payload_model is CoverPayload

    def test_device_deferred_enabled_preserves_models(self) -> None:
        """Deferred @app.device() preserves state_model and payload_model."""
        app = App("test", "1.0.0")

        class MyState:
            pass

        class MyPayload:
            pass

        @app.device(
            "sensor",
            enabled=lambda s: True,
            state_model=MyState,
            payload_model=MyPayload,
        )
        async def sensor(ctx: DeviceContext) -> None:
            pass

        reg = app._devices[0]  # noqa: SLF001
        assert reg.state_model is MyState
        assert reg.payload_model is MyPayload
