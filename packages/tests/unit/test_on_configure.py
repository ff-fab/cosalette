"""Tests for @app.on_configure lifecycle hook.

Covers: decorator registration, DI injection, execution ordering,
and interaction with the async lifecycle.

Test Techniques Used:
    - Unit Testing: isolated hook registration and ordering
    - Dependency Injection: verify Settings, adapters, and App injection
    - Async Lifecycle: hooks run inside the real async bootstrap path
    - Error Isolation: exceptions in hooks surface as RuntimeError
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Module-level helpers (needed for get_type_hints resolution under PEP 563)
# ---------------------------------------------------------------------------


class _TestSettings(Settings):
    """Settings subclass for on_configure DI tests."""

    custom_value: str = "hello"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[Settings],  # noqa: ARG003
        init_settings: Any,
        env_settings: Any,  # noqa: ARG003
        dotenv_settings: Any,  # noqa: ARG003
        file_secret_settings: Any,  # noqa: ARG003
    ) -> tuple[Any, ...]:
        return (init_settings,)


@runtime_checkable
class _FakePort(Protocol):
    def value(self) -> int: ...


class _FakeImpl:
    def value(self) -> int:
        return 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_app(
    app: App,
    *,
    settings: Settings | None = None,
) -> None:
    """Run app until shutdown, with short timeout."""
    shutdown = asyncio.Event()
    shutdown.set()

    @app.telemetry("_keeper", interval=0.01)
    async def _keeper(ctx: DeviceContext) -> dict[str, object]:
        ctx._shutdown_event.set()
        return {"v": 1}

    await asyncio.wait_for(
        app._run_async(
            mqtt=MockMqttClient(),
            settings=settings or make_settings(),
            shutdown_event=shutdown,
            clock=FakeClock(),
        ),
        timeout=5.0,
    )


# ---------------------------------------------------------------------------
# TestOnConfigureRegistration
# ---------------------------------------------------------------------------


class TestOnConfigureRegistration:
    """@app.on_configure decorator registration tests.

    Technique: Specification-based Testing
    """

    async def test_registers_hook(self) -> None:
        app = App(name="t", version="1.0.0")

        @app.on_configure
        def setup() -> None: ...

        assert len(app._configure_hooks) == 1
        assert app._configure_hooks[0] is setup

    async def test_returns_original_function(self) -> None:
        app = App(name="t", version="1.0.0")

        def setup() -> None: ...

        result = app.on_configure(setup)
        assert result is setup

    async def test_multiple_hooks_stored_in_order(self) -> None:
        app = App(name="t", version="1.0.0")

        @app.on_configure
        def first() -> None: ...

        @app.on_configure
        def second() -> None: ...

        assert [h.__name__ for h in app._configure_hooks] == ["first", "second"]

    async def test_hook_with_no_parameters(self) -> None:
        app = App(name="t", version="1.0.0")

        @app.on_configure
        def noop() -> None: ...

        assert len(app._configure_hooks) == 1


# ---------------------------------------------------------------------------
# TestOnConfigureExecution
# ---------------------------------------------------------------------------


class TestOnConfigureExecution:
    """@app.on_configure hook execution during lifecycle.

    Technique: State Transition Testing — lifecycle phases
    """

    async def test_hook_called_during_startup(self) -> None:
        app = App(name="t", version="1.0.0")
        called = False

        @app.on_configure
        def setup() -> None:
            nonlocal called
            called = True

        await _run_app(app)
        assert called is True

    async def test_hook_receives_settings_via_di(self) -> None:
        app = App(name="t", version="1.0.0", settings_class=_TestSettings)
        received: Settings | None = None

        @app.on_configure
        def setup(settings: _TestSettings) -> None:
            nonlocal received
            received = settings

        s = _TestSettings()
        await _run_app(app, settings=s)
        assert received is s

    async def test_hook_receives_adapter_via_di(self) -> None:
        app = App(name="t", version="1.0.0")
        app.adapter(_FakePort, _FakeImpl)
        received: _FakePort | None = None

        @app.on_configure
        def setup(port: _FakePort) -> None:
            nonlocal received
            received = port

        await _run_app(app)
        assert received is not None
        assert received.value() == 42

    async def test_hook_receives_logger_via_di(self) -> None:
        app = App(name="t", version="1.0.0")
        received: logging.Logger | None = None

        @app.on_configure
        def setup(log: logging.Logger) -> None:
            nonlocal received
            received = log

        await _run_app(app)
        assert received is not None
        assert received.name == "cosalette.configure"

    async def test_multiple_hooks_execute_in_registration_order(self) -> None:
        app = App(name="t", version="1.0.0")
        order: list[int] = []

        @app.on_configure
        def first() -> None:
            order.append(1)

        @app.on_configure
        def second() -> None:
            order.append(2)

        await _run_app(app)
        assert order == [1, 2]

    async def test_exception_in_hook_is_fatal(self) -> None:
        app = App(name="t", version="1.0.0")

        @app.on_configure
        def boom() -> None:
            msg = "setup failed"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="setup failed"):
            await _run_app(app)

    async def test_async_hook_is_awaited(self) -> None:
        app = App(name="t", version="1.0.0")
        called = False

        @app.on_configure
        async def setup() -> None:
            nonlocal called
            await asyncio.sleep(0)
            called = True

        await _run_app(app)
        assert called is True

    async def test_hook_runs_before_devices(self) -> None:
        """on_configure hooks execute before device tasks start."""
        app = App(name="t", version="1.0.0")
        order: list[str] = []

        @app.on_configure
        def setup() -> None:
            order.append("configure")

        shutdown = asyncio.Event()

        @app.telemetry("probe", interval=0.01)
        async def probe(ctx: DeviceContext) -> dict[str, object]:
            order.append("device")
            ctx._shutdown_event.set()
            return {"v": 1}

        async def trigger() -> None:
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                mqtt=MockMqttClient(),
                settings=make_settings(),
                shutdown_event=shutdown,
                clock=FakeClock(),
            ),
            timeout=5.0,
        )

        assert order[0] == "configure"

    async def test_hook_can_register_telemetry(self) -> None:
        """on_configure hook can register new telemetry via add_telemetry."""
        app = App(name="t", version="1.0.0")
        telem_ran = asyncio.Event()

        async def dynamic_handler() -> dict[str, object]:
            telem_ran.set()
            return {"v": 1}

        @app.on_configure
        def setup() -> None:
            app.add_telemetry("dynamic", dynamic_handler, interval=0.01)

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await telem_ran.wait()
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                mqtt=MockMqttClient(),
                settings=make_settings(),
                shutdown_event=shutdown,
                clock=FakeClock(),
            ),
            timeout=5.0,
        )
        assert telem_ran.is_set()

    async def test_hook_registered_telemetry_with_callable_interval(
        self,
    ) -> None:
        """Telemetry registered in on_configure with callable interval resolves."""
        app = App(name="t", version="1.0.0")
        telem_ran = asyncio.Event()

        async def handler() -> dict[str, object]:
            telem_ran.set()
            return {"v": 1}

        @app.on_configure
        def setup() -> None:
            app.add_telemetry("dynamic", handler, interval=lambda s: 0.01)

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await telem_ran.wait()
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                mqtt=MockMqttClient(),
                settings=make_settings(),
                shutdown_event=shutdown,
                clock=FakeClock(),
            ),
            timeout=5.0,
        )
        assert telem_ran.is_set()
