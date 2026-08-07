"""Tests for App registration — core decorators and imperative API.

Covers: MQTT name validation, @app.device, @app.telemetry,
add_device(), add_telemetry(), add_command() — core registration paths.

Test Techniques Used:
    - Specification-based Testing: Registration contract for each decorator
      and imperative API variant.
    - Boundary Value Analysis: MQTT name validation (valid, invalid, empty,
      and special-character names).
    - Error-handling Testing: Duplicate registration raises ValueError;
      invalid names raise early.
    - Contract Testing: Device, telemetry, and command handler signatures.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._strategies import OnChange
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import _FakeFilter

pytestmark = pytest.mark.unit


class TestMqttNameValidation:
    """Validate that App and registration names reject MQTT special characters.

    MQTT topic levels are separated by ``/``; ``+`` and ``#`` are
    wildcard characters; NUL (``\\0``) is forbidden by the spec.
    Names containing these characters would corrupt topic addresses.

    Test Techniques Used:
        - Boundary Value Analysis: MQTT special chars at the validation boundary
        - Equivalence Partitioning: valid vs invalid character classes
    """

    @pytest.mark.parametrize(
        "bad_name",
        [
            "foo/bar",
            "sensor+",
            "device#",
            "name\0nul",
        ],
        ids=["slash", "plus", "hash", "nul"],
    )
    def test_app_name_rejects_mqtt_special_chars(self, bad_name: str) -> None:
        """App(name=...) should reject names with MQTT special characters."""
        with pytest.raises(ValueError, match="invalid MQTT characters"):
            App(name=bad_name, version="1.0.0")

    @pytest.mark.parametrize(
        "bad_name",
        [
            "temp/sensor",
            "valve+cmd",
            "device#1",
        ],
        ids=["slash", "plus", "hash"],
    )
    def test_device_registration_rejects_mqtt_special_chars(
        self, bad_name: str
    ) -> None:
        """@app.device(name) should reject names with MQTT special characters."""
        app = App(name="testapp", version="1.0.0")
        with pytest.raises(ValueError, match="invalid MQTT characters"):

            @app.device(bad_name)
            async def handler(ctx: DeviceContext) -> None:
                pass

    @pytest.mark.parametrize(
        "good_name",
        ["temperature", "extra_sensor", "valve-cmd", "device1"],
        ids=["simple", "underscore", "hyphen", "numeric"],
    )
    def test_valid_names_accepted(self, good_name: str) -> None:
        """Names with alphanumeric, underscore, and hyphen should be accepted."""
        app = App(name=good_name, version="1.0.0")

        @app.device(good_name)
        async def handler(ctx: DeviceContext) -> None:
            pass

        assert good_name in app.registered_names


class TestControlCharacterRejection:
    """Reject ASCII control characters in App and registration names (CWE-117).

    Names flow into log records (e.g. the ephemeral-store warning) and file
    paths; CR/LF and other control bytes would allow forging or garbling log
    entries.  The App name validator must reject the C0 range (``0x00``–
    ``0x1F``) and DEL (``0x7F``).

    Test Techniques Used:
        - Boundary Value Analysis: first (NUL, 0x00), last C0 (US, 0x1F),
          and DEL (0x7F) at the control-character range boundaries.
        - Equivalence Partitioning: representative control chars (CR, LF, TAB,
          ESC) vs. accepted printable names.
    """

    @pytest.mark.parametrize(
        "bad_name",
        [
            "line\rreturn",
            "line\nfeed",
            "tab\ttab",
            "esc\x1bseq",
            "unit\x1fsep",
            "del\x7fchar",
            "bell\aring",
        ],
        ids=["cr", "lf", "tab", "esc", "unit-sep-0x1f", "del-0x7f", "bell"],
    )
    def test_app_name_rejects_control_chars(self, bad_name: str) -> None:
        """App(name=...) rejects names containing ASCII control characters."""
        with pytest.raises(ValueError, match="control characters"):
            App(name=bad_name, version="1.0.0")

    def test_app_name_rejects_nul_as_mqtt_char(self) -> None:
        """NUL is reported as an invalid MQTT char (it is in that set too).

        Technique: Specification-based Testing — documents intentional MQTT-first
        ordering; the MQTT branch fires before the control-char branch, so its
        message wins for dual-category inputs like NUL.
        """
        # NUL is both a control char and an MQTT-forbidden char; the MQTT
        # check runs first, so its message wins.  Documented here so the
        # ordering is intentional, not incidental.
        with pytest.raises(ValueError, match="invalid MQTT characters"):
            App(name="name\x00nul", version="1.0.0")

    @pytest.mark.parametrize(
        "bad_name",
        ["bad/\nname", "slash/\rreturn", "+\x1besc"],
        ids=["slash-lf", "slash-cr", "plus-esc"],
    )
    def test_app_name_rejects_mqtt_and_control_char(self, bad_name: str) -> None:
        """Names with both MQTT-forbidden and non-NUL control chars are rejected.

        The MQTT branch fires first; the error message must not contain raw
        control bytes — verifying the repr() fix for the log-injection path
        (CWE-117).  Technique: Specification-based Testing — exercises the
        overlap between MQTT-char and control-char input classes.
        """
        with pytest.raises(ValueError) as exc_info:
            App(name=bad_name, version="1.0.0")
        msg = str(exc_info.value)
        assert "invalid MQTT characters" in msg
        # No raw control byte must appear verbatim in the message (CWE-117).
        assert not any(c for c in msg if c <= "\x1f" or c == "\x7f"), (
            f"Raw control byte leaked into error message: {msg!r}"
        )

    @pytest.mark.parametrize(
        "bad_name",
        ["log\rinject", "log\nforge", "bad\x7fdel"],
        ids=["cr", "lf", "del"],
    )
    def test_device_registration_rejects_control_chars(self, bad_name: str) -> None:
        """@app.device(name) rejects names containing control characters."""
        app = App(name="testapp", version="1.0.0")
        with pytest.raises(ValueError, match="control characters"):

            @app.device(bad_name)
            async def handler(ctx: DeviceContext) -> None:
                pass

    @pytest.mark.parametrize(
        "good_name",
        [
            "sensor.hub",  # dotted — not covered by TestMqttNameValidation
            "a\x20b",  # 0x20 (SPACE) — first non-control printable ASCII
            "a\x7eb",  # 0x7E (~) — last printable ASCII before DEL (0x7F)
        ],
        ids=["dotted", "space-0x20-boundary", "tilde-0x7e-boundary"],
    )
    def test_control_free_names_accepted(self, good_name: str) -> None:
        """Printable names without control chars remain valid.

        BVA: 0x20 (first non-control) and 0x7E (last before DEL at 0x7F) sit at
        the edges of the accepted range for the control-char guard.
        """
        app = App(name=good_name, version="1.0.0")
        assert app.name == good_name


class TestEmptyNameRejection:
    """Reject empty and whitespace-only App names.

    The App name is the MQTT topic root prefix and is emitted as the
    channel-level ``x-cosalette-app`` tag (ADR-033).  An empty or blank name
    would yield ``/device/state`` topics and a generated schema whose
    ``x-cosalette-app: ''`` the loader rejects on read-back — breaking schema
    enforcement closed.  ``validate_mqtt_name`` bars ``/ + #`` and control
    characters but not emptiness, so ``App.__init__`` enforces non-blank names.

    Test Techniques Used:
        - Boundary Value Analysis: empty string and single space at the
          non-empty boundary.
        - Equivalence Partitioning: blank vs. non-blank names.
    """

    @pytest.mark.parametrize(
        "bad_name",
        ["", " ", "   "],
        ids=["empty", "single-space", "spaces"],
    )
    def test_app_name_rejects_empty_or_blank(self, bad_name: str) -> None:
        """App(name=...) rejects empty or whitespace-only names.

        Closes the regression where a blank name emitted ``x-cosalette-app: ''``
        on every channel, which the schema loader then rejected on read-back.
        """
        with pytest.raises(ValueError, match="non-empty, non-blank"):
            App(name=bad_name, version="1.0.0")


# ---------------------------------------------------------------------------
# TestDeviceDecorator
# ---------------------------------------------------------------------------


class TestDeviceDecorator:
    """@app.device decorator registration tests.

    Technique: Specification-based Testing — verifying that the
    decorator records registrations and rejects duplicates.
    """

    async def test_registers_device_function(self, app: App) -> None:
        """@app.device('name') stores a _DeviceRegistration internally."""

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None: ...

        assert len(app._devices) == 1
        assert app._devices[0].name == "sensor"
        assert app._devices[0].func is sensor

    async def test_returns_original_function(self, app: App) -> None:
        """Decorator returns the original function unchanged (transparent)."""

        async def sensor(ctx: DeviceContext) -> None: ...

        result = app.device("sensor")(sensor)
        assert result is sensor

    async def test_duplicate_device_name_raises(self, app: App) -> None:
        """Registering two devices with the same name raises ValueError."""

        @app.device("blind")
        async def blind1(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.device("blind")
            async def blind2(ctx: DeviceContext) -> None: ...

    async def test_duplicate_across_device_and_telemetry_raises(self, app: App) -> None:
        """A device name can't collide with an existing telemetry name."""

        @app.telemetry("sensor", interval=10)
        async def sensor_telem(ctx: DeviceContext) -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.device("sensor")
            async def sensor_dev(ctx: DeviceContext) -> None: ...

    async def test_multiple_distinct_devices(self, app: App) -> None:
        """Multiple devices with distinct names all register successfully."""

        @app.device("blind")
        async def blind(ctx: DeviceContext) -> None: ...

        @app.device("window")
        async def window(ctx: DeviceContext) -> None: ...

        assert len(app._devices) == 2
        names = {d.name for d in app._devices}
        assert names == {"blind", "window"}


