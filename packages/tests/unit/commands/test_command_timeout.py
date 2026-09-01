"""Unit tests for bounded handler execution defaults (F-DP5 / ADR-060).

Covers: command timeout three-state semantics (UNSET/explicit/None/callable),
bounded default enforcement with structured timeout errors, FIFO-worker
continuation after a watchdog kill, periodic watchdog, and device-context
on_command timeout storage/lookup.

Test Techniques Used:
- Boundary Value Analysis: omitted vs explicit vs None vs callable timeout
- State Transition Testing: registration (_UNSET) → bootstrap resolution →
  runner enforcement
- Error Guessing: hung handler, invalid callable resolution results
- Adversarial Testing: worker must keep serving queued commands after a
  watchdog cancellation
"""

from __future__ import annotations

import asyncio
import json

import pytest

from cosalette._app import App
from cosalette._context._device_context import DeviceContext
from cosalette._registration import TimeoutSpec, _Unset
from cosalette._registration._model import _UNSET
from cosalette._router import Router
from cosalette._runners._command_runner import _dispatch_handler
from cosalette._utils import _DEFAULT_COMMAND_TIMEOUT
from cosalette._wiring import resolve_timeouts_commands, resolve_timeouts_periodic
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


async def _noop() -> None: ...


# ---------------------------------------------------------------------------
# Registration: three-state storage
# ---------------------------------------------------------------------------


class TestCommandTimeoutRegistration:
    """timeout= storage on @app.command registrations (ADR-060)."""

    def test_omitted_stores_unset_sentinel(self, app: App) -> None:
        """Omitting timeout stores _UNSET — the bounded-default trigger."""

        @app.command("dev")
        async def handler(topic: str, payload: str) -> None: ...

        assert app._commands[0].timeout is _UNSET  # noqa: SLF001

    def test_explicit_float_stored(self, app: App) -> None:
        """An explicit float is stored verbatim."""

        @app.command("dev", timeout=2.5)
        async def handler(topic: str, payload: str) -> None: ...

        assert app._commands[0].timeout == 2.5  # noqa: SLF001

    def test_none_stores_disabled_backstop(self, app: App) -> None:
        """Explicit None opts out of the bounded default."""

        @app.command("dev", timeout=None)
        async def handler(topic: str, payload: str) -> None: ...

        assert app._commands[0].timeout is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Bootstrap resolution
# ---------------------------------------------------------------------------


class TestResolveCommandTimeouts:
    """resolve_timeouts_commands(): UNSET → 30 s default, callable → resolved."""

    def test_unset_default_applied_via_bootstrap(self) -> None:
        """End-to-end: omitted timeout resolves to _DEFAULT_COMMAND_TIMEOUT."""
        from cosalette._wiring import resolve_timeouts_commands

        app = App(name="testapp", version="1.0.0")

        @app.command("dev")
        async def handler(topic: str, payload: str) -> None: ...

        resolve_timeouts_commands(app._commands, make_settings())
        assert app._commands[0].timeout == _DEFAULT_COMMAND_TIMEOUT  # noqa: SLF001

    def test_callable_resolved_against_settings(self) -> None:
        """A (Settings) -> float spec is resolved at bootstrap."""
        from cosalette._wiring import resolve_timeouts_commands

        app = App(name="testapp", version="1.0.0")

        @app.command("dev", timeout=lambda settings: 12.0)
        async def handler(topic: str, payload: str) -> None: ...

        resolve_timeouts_commands(app._commands, make_settings())
        assert app._commands[0].timeout == 12.0  # noqa: SLF001

    def test_callable_invalid_result_raises(self) -> None:
        """Non-positive or non-numeric callable results raise ValueError."""
        from cosalette._wiring import resolve_timeouts_commands

        for bad in (lambda s: -1.0, lambda s: "nope"):
            app = App(name="testapp", version="1.0.0")

            @app.command("dev", timeout=bad)  # ty: ignore[invalid-argument-type]
            async def handler(topic: str, payload: str) -> None: ...

            with pytest.raises(ValueError, match="Command timeout"):
                resolve_timeouts_commands(app._commands, make_settings())

    def test_explicit_values_pass_through(self) -> None:
        """Concrete floats and None are untouched by resolution."""
        from cosalette._wiring import resolve_timeouts_commands

        app = App(name="testapp", version="1.0.0")

        @app.command("a", timeout=7.5)
        async def a(topic: str, payload: str) -> None: ...

        @app.command("b", timeout=None)
        async def b(topic: str, payload: str) -> None: ...

        resolve_timeouts_commands(app._commands, make_settings())
        assert app._commands[0].timeout == 7.5  # noqa: SLF001
        assert app._commands[1].timeout is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Enforcement: watchdog kills hung handlers, worker survives
