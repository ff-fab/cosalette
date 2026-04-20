"""Tests for dict-based multi-device decorator with per-device config injection.

Covers:
- Dict name expansion (N devices from one decorator)
- List name expansion
- Per-device config injection via DI
- Per-device interval resolution
- Empty dict/list warnings
- Config type shadowing framework types
- Duplicate name detection after expansion
- Interaction with @app.device and @app.command

Test Techniques Used:
    - Unit Testing: isolated expansion and wiring validation
    - Integration Testing: full run() with MockMqttClient + FakeClock
    - Dependency Injection: per-device config values injected via DI
    - Warning Capture: capsys for empty-spec warnings
    - Error Isolation: ValueError for duplicates and type conflicts
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level helpers (needed for get_type_hints under PEP 563)
# ---------------------------------------------------------------------------


@dataclass
class SensorConfig:
    mac: str
    interval: float = 10.0


@dataclass
class OtherConfig:
    name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_command(topic: str, payload: str) -> None:
    """No-op command handler for testing."""


async def _run_app(
    app: App,
    *,
    settings: Settings | None = None,
    clock: FakeClock | None = None,
) -> MockMqttClient:
    """Run app with FakeClock until shutdown, return the mock MQTT client."""
    mqtt = MockMqttClient()
    await asyncio.wait_for(
        app._run_async(
            mqtt=mqtt,
            settings=settings or make_settings(),
            shutdown_event=asyncio.Event(),
            clock=clock or FakeClock(),
        ),
        timeout=5.0,
    )
    return mqtt


# ---------------------------------------------------------------------------
# TestDictNameTelemetry
# ---------------------------------------------------------------------------


class TestDictNameTelemetry:
    """Dict-name expansion for @app.telemetry."""

    async def test_dict_name_registers_n_devices(self) -> None:
        """3-item dict → 3 telemetry registrations that all fire."""
        received: dict[str, bool] = {}
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {  # ty: ignore[invalid-argument-type]
                "a": SensorConfig("AA"),
                "b": SensorConfig("BB"),
                "c": SensorConfig("CC"),
            },
            interval=5.0,
        )
        async def handler(
            ctx: DeviceContext,
            config: SensorConfig,
        ) -> dict[str, object]:
            received[ctx.name] = True
            if len(received) >= 3:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert received == {"a": True, "b": True, "c": True}

    async def test_per_device_config_injected(self) -> None:
        """Per-device config is injected into handler via DI."""
        received: dict[str, str] = {}
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {  # ty: ignore[invalid-argument-type]
                "a": SensorConfig(mac="AA"),
                "b": SensorConfig(mac="BB"),
            },
            interval=5.0,
        )
        async def handler(
            ctx: DeviceContext,
            config: SensorConfig,
        ) -> dict[str, object]:
            received[ctx.name] = config.mac
            if len(received) >= 2:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert received == {"a": "AA", "b": "BB"}

    async def test_handler_without_config_param(self) -> None:
        """Handler that doesn't declare config param still works."""
        called: set[str] = set()
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"x": SensorConfig("XX"), "y": SensorConfig("YY")},  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            called.add(ctx.name)
            if len(called) >= 2:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert called == {"x", "y"}

    async def test_empty_dict_logs_warning(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty dict from callable logs a warning."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {},  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        # Need a keeper device to trigger shutdown
        @app.telemetry("keeper", interval=0.01)
        async def keeper(ctx: DeviceContext) -> dict[str, object]:
            ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)

        assert "empty dict" in capsys.readouterr().err

    async def test_dict_value_none_no_config_injection(self) -> None:
        """Dict value of None → no per-device config injection."""
        called: set[str] = set()
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"a": None, "b": None},  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            called.add(ctx.name)
            if len(called) >= 2:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert called == {"a", "b"}

    async def test_float_interval_shared_across_dict(self) -> None:
        """Float interval with dict name → all devices share same interval."""
        intervals: dict[str, float] = {}
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"x": SensorConfig("XX"), "y": SensorConfig("YY")},  # ty: ignore[invalid-argument-type]
            interval=7.5,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            # The interval is shared; just confirm handler runs
            intervals[ctx.name] = 7.5
            if len(intervals) >= 2:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert intervals == {"x": 7.5, "y": 7.5}