# ---------------------------------------------------------------------------
# TestTelemetryDecorator
# ---------------------------------------------------------------------------


class TestTelemetryDecorator:
    """@app.telemetry decorator registration tests.

    Technique: Specification-based Testing — verifying registration
    storage, interval validation, and duplicate detection.
    """

    async def test_registers_telemetry_function(self, app: App) -> None:
        """@app.telemetry stores a _TelemetryRegistration with interval."""

        @app.telemetry("temp", interval=30)
        async def temp(ctx: DeviceContext) -> dict[str, object]:
            return {"celsius": 22.5}

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "temp"
        assert app._telemetry[0].interval == 30
        assert app._telemetry[0].func is temp

    async def test_returns_original_function(self, app: App) -> None:
        """Decorator returns the original function unchanged."""

        async def temp(ctx: DeviceContext) -> dict[str, object]:
            return {}

        result = app.telemetry("temp", interval=5)(temp)
        assert result is temp

    async def test_duplicate_name_raises(self, app: App) -> None:
        """Duplicate telemetry name raises ValueError."""

        @app.telemetry("temp", interval=10)
        async def temp1(ctx: DeviceContext) -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.telemetry("temp", interval=20)
            async def temp2(ctx: DeviceContext) -> dict[str, object]:
                return {}

    async def test_zero_interval_raises(self, app: App) -> None:
        """Interval of zero raises ValueError at decoration time."""
        with pytest.raises(ValueError, match="interval.*schedule|positive"):

            @app.telemetry("temp", interval=0)
            async def temp(ctx: DeviceContext) -> dict[str, object]:
                return {}

    async def test_negative_interval_raises(self, app: App) -> None:
        """Negative interval raises ValueError at decoration time."""
        with pytest.raises(ValueError, match="positive"):

            @app.telemetry("temp", interval=-5)
            async def temp(ctx: DeviceContext) -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# TestDirectFunctionRegistration — imperative add_*() methods