# ---------------------------------------------------------------------------


class TestCommandTimeoutEnforcement:
    """Runtime enforcement via asyncio.timeout inside the command pipeline."""

    async def test_hung_handler_publishes_structured_timeout_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A handler exceeding its bound is cancelled and surfaces as
        error_type 'timeout' on the device error topic (CWE-400 backstop).
        """
        app = App(name="testapp", version="1.0.0")

        @app.command("dev", timeout=0.05)
        async def handler(topic: str, payload: str) -> None:
            await asyncio.sleep(10)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.01)
            await mock_mqtt.deliver("testapp/dev/set", "x")
            await asyncio.sleep(0.5)
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

        error_msgs = mock_mqtt.get_messages_for("testapp/dev/error")
        assert len(error_msgs) >= 1
        payload = json.loads(error_msgs[0][0])
        assert payload["error_type"] == "timeout"

    async def test_worker_continues_after_watchdog_kill(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """The entity worker serves the next queued command after a hang.

        Technique: Adversarial Testing — the DoS goal of F-DP5 is a
        permanently stalled FIFO; the watchdog must restore liveness.
        """
        app = App(name="testapp", version="1.0.0")
        handled: list[str] = []

        @app.command("dev", timeout=0.05)
        async def handler(topic: str, payload: str) -> dict[str, object]:
            if payload == "hang":
                await asyncio.sleep(10)
            handled.append(payload)
            return {"echo": payload}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.01)
            await mock_mqtt.deliver("testapp/dev/set", "hang")
            await mock_mqtt.deliver("testapp/dev/set", "ok")  # queues behind
            await asyncio.sleep(0.5)
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

        assert handled == ["ok"]

    async def test_timeout_none_allows_long_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """timeout=None restores unbounded execution (explicit opt-out)."""
        app = App(name="testapp", version="1.0.0")
        done = False

        @app.command("dev", timeout=None)
        async def handler(topic: str, payload: str) -> dict[str, object]:
            nonlocal done
            await asyncio.sleep(0.2)
            done = True
            return {}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.01)
            await mock_mqtt.deliver("testapp/dev/set", "x")
            await asyncio.sleep(0.5)
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
        assert done

    async def test_unavailable_on_timeout_marks_device_offline(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """unavailable_on=(TimeoutError,) composes with the watchdog."""
        app = App(name="testapp", version="1.0.0")

        @app.command("dev", timeout=0.05, unavailable_on=(TimeoutError,))
        async def handler(topic: str, payload: str) -> None:
            await asyncio.sleep(10)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.01)
            await mock_mqtt.deliver("testapp/dev/set", "x")
            await asyncio.sleep(0.5)
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

        avail = mock_mqtt.get_messages_for("testapp/dev/availability")
        assert any("offline" in m[0] for m in avail)


# ---------------------------------------------------------------------------
# Periodic watchdog
# ---------------------------------------------------------------------------


class TestPeriodicWatchdog:
    """run_periodic() cancels a wedged cycle and keeps looping."""

    async def test_wedged_cycle_cancelled_loop_continues(self) -> None:
        """A hung first cycle does not prevent the second tick."""
        from cosalette._runners._periodic import _PeriodicRegistration

        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(10)  # exceeds 0.05 s watchdog
            raise asyncio.CancelledError

        reg = _PeriodicRegistration(
            name="test",
            func=handler,
            injection_plan=[],
            interval=0.001,
            timeout=0.05,
        )
        with pytest.raises(asyncio.CancelledError):
            await run_periodic_helper(reg)

        assert len(calls) >= 2

    async def test_unset_timeout_disables_watchdog(self) -> None:
        """Direct-constructed registrations (tests) run unguarded."""
        from cosalette._registration._model import _UNSET
        from cosalette._runners._periodic import _PeriodicRegistration

        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)
            await asyncio.sleep(0.01)
            if len(calls) >= 2:
                raise asyncio.CancelledError

        reg = _PeriodicRegistration(
            name="test",
            func=handler,
            injection_plan=[],
            interval=0.001,
            timeout=_UNSET,
        )
        with pytest.raises(asyncio.CancelledError):
            await run_periodic_helper(reg)

        assert len(calls) >= 2


async def run_periodic_helper(reg: object) -> None:
    """Call run_periodic without importing at module scope twice."""
    from cosalette._runners._periodic import run_periodic

    await run_periodic(reg, {})  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# Periodic resolution
# ---------------------------------------------------------------------------


class TestPeriodicTimeoutResolution:
    """resolve_timeouts_periodic(): UNSET → interval × factor."""

    def test_unset_resolves_to_interval(self) -> None:
        from cosalette._runners._periodic import _PeriodicRegistration
        from cosalette._wiring import (
            resolve_intervals_periodic,
            resolve_timeouts_periodic,
        )

        reg = _PeriodicRegistration(
            name="p", func=_noop, injection_plan=[], interval=8.0
        )
        regs = [reg]
        resolve_timeouts_periodic(regs, make_settings())
        assert regs[0].timeout == 8.0  # interval × 1.0

        # Callable intervals must be resolved first (same contract as telemetry)
        reg2 = _PeriodicRegistration(
            name="q",
            func=_noop,
            injection_plan=[],
            interval=lambda s: 4.0,
        )
        regs2 = [reg2]
        resolve_intervals_periodic(regs2, make_settings())
        resolve_timeouts_periodic(regs2, make_settings())
        assert regs2[0].timeout == 4.0

    def test_invalid_callable_raises(self) -> None:
        from cosalette._runners._periodic import _PeriodicRegistration
        from cosalette._wiring import resolve_timeouts_periodic

        reg = _PeriodicRegistration(
            name="p",
            func=_noop,
            injection_plan=[],
            interval=8.0,
            timeout=lambda s: 0,
        )
        with pytest.raises(ValueError, match="Periodic timeout"):
            resolve_timeouts_periodic([reg], make_settings())


# ---------------------------------------------------------------------------
# Device-context on_command timeouts
# ---------------------------------------------------------------------------


class TestDeviceContextOnCommandTimeout:
    """ctx.on_command stores and reports per-handler watchdog bounds."""

    def test_default_bound_recorded(self) -> None:
        """on_command stores bounds; lookup falls back to the 30 s default."""
        ctx = DeviceContext.__new__(DeviceContext)
        object.__setattr__(ctx, "_command_handlers", {})
        object.__setattr__(ctx, "_command_timeouts", {})
        object.__setattr__(ctx, "_name", "cam")
        object.__setattr__(ctx, "_commands_consumed", False)

        @ctx.on_command
        async def root(topic: str | None, payload: str) -> None: ...

        @ctx.on_command("calibrate", timeout=90.0)
        async def cal(sub_topic: str | None, payload: str) -> None: ...

        assert ctx.get_command_timeout(None) == _DEFAULT_COMMAND_TIMEOUT
        assert ctx.get_command_timeout("calibrate") == 90.0

    async def test_dispatch_handler_enforces_bound(self) -> None:
        """_dispatch_handler cancels a hung handler and publishes timeout."""

        class _StubPublisher:
            def __init__(self) -> None:
                self.published: list[tuple[BaseException, str, bool]] = []

            async def publish(
                self, exc: BaseException, *, device: str, is_root: bool
            ) -> None:
                self.published.append((exc, device, is_root))

        class _StubCtx:
            def __init__(self) -> None:
                self.clock = type("C", (), {"now": staticmethod(lambda: 0.0)})()

        pub = _StubPublisher()

        async def hung(sub_topic: str | None, payload: str) -> None:
            await asyncio.sleep(10)

        await asyncio.wait_for(
            _dispatch_handler(
                hung,
                "t/set",
                "x",
                None,
                _StubCtx(),  # ty: ignore[invalid-argument-type]
                pub,  # ty: ignore[invalid-argument-type]
                "cam",
                False,
                timeout=0.05,
            ),
            timeout=2.0,
        )
        assert len(pub.published) == 1
        exc, device, is_root = pub.published[0]
        assert isinstance(exc, TimeoutError)
        assert (device, is_root) == ("cam", False)

    async def test_dispatch_handler_none_is_unbounded(self) -> None:
        """timeout=None runs the handler to completion regardless of duration."""

        class _StubPublisher:
            def __init__(self) -> None:
                self.published: list[BaseException] = []

            async def publish(
                self, exc: BaseException, *, device: str, is_root: bool
            ) -> None:
                self.published.append(exc)

        class _StubCtx:
            def __init__(self) -> None:
                self.clock = type("C", (), {"now": staticmethod(lambda: 0.0)})()

        pub = _StubPublisher()
        ran = False

        async def slow(sub_topic: str | None, payload: str) -> None:
            nonlocal ran
            await asyncio.sleep(0.15)
            ran = True

        await _dispatch_handler(
            slow,
            "t/set",
            "x",
            None,
            _StubCtx(),  # ty: ignore[invalid-argument-type]
            pub,  # ty: ignore[invalid-argument-type]
            "cam",
            False,
            timeout=None,
        )
        assert ran and not pub.published


# ---------------------------------------------------------------------------
# Router path parity
# ---------------------------------------------------------------------------


class TestRouterTimeoutParity:
    """A router-registered handler gets the same backstop as an app one.

    Technique: Comparison Testing — the router mixins redeclare each
    archetype's signature by hand (cos-iuhd), so ``timeout=`` must be shown
    to survive registration, ``include_router`` and bootstrap resolution, not
    just to be accepted as a keyword.
    """

    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [
            pytest.param(_UNSET, _DEFAULT_COMMAND_TIMEOUT, id="omitted-default"),
            pytest.param(2.5, 2.5, id="explicit-float"),
            pytest.param(None, None, id="explicit-none"),
            pytest.param(lambda settings: 12.0, 12.0, id="callable"),
        ],
    )
    def test_router_command_timeout_survives_include_router(
        self, timeout: TimeoutSpec | None | _Unset, expected: float | None
    ) -> None:
        """Every three-state value resolves the same through a Router."""
        # Arrange
        app = App(name="testapp", version="1.0.0")
        router = Router(prefix="sensors")

        @router.command("dev", timeout=timeout)
        async def handler(topic: str, payload: str) -> None: ...

        # Act
        app.include_router(router)
        resolve_timeouts_commands(app._commands, make_settings())  # noqa: SLF001

        # Assert
        assert app._commands[0].name == "sensors/dev"  # noqa: SLF001
        assert app._commands[0].timeout == expected  # noqa: SLF001

    def test_router_command_invalid_callable_raises(self) -> None:
        """A bad callable fails at bootstrap, as on the app path."""
        app = App(name="testapp", version="1.0.0")
        router = Router()

        @router.command("dev", timeout=lambda s: -1.0)
        async def handler(topic: str, payload: str) -> None: ...

        app.include_router(router)

        with pytest.raises(ValueError, match="Command timeout"):
            resolve_timeouts_commands(app._commands, make_settings())  # noqa: SLF001

    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [
            pytest.param(_UNSET, 8.0, id="omitted-one-interval"),
            pytest.param(5.0, 5.0, id="explicit-float"),
            pytest.param(None, None, id="explicit-none"),
            pytest.param(lambda settings: 3.0, 3.0, id="callable"),
        ],
    )
    def test_router_periodic_timeout_survives_include_router(
        self, timeout: TimeoutSpec | None | _Unset, expected: float | None
    ) -> None:
        """Omitted router periodics still auto-default to one interval."""
        # Arrange
        app = App(name="testapp", version="1.0.0")
        router = Router(prefix="sensors")

        @router.periodic("beat", interval=8.0, timeout=timeout)
        async def beat() -> None: ...

        # Act
        app.include_router(router)
        resolve_timeouts_periodic(app._periodic, make_settings())  # noqa: SLF001

        # Assert
        assert app._periodic[0].name == "sensors/beat"  # noqa: SLF001
        assert app._periodic[0].timeout == expected  # noqa: SLF001

    async def test_router_command_watchdog_kills_hung_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """End-to-end: the runner enforces a router-declared bound.

        Technique: Comparison Testing — mirrors
        ``test_hung_handler_publishes_structured_timeout_error`` on the app
        path; the only difference is the registration entry point.
        """
        app = App(name="testapp", version="1.0.0")
        router = Router()

        @router.command("dev", timeout=0.05)
        async def handler(topic: str, payload: str) -> None:
            await asyncio.sleep(10)

        app.include_router(router)
        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.01)
            await mock_mqtt.deliver("testapp/dev/set", "x")
            await asyncio.sleep(0.5)
            shutdown.set()

        asyncio.create_task(simulate())
        await asyncio.wait_for(
            app._run_async(  # noqa: SLF001
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        error_msgs = mock_mqtt.get_messages_for("testapp/dev/error")
        assert len(error_msgs) >= 1
        assert json.loads(error_msgs[0][0])["error_type"] == "timeout"
