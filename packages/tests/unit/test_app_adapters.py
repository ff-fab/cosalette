"""Tests for cosalette App — adapter registration, lifecycle, and DI.

Covers: app.adapter() registration, _is_async_context_manager helper,
adapter lifecycle (__aenter__/__aexit__), factory callable support,
and class-based adapter dependency injection.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from cosalette._adapter_lifecycle import _is_async_context_manager
from cosalette._app import App
from cosalette._context import AppContext, DeviceContext
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import (
    _DummyDryRun,
    _DummyImpl,
    _DummyPort,
    _LifecycleAdapter,
    _LifecycleAdapter2,
    _LifecyclePort,
    _LifecyclePort2,
    _PlainAdapter,
    _PlainPort,
    _TestMySettings,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level helpers used only by adapter tests
# ---------------------------------------------------------------------------


class _SettingsAwareAdapter:
    """Adapter that receives Settings via DI in __init__."""

    def __init__(self, settings: Settings) -> None:
        self.injected_settings = settings

    def do_thing(self) -> str:
        return "aware"


class _CustomSettingsAwareAdapter:
    """Adapter that receives a Settings subclass via DI in __init__."""

    def __init__(self, settings: _TestMySettings) -> None:
        self.custom_value = settings.custom_value

    def do_thing(self) -> str:
        return self.custom_value


class _StringImportableAdapter:
    """Module-level adapter class for string-import DI test.

    Referenced by fully-qualified path so ``_import_string`` can
    resolve it at runtime.
    """

    def __init__(self, settings: Settings) -> None:
        self.topic_prefix = settings.mqtt.topic_prefix or "default"

    def do_thing(self) -> str:
        return self.topic_prefix


# ---------------------------------------------------------------------------
# TestAdapterRegistration
# ---------------------------------------------------------------------------


class TestAdapterRegistration:
    """app.adapter() registration tests.

    Technique: Specification-based Testing — verifying adapter storage,
    duplicate rejection, and dry-run variant capture.
    """

    async def test_registers_adapter(self, app: App) -> None:
        """app.adapter() stores an _AdapterEntry for the port type."""
        app.adapter(_DummyPort, _DummyImpl)
        assert _DummyPort in app._adapters
        assert app._adapters[_DummyPort].impl is _DummyImpl

    async def test_duplicate_port_type_raises(self, app: App) -> None:
        """Registering the same port type twice raises ValueError."""
        app.adapter(_DummyPort, _DummyImpl)
        with pytest.raises(ValueError, match="already registered"):
            app.adapter(_DummyPort, _DummyImpl)

    async def test_dry_run_variant_stored(self, app: App) -> None:
        """dry_run parameter is preserved in the adapter entry."""
        app.adapter(_DummyPort, _DummyImpl, dry_run=_DummyDryRun)
        entry = app._adapters[_DummyPort]
        assert entry.impl is _DummyImpl
        assert entry.dry_run is _DummyDryRun

    async def test_adapter_factory_fail_fast_bad_signature(self, app: App) -> None:
        """Factory callable with un-annotated params raises TypeError at registration.

        Technique: Error Guessing — verifying that a factory callable
        whose parameter lacks a type annotation is rejected eagerly
        during adapter() rather than at runtime resolution.
        """

        # Arrange
        def bad_factory(x):  # noqa: ANN001
            return _DummyImpl()

        # Act & Assert
        with pytest.raises(TypeError, match="no type annotation"):
            app.adapter(_DummyPort, bad_factory)

    async def test_adapter_dry_run_factory_fail_fast(self, app: App) -> None:
        """dry_run factory callable with un-annotated params raises TypeError.

        Technique: Error Guessing — the dry_run variant receives the
        same fail-fast validation as the primary impl.
        """

        # Arrange
        def bad_dry_run(x):  # noqa: ANN001
            return _DummyDryRun()

        # Act & Assert
        with pytest.raises(TypeError, match="no type annotation"):
            app.adapter(_DummyPort, _DummyImpl, dry_run=bad_dry_run)

    async def test_adapter_class_no_validation(self, app: App) -> None:
        """A plain zero-arg class passes injection plan validation with an empty plan.

        Technique: Specification-based Testing — classes now go through
        build_injection_plan at registration, but a zero-arg __init__
        produces an empty plan and registers without error.
        """
        # Act — should not raise even though __init__ has un-annotated self
        app.adapter(_DummyPort, _DummyImpl)

        # Assert
        assert _DummyPort in app._adapters

    async def test_adapter_string_no_validation(self, app: App) -> None:
        """A string import path does not trigger factory signature validation.

        Technique: Specification-based Testing — strings are lazily
        imported at resolution time, so no validation at registration.
        """
        # Act — should not raise
        app.adapter(_DummyPort, "cosalette._mqtt:NullMqttClient")

        # Assert
        assert _DummyPort in app._adapters


# ---------------------------------------------------------------------------
# TestIsAsyncContextManager — helper function tests
# ---------------------------------------------------------------------------


class TestIsAsyncContextManager:
    """Tests for _is_async_context_manager() duck-type detection.

    Technique: Specification-based Testing — verifying positive and
    negative cases for the async context manager protocol check.
    The function checks for ``__aenter__`` and ``__aexit__`` via
    ``hasattr``, not ABC registration (duck-typing is more inclusive).
    """

    def test_detects_full_async_cm(self) -> None:
        """Object with both __aenter__ and __aexit__ is detected."""

        class AsyncCM:
            async def __aenter__(self) -> AsyncCM:
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        assert _is_async_context_manager(AsyncCM()) is True

    def test_rejects_plain_object(self) -> None:
        """Plain object with no CM methods is rejected."""

        class Plain:
            pass

        assert _is_async_context_manager(Plain()) is False

    def test_rejects_sync_context_manager(self) -> None:
        """Sync-only CM (__enter__/__exit__) is not async and is rejected."""

        class SyncCM:
            def __enter__(self) -> SyncCM:
                return self

            def __exit__(self, *args: object) -> None:
                pass

        assert _is_async_context_manager(SyncCM()) is False

    def test_rejects_partial_aenter_only(self) -> None:
        """Object with only __aenter__ (missing __aexit__) is rejected."""

        class PartialEnter:
            async def __aenter__(self) -> PartialEnter:
                return self

        assert _is_async_context_manager(PartialEnter()) is False

    def test_rejects_partial_aexit_only(self) -> None:
        """Object with only __aexit__ (missing __aenter__) is rejected."""

        class PartialExit:
            async def __aexit__(self, *args: object) -> None:
                pass

        assert _is_async_context_manager(PartialExit()) is False

    def test_detects_contextlib_async_cm(self) -> None:
        """@asynccontextmanager-based CM is detected as async CM."""

        @asynccontextmanager
        async def my_cm() -> AsyncIterator[None]:
            yield

        assert _is_async_context_manager(my_cm()) is True


# ---------------------------------------------------------------------------
# TestAdapterLifecycle — adapter async CM lifecycle tests
# ---------------------------------------------------------------------------


class TestAdapterLifecycle:
    """Adapter lifecycle protocol integration tests.

    Technique: Integration Testing — verifying that adapters implementing
    ``__aenter__``/``__aexit__`` are auto-managed by ``_run_async()``
    via an ``AsyncExitStack``.  Adapters are entered BEFORE the user
    lifespan and exited AFTER it (LIFO).
    """

    @pytest.mark.anyio
    async def test_lifecycle_adapter_entered_and_exited(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Adapter with __aenter__/__aexit__ is auto-entered and auto-exited.

        Coordination: shutdown immediately, verify enter/exit after
        _run_async completes.
        """
        log: list[str] = []
        adapter = _LifecycleAdapter(name="db", log=log)

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: adapter)

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert adapter.entered
        assert adapter.exited
        assert log == ["db:enter", "db:exit"]

    @pytest.mark.anyio
    async def test_adapter_aenter_before_lifespan_and_aexit_after(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Adapter __aenter__ runs before lifespan startup; __aexit__ after teardown.

        Technique: State-based Testing — event log captures exact
        ordering of adapter enter, lifespan startup, lifespan teardown,
        adapter exit.
        """
        log: list[str] = []
        adapter = _LifecycleAdapter(name="adapter", log=log)

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            log.append("lifespan:startup")
            yield
            log.append("lifespan:teardown")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        app.adapter(_LifecyclePort, lambda: adapter)

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert log == [
            "adapter:enter",
            "lifespan:startup",
            "lifespan:teardown",
            "adapter:exit",
        ]

    @pytest.mark.anyio
    async def test_mixed_adapters_lifecycle_and_plain(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Non-lifecycle adapters pass through unchanged; lifecycle ones managed.

        Technique: Specification-based Testing — verifying that adapters
        without __aenter__/__aexit__ are still resolved and usable.
        """
        log: list[str] = []
        lc_adapter = _LifecycleAdapter(name="lc", log=log)
        plain_adapter = _PlainAdapter()

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: lc_adapter)
        app.adapter(_PlainPort, lambda: plain_adapter)

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Lifecycle adapter was managed
        assert lc_adapter.entered
        assert lc_adapter.exited
        # Plain adapter is fine — no lifecycle methods
        assert plain_adapter.compute() == 42

    @pytest.mark.anyio
    async def test_error_during_aenter_cleans_up_already_entered(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If adapter __aenter__ raises, already-entered adapters get __aexit__.

        Technique: Error Guessing — AsyncExitStack guarantees cleanup
        of previously entered CMs when a subsequent enter fails.
        """
        log: list[str] = []
        good_adapter = _LifecycleAdapter(name="good", log=log)

        class _FailingAdapter:
            def get_value(self) -> str:
                return "fail"

            async def __aenter__(self) -> _FailingAdapter:
                log.append("fail:enter")
                msg = "adapter startup failed"
                raise RuntimeError(msg)

            async def __aexit__(self, *args: object) -> None:
                log.append("fail:exit")

        failing = _FailingAdapter()

        app = App(name="testapp", version="1.0.0")
        # Registration order matters — good first, then failing
        app.adapter(_LifecyclePort, lambda: good_adapter)
        app.adapter(_LifecyclePort2, lambda: failing)  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        with pytest.raises(RuntimeError, match="adapter startup failed"):
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        # Good adapter was entered then exited (cleanup)
        assert "good:enter" in log
        assert "good:exit" in log
        # Failing adapter attempted enter but never exited
        assert "fail:enter" in log
        assert "fail:exit" not in log

    @pytest.mark.anyio
    async def test_error_during_aexit_propagates(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """RuntimeError in adapter __aexit__ propagates from AsyncExitStack.

        Technique: Error Guessing — AsyncExitStack does not suppress
        ``__aexit__`` exceptions; they propagate to the caller.
        """

        class _ExitErrorAdapter:
            def get_value(self) -> str:
                return "exitfail"

            async def __aenter__(self) -> _ExitErrorAdapter:
                return self

            async def __aexit__(self, *args: object) -> None:
                msg = "exit cleanup failed"
                raise RuntimeError(msg)

        adapter = _ExitErrorAdapter()
        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: adapter)  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        # The __aexit__ error will propagate from AsyncExitStack
        # unless suppressed; RuntimeError from __aexit__ propagates.
        # The app should still complete — the stack unwinds regardless.
        with pytest.raises(RuntimeError, match="exit cleanup failed"):
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

    @pytest.mark.anyio
    async def test_lifo_exit_ordering(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """With 2+ lifecycle adapters, exit order is reverse of entry (LIFO).

        Technique: State-based Testing — AsyncExitStack guarantees
        LIFO ordering.  We verify the log records match expectations.
        """
        log: list[str] = []
        adapter1 = _LifecycleAdapter(name="first", log=log)
        adapter2 = _LifecycleAdapter2(log=log)

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: adapter1)
        app.adapter(_LifecyclePort2, lambda: adapter2)  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Entry: first, adapter2 (registration order)
        # Exit: adapter2, first (LIFO — reverse of entry)
        assert log == [
            "first:enter",
            "adapter2:enter",
            "adapter2:exit",
            "first:exit",
        ]

    @pytest.mark.anyio
    async def test_coexistence_with_lifespan_and_lifo(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifecycle adapters + user lifespan coexist with correct ordering.

        Full ordering: adapter1:enter → adapter2:enter → lifespan:startup
        → lifespan:teardown → adapter2:exit → adapter1:exit.
        """
        log: list[str] = []
        adapter1 = _LifecycleAdapter(name="first", log=log)
        adapter2 = _LifecycleAdapter2(log=log)

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            log.append("lifespan:startup")
            yield
            log.append("lifespan:teardown")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        app.adapter(_LifecyclePort, lambda: adapter1)
        app.adapter(_LifecyclePort2, lambda: adapter2)  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert log == [
            "first:enter",
            "adapter2:enter",
            "lifespan:startup",
            "lifespan:teardown",
            "adapter2:exit",
            "first:exit",
        ]

    @pytest.mark.anyio
    async def test_no_lifecycle_adapters_works_normally(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """When no adapters implement lifecycle protocol, everything works as before.

        Technique: Regression Testing — verifying that the lifecycle
        feature doesn't break the existing no-adapter path.
        """
        phases: list[str] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            phases.append("startup")
            yield
            phases.append("teardown")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        # Register a plain (non-lifecycle) adapter
        app.adapter(_PlainPort, _PlainAdapter)

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert phases == ["startup", "teardown"]

    @pytest.mark.anyio
    async def test_health_shutdown_runs_even_when_aexit_raises(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Health offline messages are published even if adapter __aexit__ raises.

        Technique: Error Guessing — the ``try/finally`` in ``_run_async``
        guarantees ``health_reporter.shutdown()`` runs regardless of
        adapter lifecycle exceptions.
        """

        class _ExitBoomAdapter:
            def get_value(self) -> str:
                return "boom"

            async def __aenter__(self) -> _ExitBoomAdapter:
                return self

            async def __aexit__(self, *args: object) -> None:
                msg = "boom in aexit"
                raise RuntimeError(msg)

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: _ExitBoomAdapter())  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        with pytest.raises(RuntimeError, match="boom in aexit"):
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        # Health shutdown publishes "offline" to status topic even though
        # the adapter __aexit__ raised.
        offline_messages = [
            (t, p) for t, p, _r, _q in mock_mqtt.published if p == "offline"
        ]
        assert len(offline_messages) > 0, "health_reporter.shutdown() was skipped"

    @pytest.mark.anyio
    async def test_shared_adapter_instance_entered_once(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Same adapter instance registered for two ports is entered only once.

        Technique: Edge-case Testing — ``_enter_lifecycle_adapters``
        deduplicates by ``id()`` so a shared instance doesn't get
        double-entered (most async CMs are not re-entrant).
        """
        log: list[str] = []

        class _SharedAdapter:
            """Satisfies both _LifecyclePort and _LifecyclePort2."""

            def get_value(self) -> str:
                return "shared"

            def label(self) -> str:
                return "shared"

            async def __aenter__(self) -> _SharedAdapter:
                log.append("shared:enter")
                return self

            async def __aexit__(self, *args: object) -> None:
                log.append("shared:exit")

        shared = _SharedAdapter()

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: shared)
        app.adapter(_LifecyclePort2, lambda: shared)  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Entered and exited exactly once despite two port registrations
        assert log == ["shared:enter", "shared:exit"]

    @pytest.mark.anyio
    async def test_non_callable_aenter_raises_type_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Adapter with non-callable __aenter__ gets a clear TypeError.

        Technique: Error Guessing — defensive check in
        ``_enter_lifecycle_adapters`` catches mis-implementations early
        instead of producing an opaque error from ``AsyncExitStack``.
        """

        class _BadAdapter:
            __aenter__ = "not a method"  # type: ignore[assignment]
            __aexit__ = "also not a method"  # type: ignore[assignment]

            def get_value(self) -> str:
                return "bad"

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, lambda: _BadAdapter())  # type: ignore[arg-type]

        shutdown = asyncio.Event()
        shutdown.set()

        with pytest.raises(TypeError, match="has __aenter__ but it's not callable"):
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )


# ---------------------------------------------------------------------------
# TestAdapterFactoryCallable — factory callable support
# ---------------------------------------------------------------------------


class TestAdapterFactoryCallable:
    """app.adapter() with factory callable support.

    Technique: Specification-based Testing — verifying that factory
    callables (non-type callables) are accepted and invoked during
    adapter resolution, complementing class-based registration.
    """

    async def test_factory_callable_registration(self, app: App) -> None:
        """A lambda returning an adapter instance is accepted and resolved."""
        app.adapter(_DummyPort, lambda: _DummyImpl())

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyImpl)

    async def test_factory_callable_with_constructor_args(self, app: App) -> None:
        """Factory callable can pass constructor arguments to the adapter."""

        class PinAdapter:
            def __init__(self, pin: int) -> None:
                self.pin = pin

            def do_thing(self) -> str:
                return f"pin-{self.pin}"

        app.adapter(_DummyPort, lambda: PinAdapter(pin=17))

        resolved = app._resolve_adapters(make_settings())
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, PinAdapter)
        assert adapter.pin == 17

    async def test_factory_callable_for_dry_run(self) -> None:
        """Factory callable used as dry_run variant is resolved in dry-run mode."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, _DummyImpl, dry_run=lambda: _DummyDryRun())

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_class_impl_factory_dry_run(self) -> None:
        """Class for impl, factory callable for dry_run — mixed registration."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, _DummyImpl, dry_run=lambda: _DummyDryRun())

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_factory_impl_class_dry_run(self) -> None:
        """Factory callable for impl, class for dry_run — mixed registration."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, lambda: _DummyImpl(), dry_run=_DummyDryRun)

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_factory_impl_resolves_in_normal_mode(self) -> None:
        """Factory impl is used (not dry_run) when dry_run mode is off."""
        app = App(name="testapp", version="1.0.0")
        app.adapter(_DummyPort, lambda: _DummyImpl(), dry_run=_DummyDryRun)

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyImpl)

    async def test_string_impl_factory_dry_run(self) -> None:
        """String import for impl, factory callable for dry_run."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(
            _DummyPort,
            "cosalette._mqtt:NullMqttClient",
            dry_run=lambda: _DummyDryRun(),
        )

        resolved = app._resolve_adapters(make_settings())
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_factory_with_settings_injection(self, app: App) -> None:
        """Factory callable accepting settings receives the parsed instance.

        Technique: Specification-based Testing — verifying that the
        injection system wires settings into adapter factories.
        """

        class ConfiguredAdapter:
            def __init__(self, name: str) -> None:
                self.name = name

            def do_thing(self) -> str:
                return self.name

        def make_adapter(settings: Settings) -> ConfiguredAdapter:
            return ConfiguredAdapter(name=settings.mqtt.topic_prefix or "default")

        app.adapter(_DummyPort, make_adapter)

        test_settings = make_settings()
        resolved = app._resolve_adapters(test_settings)
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, ConfiguredAdapter)

    async def test_factory_with_settings_subclass_injection(self, app: App) -> None:
        """Factory annotated with Settings subclass gets the subclass instance.

        Technique: Specification-based Testing — verifying subclass
        resolution mirrors device handler injection.
        """

        class ConfiguredAdapter:
            def __init__(self, v: str) -> None:
                self.v = v

            def do_thing(self) -> str:
                return self.v

        def make_adapter(settings: _TestMySettings) -> ConfiguredAdapter:
            return ConfiguredAdapter(v=settings.custom_value)

        app.adapter(_DummyPort, make_adapter)

        test_settings = _TestMySettings()
        resolved = app._resolve_adapters(test_settings)
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, ConfiguredAdapter)
        assert adapter.v == "hello"

    async def test_zero_arg_factory_still_works(self, app: App) -> None:
        """Zero-arg factory callable remains backward compatible.

        Technique: Specification-based Testing — regression test
        ensuring existing zero-arg factories are unaffected.
        """
        app.adapter(_DummyPort, lambda: _DummyImpl())

        test_settings = make_settings()
        resolved = app._resolve_adapters(test_settings)
        assert isinstance(resolved[_DummyPort], _DummyImpl)

    async def test_factory_with_unknown_type_raises(self, app: App) -> None:
        """Factory requesting an unavailable type fails at registration time.

        Technique: Error Guessing — verifying that a factory callable
        whose parameter type cannot be resolved produces a descriptive
        TypeError at ``app.adapter()`` time (fail-fast), rather than
        deferring the error to runtime adapter resolution.
        """

        class UnknownDep:
            pass

        def bad_factory(dep: UnknownDep) -> _DummyImpl:
            return _DummyImpl()

        with pytest.raises(TypeError, match="unresolvable annotation"):
            app.adapter(_DummyPort, bad_factory)


# ---------------------------------------------------------------------------
# TestAdapterClassDI — class-based adapter DI support
# ---------------------------------------------------------------------------


class TestAdapterClassDI:
    """app.adapter() with class-based dependency injection.

    Technique: Specification-based Testing — verifying that adapter
    classes whose ``__init__`` declares a ``Settings``-typed parameter
    receive the parsed settings instance automatically, just like
    factory callables.
    """

    async def test_class_with_settings_injection(self, app: App) -> None:
        """Class with Settings __init__ param gets auto-injected."""
        app.adapter(_DummyPort, _SettingsAwareAdapter)

        test_settings = make_settings()
        resolved = app._resolve_adapters(test_settings)
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, _SettingsAwareAdapter)
        assert adapter.injected_settings is test_settings

    async def test_class_with_settings_subclass_injection(self, app: App) -> None:
        """Class with Settings subclass __init__ param gets injected."""
        app.adapter(_DummyPort, _CustomSettingsAwareAdapter)

        test_settings = _TestMySettings()
        resolved = app._resolve_adapters(test_settings)
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, _CustomSettingsAwareAdapter)
        assert adapter.custom_value == "hello"

    async def test_class_zero_arg_backward_compat(self, app: App) -> None:
        """Class with zero-arg ``__init__`` still works (backward compatible)."""
        app.adapter(_DummyPort, _DummyImpl)

        resolved = app._resolve_adapters(make_settings())
        impl = resolved[_DummyPort]
        assert isinstance(impl, _DummyImpl)
        assert impl.do_thing() == "real"

    async def test_class_no_init_backward_compat(self, app: App) -> None:
        """Class with no explicit ``__init__`` still works."""

        class BareAdapter:
            def do_thing(self) -> str:
                return "bare"

        app.adapter(_DummyPort, BareAdapter)

        resolved = app._resolve_adapters(make_settings())
        impl = resolved[_DummyPort]
        assert isinstance(impl, BareAdapter)
        assert impl.do_thing() == "bare"

    async def test_class_fail_fast_unknown_type(self, app: App) -> None:
        """Class declaring unknown type in ``__init__`` fails at registration time.

        Technique: Error Guessing — verifying that classes with
        unresolvable ``__init__`` parameter types are rejected
        eagerly, consistent with factory callable validation.
        """

        class UnknownDep:
            pass

        class BadAdapter:
            def __init__(self, dep: UnknownDep) -> None:
                self.dep = dep

            def do_thing(self) -> str:
                return "bad"

        with pytest.raises(TypeError, match="unresolvable annotation"):
            app.adapter(_DummyPort, BadAdapter)

    async def test_string_import_with_settings_injection(self, app: App) -> None:
        """Lazy import string resolving to a class with Settings param gets DI."""
        app.adapter(
            _DummyPort,
            "tests.unit.test_app_adapters:_StringImportableAdapter",
        )

        test_settings = make_settings()
        resolved = app._resolve_adapters(test_settings)
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, _StringImportableAdapter)
        assert adapter.topic_prefix == (test_settings.mqtt.topic_prefix or "default")


# ---------------------------------------------------------------------------
# TestSignalHandlerTimingGap — signal handlers installed before adapter entry
# ---------------------------------------------------------------------------


class TestSignalHandlerTimingGap:
    """Signal handlers must be installed before adapter __aenter__.

    Technique: State-based Testing — an event log records the call
    ordering of ``_install_signal_handlers`` and adapter
    ``__aenter__``.  The handler install must appear first.

    Background: Before this fix, signal handlers were installed
    *inside* the ``_enter_lifecycle_adapters`` block, leaving a
    window where SIGTERM/SIGINT during a slow ``__aenter__`` would
    trigger Python's default handler (immediate termination, no
    cleanup).  Moving ``_install_signal_handlers()`` before the
    ``async with`` block closes this gap.
    """

    @pytest.mark.anyio
    async def test_signal_handler_installed_before_adapter_aenter(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Signal handlers are installed before adapter __aenter__ runs.

        Technique: State-based Testing — an event log records the
        ordering of ``_install_signal_handlers`` and adapter
        ``__aenter__``.  The handler install must appear first.
        """
        ordering: list[str] = []
        entry_done = asyncio.Event()

        class _OrderProbeAdapter:
            """Adapter that logs when __aenter__ is called."""

            def get_value(self) -> str:
                return "probe"

            async def __aenter__(self) -> _OrderProbeAdapter:
                ordering.append("adapter:aenter")
                entry_done.set()
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, _OrderProbeAdapter)

        # Patch _install_signal_handlers to record its call while
        # still accepting the injected shutdown_event.
        original_install = app._install_signal_handlers

        def _recording_install(
            shutdown_event: asyncio.Event | None,
        ) -> asyncio.Event:
            ordering.append("signal_handlers:install")
            return original_install(shutdown_event)

        app._install_signal_handlers = _recording_install  # type: ignore[assignment]

        shutdown = asyncio.Event()

        async def _set_shutdown_after_entry() -> None:
            await entry_done.wait()
            shutdown.set()

        trigger = asyncio.create_task(_set_shutdown_after_entry())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            trigger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trigger
            trigger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trigger

        assert "signal_handlers:install" in ordering
        assert "adapter:aenter" in ordering

        install_idx = ordering.index("signal_handlers:install")
        aenter_idx = ordering.index("adapter:aenter")
        assert install_idx < aenter_idx, (
            f"Signal handlers installed at index {install_idx} but adapter "
            f"__aenter__ ran at index {aenter_idx}. "
            f"Full ordering: {ordering}"
        )