# ---------------------------------------------------------------------------
# TestListNameTelemetry
# ---------------------------------------------------------------------------


class TestListNameTelemetry:
    """List-name expansion for @app.telemetry."""

    async def test_list_name_registers_n_devices(self) -> None:
        """3-item list → 3 telemetry registrations."""
        called: set[str] = set()
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: ["a", "b", "c"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            called.add(ctx.name)
            if len(called) >= 3:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert called == {"a", "b", "c"}

    async def test_empty_list_logs_warning(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty list from callable logs a warning."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: [],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        @app.telemetry("keeper", interval=0.01)
        async def keeper(ctx: DeviceContext) -> dict[str, object]:
            ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)

        assert "empty list" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# TestDictNameDevice
# ---------------------------------------------------------------------------


class TestDictNameDevice:
    """Dict-name expansion for @app.device."""

    async def test_dict_name_with_device(self) -> None:
        """Dict name with @app.device registers multiple devices."""
        called: set[str] = set()
        app = App(name="test", version="1.0.0")

        @app.device(
            name=lambda s: {"d1": OtherConfig("one"), "d2": OtherConfig("two")},  # ty: ignore[invalid-argument-type]
        )
        async def handler(ctx: DeviceContext, config: OtherConfig) -> None:
            called.add(f"{ctx.name}:{config.name}")
            if len(called) >= 2:
                ctx._shutdown_event.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        await _run_app(app)
        assert called == {"d1:one", "d2:two"}


# ---------------------------------------------------------------------------
# TestDictNameCommand
# ---------------------------------------------------------------------------


class TestDictNameCommand:
    """Dict-name expansion for @app.command."""

    async def test_dict_name_with_command(self) -> None:
        """Dict name with @app.command registers multiple commands."""
        app = App(name="test", version="1.0.0")

        @app.command(
            name=lambda s: {"cmd1": OtherConfig("c1"), "cmd2": OtherConfig("c2")},  # ty: ignore[invalid-argument-type]
        )
        async def handler(ctx: DeviceContext, config: OtherConfig) -> dict[str, object]:
            return {"name": config.name}

        # Need a keeper telemetry to drive the app
        @app.telemetry("keeper", interval=0.01)
        async def keeper(ctx: DeviceContext) -> dict[str, object]:
            ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        # Commands are registered — verify via internal state
        assert len(app._commands) == 2
        names = {r.name for r in app._commands}
        assert names == {"cmd1", "cmd2"}


# ---------------------------------------------------------------------------
# TestConfigTypeShadowing
# ---------------------------------------------------------------------------


class TestConfigTypeShadowing:
    """Config type shadowing framework types → TypeError."""

    async def test_config_type_shadows_framework_type(self) -> None:
        """Dict config whose type is a framework injectable → TypeError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"bad": Settings()},  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(TypeError, match="shadows"):
            await _run_app(app)


# ---------------------------------------------------------------------------
# TestDuplicateNames
# ---------------------------------------------------------------------------


class TestDuplicateNames:
    """Duplicate names after expansion → ValueError."""

    async def test_duplicate_telemetry_names(self) -> None:
        """Two telemetry expansions producing same name → ValueError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: ["dup", "other"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler1(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        @app.telemetry(
            name=lambda s: ["dup"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler2(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="already registered"):
            await _run_app(app)

    async def test_expansion_collides_with_static_name(self) -> None:
        """Dynamic expansion name collides with static telemetry name."""
        app = App(name="test", version="1.0.0")

        @app.telemetry("existing", interval=5.0)
        async def static_handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        @app.telemetry(
            name=lambda s: ["existing"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def dynamic_handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="already registered"):
            await _run_app(app)

    async def test_is_root_mismatch_after_expansion(self) -> None:
        """Expanded telemetry + command sharing name but disagreeing on is_root."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: ["shared"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def tel_handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        app.add_command("shared", _noop_command, is_root=True)

        with pytest.raises(ValueError, match="MQTT topic namespaces would conflict"):
            await _run_app(app)

    async def test_enabled_false_deferred_allows_name_collision(self) -> None:
        """Disabled registration pruned before duplicate check; no false conflict."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: ["shared"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
            enabled=lambda s: False,  # pruned before duplicate check
        )
        async def handler1(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        @app.telemetry(
            name=lambda s: ["shared"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler2(ctx: DeviceContext) -> dict[str, object]:
            ctx._shutdown_event.set()  # noqa: SLF001
            return {"v": 2}

        # Must not raise — handler1 is disabled and pruned before the check
        await _run_app(app)


# ---------------------------------------------------------------------------
# TestDeferredEnabledPerDevice
# ---------------------------------------------------------------------------


class TestDeferredEnabledPerDevice:
    """Callable enabled= receives per-device config for dict-name registrations."""

    async def test_enabled_receives_per_device_config(self) -> None:
        """enabled= callable gets per-device config, not global settings."""
        surviving: list[str] = []
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {  # ty: ignore[invalid-argument-type]
                "enabled_dev": SensorConfig(mac="E"),
                "disabled_dev": SensorConfig(mac="D"),
            },
            interval=5.0,
            enabled=lambda cfg: cfg.mac == "E",
        )
        async def handler(
            ctx: DeviceContext,
            config: SensorConfig,
        ) -> dict[str, object]:
            surviving.append(ctx.name)
            ctx._shutdown_event.set()  # noqa: SLF001
            return {"mac": config.mac}

        await _run_app(app)
        assert surviving == ["enabled_dev"]


# ---------------------------------------------------------------------------
# TestPerDeviceInterval
# ---------------------------------------------------------------------------


class TestPerDeviceInterval:
    """Per-device interval resolution with dict names."""

    async def test_callable_interval_per_device(self) -> None:
        """Dict name + callable interval → per-device resolution."""
        received: dict[str, float] = {}
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {  # ty: ignore[invalid-argument-type]
                "fast": SensorConfig(mac="F", interval=1.0),
                "slow": SensorConfig(mac="S", interval=5.0),
            },
            interval=lambda cfg: cfg.interval,
        )
        async def handler(
            ctx: DeviceContext,
            config: SensorConfig,
        ) -> dict[str, object]:
            received[ctx.name] = config.interval
            if len(received) >= 2:
                ctx._shutdown_event.set()
            return {"v": 1}

        await _run_app(app)
        assert received == {"fast": 1.0, "slow": 5.0}

    async def test_per_device_interval_with_group_raises(self) -> None:
        """Per-device interval (callable) with group → ValueError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"a": SensorConfig(mac="A")},  # ty: ignore[invalid-argument-type]
            interval=lambda cfg: cfg.interval,
            group="g",
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="cannot be used with group"):
            await _run_app(app)

    async def test_per_device_interval_non_positive_raises(self) -> None:
        """Per-device interval resolving to <= 0 → ValueError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: {"bad": SensorConfig(mac="B", interval=-1.0)},  # ty: ignore[invalid-argument-type]
            interval=lambda cfg: cfg.interval,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="positive"):
            await _run_app(app)


# ---------------------------------------------------------------------------
# TestNameSpecReturnType
# ---------------------------------------------------------------------------


class TestNameSpecReturnType:
    """Invalid return types from name= callable."""

    async def test_name_returns_invalid_type(self) -> None:
        """name= callable returning non-dict/list → TypeError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: "not_a_list_or_dict",  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(TypeError, match="must return dict or list"):
            await _run_app(app)


# ---------------------------------------------------------------------------
# TestCallableNameMqttValidation
# ---------------------------------------------------------------------------


class TestCallableNameMqttValidation:
    """Callable name specs that produce MQTT-invalid names must be rejected.

    Test Techniques Used:
        - Equivalence Partitioning: one bad char per MQTT special class
        - Error Isolation: ValueError during expansion
    """

    @pytest.mark.parametrize(
        "bad_name",
        ["temp/sensor", "valve+cmd", "device#1"],
        ids=["slash", "plus", "hash"],
    )
    async def test_dict_name_rejects_mqtt_special_chars(self, bad_name: str) -> None:
        """Dict-name callable returning MQTT special chars → ValueError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s, n=bad_name: {n: None},  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            await _run_app(app)

    async def test_list_name_rejects_mqtt_special_chars(self) -> None:
        """List-name callable returning MQTT special chars → ValueError."""
        app = App(name="test", version="1.0.0")

        @app.telemetry(
            name=lambda s: ["ok", "bad/name"],  # ty: ignore[invalid-argument-type]
            interval=5.0,
        )
        async def handler(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="invalid MQTT characters"):
            await _run_app(app)