# ---------------------------------------------------------------------------


class TestDirectFunctionRegistration:
    """Tests for imperative add_device(), add_telemetry(), add_command() methods.

    Technique: Specification-based Testing — verifying that the imperative
    API produces correct registrations, shares validation with decorators,
    and detects collisions across both APIs.
    """

    # --- add_device ---------------------------------------------------------

    def test_add_device_registers_function(self, app: App) -> None:
        """add_device stores a _DeviceRegistration with is_root=False."""

        async def sensor(ctx: DeviceContext) -> None: ...

        app.add_device("sensor", sensor)

        assert len(app._devices) == 1  # noqa: SLF001
        reg = app._devices[0]  # noqa: SLF001
        assert reg.name == "sensor"
        assert reg.func is sensor
        assert reg.is_root is False

    def test_add_device_duplicate_name_raises(self, app: App) -> None:
        """Registering two devices with the same name raises ValueError."""

        async def dev1(ctx: DeviceContext) -> None: ...

        async def dev2(ctx: DeviceContext) -> None: ...

        app.add_device("x", dev1)
        with pytest.raises(ValueError, match="already registered"):
            app.add_device("x", dev2)

    def test_add_device_cross_type_collision(self, app: App) -> None:
        """A device name can't collide with an existing telemetry name."""

        async def dev(ctx: DeviceContext) -> None: ...

        async def telem() -> dict[str, object]:
            return {"v": 1}

        app.add_device("x", dev)
        with pytest.raises(ValueError, match="already registered"):
            app.add_telemetry("x", telem, interval=1)

    def test_add_device_with_init(self, app: App) -> None:
        """init callback is stored on the registration."""

        def make_filter() -> _FakeFilter:
            return _FakeFilter()

        async def dev(ctx: DeviceContext, f: _FakeFilter) -> None: ...

        app.add_device("dev", dev, init=make_filter)

        reg = app._devices[0]  # noqa: SLF001
        assert reg.init is make_filter
        assert reg.init_injection_plan == []

    def test_add_device_async_init_raises(self, app: App) -> None:
        """Async init is rejected with TypeError."""

        async def async_init() -> _FakeFilter:
            return _FakeFilter()

        async def dev(ctx: DeviceContext) -> None: ...

        with pytest.raises(TypeError, match="synchronous callable"):
            app.add_device("dev", dev, init=async_init)

    def test_add_device_unannotated_param_raises(self, app: App) -> None:
        """Function with unannotated param raises TypeError."""

        async def bad(some_arg) -> None:  # noqa: ANN001
            pass

        with pytest.raises(TypeError, match="no type annotation"):
            app.add_device("bad", bad)

    # --- add_telemetry ------------------------------------------------------

    def test_add_telemetry_registers_function(self, app: App) -> None:
        """add_telemetry stores a _TelemetryRegistration with correct fields."""

        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        app.add_telemetry("temp", temp, interval=30)

        assert len(app._telemetry) == 1  # noqa: SLF001
        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.name == "temp"
        assert reg.func is temp
        assert reg.interval == 30
        assert reg.is_root is False

    def test_add_telemetry_zero_interval_raises(self, app: App) -> None:
        """interval=0 raises ValueError."""

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="interval.*schedule|positive"):
            app.add_telemetry("temp", temp, interval=0)

    def test_add_telemetry_negative_interval_raises(self, app: App) -> None:
        """interval=-1 raises ValueError."""

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="positive"):
            app.add_telemetry("temp", temp, interval=-1)

    def test_add_telemetry_persist_without_store_raises(self, app: App) -> None:
        """persist set but no store on App raises ValueError."""
        from cosalette._persistence._persist import SaveOnPublish

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="store="):
            app.add_telemetry("temp", temp, interval=10, persist=SaveOnPublish())

    # --- add_command --------------------------------------------------------

    def test_add_command_registers_function(self, app: App) -> None:
        """add_command stores a _CommandRegistration with correct fields."""

        async def switch(payload: str) -> dict[str, object]:
            return {"state": payload}

        app.add_command("switch", switch)

        assert len(app._commands) == 1  # noqa: SLF001
        reg = app._commands[0]  # noqa: SLF001
        assert reg.name == "switch"
        assert reg.func is switch
        assert reg.is_root is False

    def test_add_command_detects_mqtt_params(self, app: App) -> None:
        """Function with topic and payload params detected in mqtt_params."""

        async def handler(topic: str, payload: str) -> dict[str, object]:
            return {"t": topic, "p": payload}

        app.add_command("switch", handler)

        reg = app._commands[0]  # noqa: SLF001
        assert reg.mqtt_params == frozenset({"topic", "payload"})

    # --- Collision between decorator and imperative -------------------------

    def test_decorator_and_add_collision(self, app: App) -> None:
        """@app.device('x') then app.add_device('x', ...) raises."""

        @app.device("x")
        async def x_dev(ctx: DeviceContext) -> None: ...

        async def x_dev2(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):
            app.add_device("x", x_dev2)

    def test_add_and_decorator_collision(self, app: App) -> None:
        """app.add_device('x', ...) then @app.device('x') raises."""

        async def x_dev(ctx: DeviceContext) -> None: ...

        app.add_device("x", x_dev)

        with pytest.raises(ValueError, match="already registered"):

            @app.device("x")
            async def x_dev2(ctx: DeviceContext) -> None: ...

    # --- Decorator equivalence ----------------------------------------------

    def test_decorator_equivalence_device(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_device."""
        app2 = App(name="testapp", version="1.0.0")

        async def sensor(ctx: DeviceContext) -> None: ...

        # Decorator path
        app.device("sensor")(sensor)
        # Imperative path
        app2.add_device("sensor", sensor)

        d_reg = app._devices[0]  # noqa: SLF001
        a_reg = app2._devices[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.injection_plan == a_reg.injection_plan
        assert d_reg.init == a_reg.init
        assert d_reg.init_injection_plan == a_reg.init_injection_plan

    def test_decorator_equivalence_telemetry(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_telemetry."""
        app2 = App(name="testapp", version="1.0.0")
        strategy = OnChange()

        async def temp() -> dict[str, object]:
            return {"v": 1}

        app.telemetry("temp", interval=10, publish=strategy)(temp)
        app2.add_telemetry("temp", temp, interval=10, publish=strategy)

        d_reg = app._telemetry[0]  # noqa: SLF001
        a_reg = app2._telemetry[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.interval == a_reg.interval
        assert d_reg.publish_strategy is a_reg.publish_strategy
        assert d_reg.injection_plan == a_reg.injection_plan

    def test_decorator_equivalence_command(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_command."""
        app2 = App(name="testapp", version="1.0.0")

        async def valve(payload: str) -> dict[str, object]:
            return {"v": payload}

        app.command("valve")(valve)
        app2.add_command("valve", valve)

        d_reg = app._commands[0]  # noqa: SLF001
        a_reg = app2._commands[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.mqtt_params == a_reg.mqtt_params
        assert d_reg.injection_plan == a_reg.injection_plan

    # --- Mixed registration -------------------------------------------------

    def test_mixed_decorator_and_imperative(self, app: App) -> None:
        """Mix of decorators and imperative registrations all register."""

        @app.device("d1")
        async def d1(ctx: DeviceContext) -> None: ...

        async def d2(ctx: DeviceContext) -> None: ...

        app.add_device("d2", d2)

        @app.telemetry("t1", interval=10)
        async def t1() -> dict[str, object]:
            return {}

        async def t2() -> dict[str, object]:
            return {}

        app.add_telemetry("t2", t2, interval=20)

        @app.command("c1")
        async def c1(payload: str) -> dict[str, object]:
            return {}

        async def c2(payload: str) -> dict[str, object]:
            return {}

        app.add_command("c2", c2)

        assert len(app._devices) == 2  # noqa: SLF001
        assert len(app._telemetry) == 2  # noqa: SLF001
        assert len(app._commands) == 2  # noqa: SLF001
        all_names = {
            r.name
            for r in [*app._devices, *app._telemetry, *app._commands]  # noqa: SLF001
        }
        assert all_names == {"d1", "d2", "t1", "t2", "c1", "c2"}

    # --- Runtime integration ------------------------------------------------

    async def test_add_device_runs_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered device actually executes in _run_async.

        Technique: Integration Testing — register a device via add_device,
        verify it runs during the async lifecycle.
        """
        app = App(name="testapp", version="1.0.0")
        device_called = asyncio.Event()

        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_called.set()
            yield

        app.add_device("sensor", sensor)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_called.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert device_called.is_set()

    async def test_add_telemetry_runs_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered telemetry polls and publishes.

        Technique: Integration Testing — register telemetry via
        add_telemetry, verify it runs and publishes state.
        """
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

        app.add_telemetry("temp", temp, interval=0.01)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert called.is_set()
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) >= 1
        assert "22.5" in state_messages[0][0]

    async def test_add_command_routes_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered command receives dispatched messages.

        Technique: Integration Testing — register a command via
        add_command, deliver an MQTT message, verify the handler
        is invoked and state is published.
        """
        app = App(name="testapp", version="1.0.0")
        command_received = asyncio.Event()

        async def relay(payload: str) -> dict[str, object]:
            command_received.set()
            return {"state": payload}

        app.add_command("relay", relay)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/relay/set", "ON")
            await command_received.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(simulate())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert command_received.is_set()
        state_messages = mock_mqtt.get_messages_for("testapp/relay/state")
        assert len(state_messages) >= 1
        assert "ON" in state_messages[0][0]