# ---------------------------------------------------------------------------
# TestAdapterEntryShutdownCancellation — shutdown during slow __aenter__
# ---------------------------------------------------------------------------


class TestAdapterEntryShutdownCancellation:
    """Shutdown signal during a slow adapter ``__aenter__`` must abort cleanly.

    Technique: Behaviour-based Testing — a slow adapter simulates a
    blocking ``__aenter__``.  Setting the shutdown event mid-entry
    verifies the app exits promptly without hanging, and that
    already-entered adapters receive proper ``__aexit__`` cleanup.

    Background: ``_enter_lifecycle_adapters`` now races each
    ``stack.enter_async_context(adapter)`` against
    ``shutdown_event.wait()``.  If shutdown wins the race, the entry
    task is cancelled and remaining adapters are skipped.  The
    ``yield`` still executes so the run phase sees the already-set
    event and exits immediately, then the ``AsyncExitStack`` calls
    ``__aexit__`` on any adapters that *did* enter.
    """

    @pytest.mark.anyio
    async def test_shutdown_during_slow_adapter_entry_aborts_remaining(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """App exits cleanly when shutdown fires during a slow __aenter__.

        Technique: Behaviour-based Testing — a slow adapter blocks in
        ``__aenter__`` until cancelled.  The shutdown event is set
        after a short delay.  The app must exit within the timeout
        instead of hanging indefinitely.
        """
        aenter_cancelled = False
        entry_started = asyncio.Event()

        class _SlowAdapter:
            """Adapter whose __aenter__ blocks until cancelled."""

            def get_value(self) -> str:
                return "slow"

            async def __aenter__(self) -> _SlowAdapter:
                nonlocal aenter_cancelled
                entry_started.set()
                try:
                    await asyncio.sleep(3600)  # "forever"
                except asyncio.CancelledError:
                    aenter_cancelled = True
                    raise
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, _SlowAdapter)

        shutdown = asyncio.Event()

        async def _set_shutdown_when_entry_starts() -> None:
            await entry_started.wait()
            shutdown.set()

        trigger = asyncio.create_task(_set_shutdown_when_entry_starts())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            trigger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trigger

        assert aenter_cancelled, (
            "Slow adapter __aenter__ should have been cancelled by shutdown"
        )

    @pytest.mark.anyio
    async def test_already_entered_adapters_cleaned_up_on_shutdown(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Adapters that entered before shutdown get __aexit__ called.

        Technique: State-based Testing — two adapters are registered:
        the first enters instantly, the second blocks.  Shutdown fires
        during the second's ``__aenter__``.  We verify the first
        adapter's ``__aexit__`` was called (AsyncExitStack cleanup).
        """
        log: list[str] = []

        class _FastAdapter:
            """Adapter that enters and exits instantly, logging both."""

            def get_value(self) -> str:
                return "fast"

            async def __aenter__(self) -> _FastAdapter:
                log.append("fast:enter")
                return self

            async def __aexit__(self, *args: object) -> None:
                log.append("fast:exit")

        slow_entry_started = asyncio.Event()

        class _SlowAdapter2:
            """Adapter whose __aenter__ blocks until cancelled."""

            def label(self) -> str:
                return "slow2"

            async def __aenter__(self) -> _SlowAdapter2:
                log.append("slow:enter-start")
                slow_entry_started.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    log.append("slow:enter-cancelled")
                    raise
                log.append("slow:enter-done")
                return self

            async def __aexit__(self, *args: object) -> None:
                log.append("slow:exit")

        app = App(name="testapp", version="1.0.0")
        # Register both adapters — fast first, slow second.
        app.adapter(_LifecyclePort, _FastAdapter)
        app.adapter(_LifecyclePort2, _SlowAdapter2)

        shutdown = asyncio.Event()

        async def _set_shutdown_when_slow_starts() -> None:
            await slow_entry_started.wait()
            shutdown.set()

        trigger = asyncio.create_task(_set_shutdown_when_slow_starts())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            trigger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trigger

        # Fast adapter must have been entered and exited.
        assert "fast:enter" in log, f"Fast adapter should have entered. Log: {log}"
        assert "fast:exit" in log, (
            f"Fast adapter __aexit__ should have been called during "
            f"AsyncExitStack cleanup. Log: {log}"
        )

        # Slow adapter started entry but was cancelled.
        assert "slow:enter-start" in log, (
            f"Slow adapter should have started. Log: {log}"
        )
        assert "slow:enter-cancelled" in log, (
            f"Slow adapter __aenter__ should have been cancelled. Log: {log}"
        )
        # Slow adapter never completed entry → __aexit__ must NOT be called.
        assert "slow:exit" not in log, (
            f"Slow adapter __aexit__ should NOT be called since it never "
            f"finished entering. Log: {log}"
        )

    @pytest.mark.anyio
    async def test_pre_set_shutdown_event_fast_adapters_still_enter(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A pre-set shutdown event does not prevent fast adapter entry.

        Technique: State-based Testing — the shutdown event is set
        *before* ``_run_async`` is called.  Because a fast adapter's
        ``__aenter__`` completes in one event-loop step (no internal
        await), its entry task finishes before the shutdown task is
        scheduled.  The run phase sees the event and exits promptly.
        """
        log: list[str] = []

        class _TrackedAdapter:
            """Adapter that logs __aenter__ / __aexit__."""

            def get_value(self) -> str:
                return "tracked"

            async def __aenter__(self) -> _TrackedAdapter:
                log.append("tracked:enter")
                return self

            async def __aexit__(self, *args: object) -> None:
                log.append("tracked:exit")

        app = App(name="testapp", version="1.0.0")
        app.adapter(_LifecyclePort, _TrackedAdapter)

        shutdown = asyncio.Event()
        shutdown.set()  # pre-set — fast adapters still win the race

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert "tracked:enter" in log, (
            f"Fast adapter must enter even with pre-set shutdown. Log: {log}"
        )
        assert "tracked:exit" in log, f"Fast adapter must exit (cleanup). Log: {log}"


# ---------------------------------------------------------------------------
# TestRunAsyncAdapters — adapter resolution in _run_async
# ---------------------------------------------------------------------------


class TestRunAsyncAdapters:
    """Adapter resolution and dry-run swap integration tests."""

    async def test_adapter_resolution_in_device(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Registered adapter is available via DeviceContext.adapter().

        Technique: register adapter before run, verify device can
        resolve it at runtime.
        """
        app = App(name="testapp", version="1.0.0")
        resolved_adapter: list[object] = []
        device_done = asyncio.Event()

        app.adapter(_DummyPort, _DummyImpl)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(_DummyPort)
            resolved_adapter.append(adapter)
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
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

        assert len(resolved_adapter) == 1
        assert isinstance(resolved_adapter[0], _DummyImpl)

    async def test_dry_run_adapter_swap(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """dry_run=True resolves the dry-run adapter variant.

        Technique: create App with dry_run=True, register adapter
        with a dry_run variant, verify the device gets the dry-run
        instance.
        """
        app = App(name="testapp", version="1.0.0", dry_run=True)
        resolved_adapter: list[object] = []
        device_done = asyncio.Event()

        app.adapter(_DummyPort, _DummyImpl, dry_run=_DummyDryRun)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(_DummyPort)
            resolved_adapter.append(adapter)
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
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

        assert len(resolved_adapter) == 1
        assert isinstance(resolved_adapter[0], _DummyDryRun)
