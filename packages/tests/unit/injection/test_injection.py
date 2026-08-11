"""Tests for cosalette._injection — Signature-based dependency injection.

Test Techniques Used:
    - Specification-based Testing: Verify injection plan building rules
    - Boundary Value Analysis: Zero-parameter, single-parameter, multi-parameter,
      empty providers map
    - Error Guessing: Missing annotations, unknown types, unsupported param kinds,
      masked annotation-evaluation failures, async dependencies, dependency cycles
    - Equivalence Partitioning: Parameter kinds (allowed vs rejected), hint
      sources (function / class / callable instance / partial)
    - Branch/Condition Coverage: get_type_hints path vs eval() fallback,
      cycle guard positive and negative paths
    - Integration Testing: Full injection with DeviceContext + resolve_kwargs
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Annotated, Any, Protocol, runtime_checkable

import pytest

from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._injection import (
    _DEP_CHAIN,
    _SENTINEL,
    _find_subclass_instance,
    _hint_source_for,
    _resolve_single,
    _try_resolve_single,
    build_injection_plan,
    build_providers,
    resolve_kwargs,
    resolve_request_kwargs,
)
from cosalette._runners._stream_types import Stream
from cosalette._settings import Settings
from cosalette.di import Depends, Optional
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.fixtures import pep563_di

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@runtime_checkable
class _UnknownPort(Protocol):
    """Module-level protocol for injection plan tests.

    Defined at module level so get_type_hints() can resolve it
    (PEP 563 string annotations require module-global resolution).
    """

    def do(self) -> str: ...


@runtime_checkable
class _SomePort(Protocol):
    """Module-level protocol for resolve_kwargs adapter tests."""

    def action(self) -> str: ...


class _SomeImpl:
    """Concrete adapter for resolve_kwargs tests."""

    def action(self) -> str:
        return "ok"


@runtime_checkable
class _APort(Protocol):
    """Module-level protocol for build_providers tests."""

    def go(self) -> None: ...


class _AImpl:
    """Concrete adapter for build_providers tests."""

    def go(self) -> None: ...


class _CustomSettings(Settings):
    """Settings subclass for subclass-injection tests."""

    model_config = {"extra": "ignore"}
    custom_value: str = "hello"


def _make_device_context(
    *,
    name: str = "testdevice",
    adapters: dict[type, object] | None = None,
) -> DeviceContext:
    """Create a DeviceContext with sensible test defaults."""
    return DeviceContext(
        name=name,
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="testapp",
        shutdown_event=asyncio.Event(),
        adapters=adapters or {},
        clock=FakeClock(),
    )


# ---------------------------------------------------------------------------
# TestBuildInjectionPlan
# ---------------------------------------------------------------------------


class TestBuildInjectionPlan:
    """build_injection_plan() unit tests.

    Technique: Specification-based Testing — verifying the plan builder's
    contract for different parameter configurations.
    """

    def test_zero_params_returns_empty_plan(self) -> None:
        """A zero-parameter function produces an empty injection plan."""

        async def handler() -> dict[str, object]:
            return {}

        plan = build_injection_plan(handler)
        assert plan == []

    def test_single_ctx_param(self) -> None:
        """A handler requesting only DeviceContext gets a single-entry plan."""

        async def handler(ctx: DeviceContext) -> None: ...

        plan = build_injection_plan(handler)
        assert len(plan) == 1
        assert plan[0] == ("ctx", DeviceContext)

    def test_single_settings_param(self) -> None:
        """A handler requesting only Settings gets a single-entry plan."""

        async def handler(settings: Settings) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("settings", Settings)]

    def test_single_logger_param(self) -> None:
        """A handler requesting only a Logger gets a single-entry plan."""

        async def handler(logger: logging.Logger) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("logger", logging.Logger)]

    def test_single_clock_param(self) -> None:
        """A handler requesting only ClockPort gets a single-entry plan."""

        async def handler(clock: ClockPort) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("clock", ClockPort)]

    def test_single_event_param(self) -> None:
        """A handler requesting only asyncio.Event gets a single-entry plan."""

        async def handler(shutdown: asyncio.Event) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("shutdown", asyncio.Event)]

    def test_multi_params(self) -> None:
        """A handler requesting multiple types gets all of them in order."""

        async def handler(
            ctx: DeviceContext,
            logger: logging.Logger,
        ) -> None: ...

        plan = build_injection_plan(handler)
        assert len(plan) == 2
        assert plan[0] == ("ctx", DeviceContext)
        assert plan[1] == ("logger", logging.Logger)

    def test_unknown_type_accepted_in_plan(self) -> None:
        """Unknown types are accepted in the plan (adapter types).

        Resolution failure is deferred to call time — adapters
        may be registered after devices.
        """

        async def handler(port: _UnknownPort) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("port", _UnknownPort)]

    def test_missing_annotation_raises_type_error(self) -> None:
        """A parameter without a type annotation raises TypeError.

        Technique: Error Guessing — fail-fast at registration time.
        """

        async def handler(ctx) -> None: ...  # type: ignore[no-untyped-def]

        with pytest.raises(TypeError, match="no type annotation"):
            build_injection_plan(handler)

    def test_param_name_is_irrelevant(self) -> None:
        """Injection matches by type, not by parameter name.

        Arbitrary parameter names work as long as the annotation is known.
        """

        async def handler(whatever: DeviceContext) -> None: ...

        plan = build_injection_plan(handler)
        assert plan == [("whatever", DeviceContext)]


# ---------------------------------------------------------------------------
# TestResolveKwargs
# ---------------------------------------------------------------------------


class TestResolveKwargs:
    """resolve_kwargs() unit tests.

    Technique: Specification-based Testing — verify type-to-instance
    mapping for all injectable types.
    """

    def test_empty_plan_returns_empty_kwargs(self) -> None:
        """An empty plan resolves to empty kwargs."""
        result = resolve_kwargs([], {})
        assert result == {}

    def test_resolves_device_context(self) -> None:
        """DeviceContext is resolved from providers."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("ctx", DeviceContext)]

        result = resolve_kwargs(plan, providers)
        assert result == {"ctx": ctx}

    def test_resolves_settings(self) -> None:
        """Settings is resolved from providers."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("s", Settings)]

        result = resolve_kwargs(plan, providers)
        assert result["s"] is ctx.settings

    def test_resolves_logger(self) -> None:
        """logging.Logger is resolved with per-device name."""
        ctx = _make_device_context(name="mydev")
        providers = build_providers(ctx, "mydev")
        plan = [("log", logging.Logger)]

        result = resolve_kwargs(plan, providers)
        assert isinstance(result["log"], logging.Logger)
        assert result["log"].name == "cosalette.mydev"

    def test_resolves_clock(self) -> None:
        """ClockPort is resolved from providers."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("clk", ClockPort)]

        result = resolve_kwargs(plan, providers)
        assert result["clk"] is ctx.clock

    def test_resolves_shutdown_event(self) -> None:
        """asyncio.Event is resolved from providers."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("evt", asyncio.Event)]

        result = resolve_kwargs(plan, providers)
        assert isinstance(result["evt"], asyncio.Event)

    def test_resolves_adapter(self) -> None:
        """Adapter port types are resolved from the adapter registry."""
        impl = _SomeImpl()
        ctx = _make_device_context(adapters={_SomePort: impl})
        providers = build_providers(ctx, "testdevice")
        plan = [("port", _SomePort)]

        result = resolve_kwargs(plan, providers)
        assert result["port"] is impl

    def test_unresolvable_type_raises_type_error(self) -> None:
        """An unknown type with no matching provider raises TypeError.

        Technique: Error Guessing — clear error at call time.
        """

        class UnknownType:
            pass

        providers: dict[type, object] = {}
        plan = [("x", UnknownType)]

        with pytest.raises(TypeError, match="Cannot resolve"):
            resolve_kwargs(plan, providers)

    def test_resolves_settings_subclass(self) -> None:
        """A handler annotated with a Settings subclass receives it.

        build_providers adds both the base Settings key and the concrete
        subclass key — resolve_kwargs matches the subclass via
        _is_settings_subclass.
        """
        custom = _CustomSettings()
        ctx = DeviceContext(
            name="sub",
            settings=custom,
            mqtt=MockMqttClient(),
            topic_prefix="test",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
        )
        providers = build_providers(ctx, "sub")
        plan = [("s", _CustomSettings)]

        result = resolve_kwargs(plan, providers)
        assert result["s"] is custom
        assert isinstance(result["s"], _CustomSettings)

    def test_resolves_multiple_types(self) -> None:
        """Multiple types are all resolved in a single call."""
        ctx = _make_device_context(name="multi")
        providers = build_providers(ctx, "multi")
        plan = [
            ("ctx", DeviceContext),
            ("log", logging.Logger),
            ("s", Settings),
        ]

        result = resolve_kwargs(plan, providers)
        assert result["ctx"] is ctx
        assert isinstance(result["log"], logging.Logger)
        assert result["s"] is ctx.settings


# ---------------------------------------------------------------------------
# TestBuildProviders
# ---------------------------------------------------------------------------


class TestBuildProviders:
    """build_providers() unit tests.

    Technique: Specification-based Testing — verify provider map
    contents from a DeviceContext.
    """

    def test_contains_all_known_types(self) -> None:
        """Provider map includes all framework-known injectable types."""
        ctx = _make_device_context(name="dev1")
        providers = build_providers(ctx, "dev1")

        assert DeviceContext in providers
        assert Settings in providers
        assert logging.Logger in providers
        assert ClockPort in providers
        assert asyncio.Event in providers

    def test_logger_has_device_scoped_name(self) -> None:
        """Logger in providers is named cosalette.<device_name>."""
        ctx = _make_device_context(name="mydev")
        providers = build_providers(ctx, "mydev")

        log = providers[logging.Logger]
        assert isinstance(log, logging.Logger)
        assert log.name == "cosalette.mydev"

    def test_adapter_types_included(self) -> None:
        """Adapter port types from the context are in the providers map."""
        impl = _AImpl()
        ctx = _make_device_context(adapters={_APort: impl})
        providers = build_providers(ctx, "test")

        assert _APort in providers
        assert providers[_APort] is impl

    def test_settings_subclass_included(self) -> None:
        """When settings is a subclass, both base and subclass keys exist."""
        custom = _CustomSettings()  # extra="ignore" prevents env var errors
        ctx = DeviceContext(
            name="sub",
            settings=custom,
            mqtt=MockMqttClient(),
            topic_prefix="test",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
        )
        providers = build_providers(ctx, "sub")

        assert Settings in providers
        assert _CustomSettings in providers
        assert providers[_CustomSettings] is custom
        assert providers[Settings] is custom


# ---------------------------------------------------------------------------
# TestParameterKindValidation
# ---------------------------------------------------------------------------


class TestParameterKindValidation:
    """Parameter kind validation in build_injection_plan().

    Technique: Specification-based Testing — the injection system
    dispatches handlers via ``**kwargs``, so only positional-or-keyword
    and keyword-only parameters are compatible.  Positional-only,
    ``*args``, and ``**kwargs`` parameters must be rejected at
    registration time to prevent silent runtime failures.
    """

    async def test_injection_plan_rejects_positional_only_param(self) -> None:
        """Positional-only parameters (``/``) can't be passed as kwargs.

        Technique: Error Guessing — ``def f(x, /)`` would accept
        ``f(x=val)`` at plan-build time but raise ``TypeError`` at
        dispatch time when called with ``**kwargs``.
        """
        # eval is needed because the / syntax can't be expressed in a
        # way that's unambiguous inside a test function using from __future__
        # annotations.  We build the function dynamically.
        ns: dict[str, object] = {}
        exec(  # noqa: S102
            "async def handler(x: int, /, y: str) -> None: ...",
            {"__builtins__": __builtins__},
            ns,
        )
        handler = ns["handler"]

        with pytest.raises(TypeError, match="unsupported kind POSITIONAL_ONLY"):
            build_injection_plan(handler)

    async def test_injection_plan_rejects_var_positional_param(self) -> None:
        """``*args`` parameters can't appear in an injection plan.

        Technique: Error Guessing — ``*args`` has no name→type mapping
        that the DI container can resolve.
        """

        async def handler(topic: str, *args: str) -> None: ...

        with pytest.raises(TypeError, match="unsupported kind VAR_POSITIONAL"):
            build_injection_plan(handler, mqtt_params={"topic"})

    async def test_injection_plan_rejects_var_keyword_param(self) -> None:
        """``**kwargs`` parameters can't appear in an injection plan.

        Technique: Error Guessing — ``**kwargs`` would absorb all
        injected arguments, defeating the purpose of typed DI.
        """

        async def handler(topic: str, **kwargs: str) -> None: ...

        with pytest.raises(TypeError, match="unsupported kind VAR_KEYWORD"):
            build_injection_plan(handler, mqtt_params={"topic"})

    async def test_injection_plan_accepts_keyword_only_param(self) -> None:
        """Keyword-only parameters (after ``*``) are valid for injection.

        Technique: Specification-based — keyword-only params are
        passed via ``**kwargs`` just like regular params, so they
        should be accepted.
        """

        async def handler(topic: str, *, ctx: DeviceContext) -> None: ...

        plan = build_injection_plan(handler, mqtt_params={"topic"})
        assert len(plan) == 1
        assert plan[0] == ("ctx", DeviceContext)

    async def test_positional_only_mqtt_param_is_skipped_before_kind_check(
        self,
    ) -> None:
        """MQTT params are skipped *before* the kind check runs.

        Technique: Specification-based — even if ``topic`` were
        positional-only, it should be silently skipped because it's in
        ``mqtt_params``, not rejected.
        """
        ns: dict[str, object] = {}
        exec(  # noqa: S102
            "async def handler("
            "topic: str, /, payload: str, ctx: DeviceContext"
            ") -> None: ...",
            {"__builtins__": __builtins__, "DeviceContext": DeviceContext},
            ns,
        )
        handler = ns["handler"]

        # topic is positional-only AND in mqtt_params — should be skipped,
        # not rejected.  payload is regular, ctx is regular.
        plan = build_injection_plan(handler, mqtt_params={"topic", "payload"})
        assert len(plan) == 1
        assert plan[0] == ("ctx", DeviceContext)


class TestInjectionEdgeCases:
    """Edge cases for DI resolution.

    Tests couple to private internals (_SENTINEL,
    _find_subclass_instance, _resolve_single) for branch coverage.
    Update if these helpers are refactored.

    Test Techniques Used:
    - Error Guessing: Unusual annotation types
    - Boundary Value Analysis: Empty providers
    - Branch Coverage: Fallback resolution paths
    """

    def test_resolve_annotation_string_fallback_unreachable(self) -> None:
        """String annotation that cannot be resolved raises TypeError.

        Technique: Error Guessing — unresolvable forward reference.
        """

        def handler(x: NonExistentType) -> None: ...  # ty: ignore[unresolved-reference]  # noqa: F821

        with pytest.raises(TypeError, match="unresolvable annotation"):
            build_injection_plan(handler)

    def test_resolve_annotation_non_type_raises(self) -> None:
        """Union type annotation (int | str) is not a concrete type and is rejected.

        Technique: Error Guessing — union annotation produces types.UnionType, not type.
        The error message now redirects to Optional() / Payload() for guidance.
        """

        def handler(x: int | str) -> None: ...  # noqa: ARG001

        with pytest.raises(TypeError, match="Optional\\(\\)"):
            build_injection_plan(handler)

    def test_find_subclass_instance_type_error_in_issubclass(self) -> None:
        """_find_subclass_instance handles TypeError from issubclass gracefully.

        Technique: Branch Coverage — the except TypeError path.
        """
        # A non-type key in providers triggers TypeError in issubclass
        providers: dict[type, Any] = {42: "not a type"}  # type: ignore[dict-item]  # ty: ignore[invalid-assignment]
        result = _find_subclass_instance(Settings, providers)
        assert result is _SENTINEL

    def test_resolve_single_raises_when_no_subclass_match(self) -> None:
        """_resolve_single raises TypeError when no resolution strategy matches.

        Technique: Branch Coverage — exercises the error path after all
        strategies (exact, settings-subclass, adapter-subclass) fail.
        """

        class MyPort(Protocol):
            def do_thing(self) -> None: ...

        class MyAdapter:
            def do_thing(self) -> None: ...

        providers: dict[type, Any] = {MyAdapter: MyAdapter()}
        # MyAdapter is not a subclass of MyPort (structural typing),
        # so all strategies fail and TypeError is raised
        with pytest.raises(TypeError, match="Cannot resolve"):
            _resolve_single("x", MyPort, providers)

    def test_build_injection_plan_get_type_hints_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When get_type_hints() fails, raw param.annotation is used.

        Technique: Branch Coverage — the except path in build_injection_plan.
        """
        monkeypatch.setattr(
            "cosalette._injection.get_type_hints",
            lambda *a, **kw: (_ for _ in ()).throw(Exception("forced")),
        )

        def handler(ctx: DeviceContext) -> None: ...  # noqa: ARG001

        plan = build_injection_plan(handler)
        assert len(plan) == 1
        assert plan[0] == ("ctx", DeviceContext)

    def test_per_device_config_added_to_providers(self) -> None:
        """build_providers includes per_device_config when provided.

        Technique: Specification-based — verify config injection.
        """
        ctx = _make_device_context()
        config = {"key": "value"}
        providers = build_providers(ctx, "test_device", per_device_config=config)
        assert providers[dict] is config


# ---------------------------------------------------------------------------
# TestAnnotationResolutionDiagnostics
# ---------------------------------------------------------------------------


class TestAnnotationResolutionDiagnostics:
    """Annotation resolution must not mask the real failure.

    All handlers under test live in ``tests.fixtures.pep563_di`` because the
    bug only reproduces when annotations are genuinely deferred strings.

    Test Techniques Used:
    - Error Guessing: Marker rejection reported as a missing import
    - Branch/Condition Coverage: get_type_hints path vs eval() fallback path
    """

    def test_async_depends_under_pep563_reports_async_rejection(self) -> None:
        """Async Depends() reports the async rejection, not a missing import.

        Technique: Error Guessing — the deferred marker construction happens
        inside ``get_type_hints()``, which can swallow the real failure.
        """
        # Arrange / Act
        with pytest.raises(TypeError) as exc_info:
            build_injection_plan(pep563_di.handler_with_async_depends)

        # Assert
        assert "Async dependency functions are not supported" in str(exc_info.value)
        assert "unresolvable annotation" not in str(exc_info.value)

    def test_async_depends_via_eval_fallback_reports_async_rejection(self) -> None:
        """The eval() fallback also reports marker rejection verbatim.

        Technique: Branch Coverage — a second unresolvable parameter forces
        ``get_type_hints()`` to bail out, so the ``Depends`` marker is built by
        the per-parameter ``eval()`` fallback instead.
        """
        # Arrange / Act
        with pytest.raises(TypeError) as exc_info:
            build_injection_plan(pep563_di.handler_with_async_depends_and_missing_type)

        # Assert
        assert "Async dependency functions are not supported" in str(exc_info.value)
        assert "unresolvable annotation" not in str(exc_info.value)

    def test_unresolvable_annotation_keeps_cause_and_hint(self) -> None:
        """A genuinely missing type still reports the import hint — with a cause.

        Technique: Error Guessing — the negative case for the fix above: real
        missing imports must keep their actionable message, and now also chain
        the underlying ``NameError`` instead of dropping it via ``from None``.
        """
        # Arrange / Act
        with pytest.raises(TypeError) as exc_info:
            build_injection_plan(pep563_di.handler_with_missing_type)

        # Assert
        message = str(exc_info.value)
        assert "unresolvable annotation" in message
        assert "Ensure the type is imported and available." in message
        assert "NameError" in message
        assert isinstance(exc_info.value.__cause__, NameError)

    def test_get_type_hints_failure_is_logged_at_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A swallowed get_type_hints() failure is visible in the log.

        Technique: Error Guessing — the failure logs at WARNING, not DEBUG.
        """
        # Arrange
        caplog.set_level(logging.WARNING, logger="cosalette._injection")

        # Act
        with pytest.raises(TypeError):
            build_injection_plan(pep563_di.handler_with_missing_type)

        # Assert
        assert any(
            record.levelno == logging.WARNING
            and "get_type_hints() failed" in record.getMessage()
            and "DoesNotExistAnywhere" in record.getMessage()
            for record in caplog.records
        )


class TestHintSourceFor:
    """_hint_source_for() picks the object that carries the annotations.

    Test Techniques Used:
    - Equivalence Partitioning: function / class / callable instance / partial
    - Error Guessing: callable instances resolved against empty globals
    """

    def test_plain_function_is_its_own_hint_source(self) -> None:
        """A regular function carries its own annotations."""

        def handler(ctx: DeviceContext) -> None: ...

        assert _hint_source_for(handler) is handler

    def test_class_resolves_to_init(self) -> None:
        """A class object delegates to ``__init__``."""
        assert _hint_source_for(_SomeImpl) is _SomeImpl.__init__

    def test_callable_instance_resolves_to_class_call(self) -> None:
        """A callable instance delegates to its class's ``__call__``.

        Technique: Error Guessing — instances carry neither
        ``__annotations__`` nor ``__globals__``, so without this the eval()
        fallback ran against an empty namespace.
        """
        dependency = pep563_di.CallableDependency()

        assert _hint_source_for(dependency) is pep563_di.CallableDependency.__call__

    def test_partial_of_callable_instance_resolves_to_class_call(self) -> None:
        """``functools.partial`` is unwrapped before the instance check."""
        wrapped = functools.partial(pep563_di.CallableDependency())

        assert _hint_source_for(wrapped) is pep563_di.CallableDependency.__call__

    def test_callable_instance_plan_resolves_pep563_hints(self) -> None:
        """A callable instance's PEP 563 annotations resolve to real types."""
        # Arrange
        handler = pep563_di.CallableHandler()

        # Act
        plan = build_injection_plan(handler)

        # Assert
        assert plan == [("ctx", DeviceContext), ("port", pep563_di.LocalPort)]

    def test_callable_instance_dependency_is_resolvable(self) -> None:
        """A callable instance used via Depends() receives its injected port."""
        # Arrange
        port = pep563_di.LocalPort()
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        providers[pep563_di.LocalPort] = port
        plan = [
            (
                "value",
                Annotated[str, Depends(pep563_di.CallableDependency())],
            )
        ]

        # Act
        result = resolve_request_kwargs(plan, providers)

        # Assert
        assert result == {"value": "local"}


# ---------------------------------------------------------------------------
# TestAsyncDependsDetection
# ---------------------------------------------------------------------------


class TestAsyncDependsDetection:
    """Depends() rejects every async form it can see.

    Test Techniques Used:
    - Equivalence Partitioning: async def / async gen / async __call__ / sync
    - Error Guessing: awaitable returned by a synchronous callable
    """

    def test_depends_rejects_async_call_dunder(self) -> None:
        """An instance whose ``__call__`` is async is rejected.

        Technique: Error Guessing — ``iscoroutinefunction`` inspects the
        object itself, never its ``__call__`` dunder.
        """
        with pytest.raises(TypeError, match="__call__ method is a coroutine"):
            Depends(pep563_di.AsyncCallableDependency())

    def test_depends_rejects_async_gen_call_dunder(self) -> None:
        """An instance whose ``__call__`` is an async generator is rejected."""
        with pytest.raises(TypeError, match="__call__ method is a coroutine"):
            Depends(pep563_di.AsyncGenCallableDependency())

    def test_depends_accepts_sync_call_dunder(self) -> None:
        """A sync callable instance is still accepted.

        Technique: Equivalence Partitioning — the negative case that must
        keep working.
        """
        dependency = pep563_di.CallableDependency()

        assert Depends(dependency).dependency is dependency

    def test_depends_accepts_plain_function(self) -> None:
        """A plain sync function is still accepted."""
        assert Depends(pep563_di.sync_dep).dependency is pep563_di.sync_dep

    def test_awaitable_result_raises_at_resolution_time(self) -> None:
        """A sync dependency returning a coroutine raises instead of injecting.

        Technique: Error Guessing — ``Depends(lambda: async_fn())`` passes
        every static check, so only resolution can catch it.
        """
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = list(build_injection_plan(pep563_di.handler_with_awaitable_depends))

        # Act / Assert
        with pytest.raises(TypeError, match="returned an awaitable"):
            resolve_request_kwargs(plan, providers)

    def test_awaitable_result_error_names_dependency_and_param(self) -> None:
        """The awaitable rejection names the parameter and the dependency."""
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("value", Annotated[str, Depends(pep563_di.awaitable_dep)])]

        # Act
        with pytest.raises(TypeError) as exc_info:
            resolve_request_kwargs(plan, providers)

        # Assert
        message = str(exc_info.value)
        assert "'value'" in message
        assert "awaitable_dep" in message

    def test_sync_result_is_still_injected(self) -> None:
        """A plain sync dependency result is injected unchanged."""
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("value", Annotated[str, Depends(pep563_di.sync_dep)])]

        # Act
        result = resolve_request_kwargs(plan, providers)

        # Assert
        assert result == {"value": "ok"}


# ---------------------------------------------------------------------------
# TestUnresolvedProviderDiagnostics
# ---------------------------------------------------------------------------


class TestUnresolvedProviderDiagnostics:
    """Unresolved providers, cycles and unhashable deps name the fix.

    Test Techniques Used:
    - Error Guessing: cycles, unhashable callables, missing adapters
    - Branch/Condition Coverage: cycle guard positive and negative paths
    """

    def test_unresolved_message_names_param_type_and_action(self) -> None:
        """The message names the parameter, the missing type and the fix."""
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")

        # Act
        with pytest.raises(TypeError) as exc_info:
            _resolve_single("port", pep563_di.LocalPort, providers)

        # Assert
        message = str(exc_info.value)
        assert "parameter 'port'" in message
        assert "tests.fixtures.pep563_di.LocalPort" in message
        assert "app.adapter(LocalPort" in message

    def test_available_types_are_sorted_qualnames(self) -> None:
        """Available types render as sorted qualnames, not a repr list."""
        # Arrange
        providers: dict[type, Any] = {
            DeviceContext: object(),
            ClockPort: object(),
            logging.Logger: object(),
        }

        # Act
        with pytest.raises(TypeError) as exc_info:
            _resolve_single("port", pep563_di.LocalPort, providers)

        # Assert
        available = str(exc_info.value).split("Available types: ")[1]
        assert "<class" not in available
        rendered = available.split(", ")
        assert rendered == sorted(rendered)
        assert "logging.Logger" in rendered

    def test_empty_providers_render_as_none(self) -> None:
        """An empty providers map renders a placeholder, not an empty string.

        Technique: Boundary Value Analysis — zero available types.
        """
        with pytest.raises(TypeError, match=r"Available types: \(none\)"):
            _resolve_single("port", pep563_di.LocalPort, {})

    def test_generic_annotation_keeps_its_parameters(self) -> None:
        """A parameterised annotation is rendered with its arguments intact.

        Technique: Equivalence Partitioning — generic aliases forward
        ``__qualname__`` to their origin, so they need the repr() branch.
        """
        with pytest.raises(TypeError) as exc_info:
            _resolve_single("s", Stream[int], {})

        assert "Stream[int]" in str(exc_info.value)

    def test_unhashable_dependency_is_named(self) -> None:
        """An unhashable dependency callable is rejected by name.

        Technique: Error Guessing — an unhashable dependency would otherwise
        surface a bare ``TypeError: unhashable type`` from the lru_cache.
        """
        with pytest.raises(TypeError, match="requires a hashable dependency"):
            Depends(pep563_di.UnhashableDependency())

    def test_self_recursive_depends_reports_cycle(self) -> None:
        """A self-referencing Depends() reports a cycle, not a RecursionError."""
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("value", Annotated[str, Depends(pep563_di.self_recursive_dep)])]

        # Act
        with pytest.raises(TypeError) as exc_info:
            resolve_request_kwargs(plan, providers)

        # Assert
        message = str(exc_info.value)
        assert "Circular dependency detected" in message
        assert "self_recursive_dep -> self_recursive_dep" in message

    def test_transitive_depends_cycle_reports_full_chain(self) -> None:
        """A two-node cycle reports the whole chain."""
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("value", Annotated[str, Depends(pep563_di.cycle_dep_a)])]

        # Act
        with pytest.raises(TypeError) as exc_info:
            resolve_request_kwargs(plan, providers)

        # Assert
        assert "cycle_dep_a -> cycle_dep_b -> cycle_dep_a" in str(exc_info.value)

    def test_same_dependency_twice_is_not_a_cycle(self) -> None:
        """Reusing one dependency for two parameters is not a cycle.

        Technique: Branch Coverage — the negative case for the cycle guard;
        the chain must unwind after each parameter.
        """
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [
            ("first", Annotated[str, Depends(pep563_di.sync_dep)]),
            ("second", Annotated[str, Depends(pep563_di.sync_dep)]),
        ]

        # Act
        result = resolve_request_kwargs(plan, providers)

        # Assert
        assert result == {"first": "ok", "second": "ok"}

    def test_cycle_guard_unwinds_after_failure(self) -> None:
        """A failed resolution leaves no residue in the cycle chain.

        Technique: Error Guessing — a leaked ContextVar token would turn the
        next resolution of the same dependency into a false cycle report.
        """
        # Arrange
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        plan = [("value", Annotated[str, Depends(pep563_di.awaitable_dep)])]

        # Act
        with pytest.raises(TypeError, match="returned an awaitable"):
            resolve_request_kwargs(plan, providers)
        with pytest.raises(TypeError, match="returned an awaitable"):
            resolve_request_kwargs(plan, providers)

        # Assert — second call reports the same error, not a cycle
        assert _DEP_CHAIN.get() == ()


# ---------------------------------------------------------------------------
# TestOptionalMarker
# ---------------------------------------------------------------------------


class _StoreProto(Protocol):
    """Minimal store protocol for Optional() injection tests."""

    def get(self, key: str) -> object | None: ...


class _StoreImpl:
    def get(self, key: str) -> object | None:
        return "found"


def _build_plan_from(plan_entries: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """Run plan entries through _resolve_annotation to simulate registration."""
    from cosalette._injection import _resolve_annotation

    result = []
    for name, annotation in plan_entries:
        param = inspect.Parameter(
            name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=annotation,
        )
        resolved = _resolve_annotation(name, param, {name: annotation}, lambda: None)
        result.append((name, resolved))
    return result


class TestOptionalMarker:
    """Optional() binding marker.

    Test Techniques Used:
    - Specification-based: inject when provider present; fall back when absent
    - Boundary Value Analysis: explicit default, no default (→ None)
    - Error Guessing: combining with other markers, bad inner types
    """

    def test_optional_injects_provider_when_registered_union_form(self) -> None:
        """Optional() resolves the provider when registered (T | None inner form)."""
        impl = _StoreImpl()
        ctx = _make_device_context(adapters={_StoreProto: impl})
        providers = build_providers(ctx, "testdevice")

        plan = _build_plan_from([("store", Annotated[_StoreProto | None, Optional()])])
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is impl

    def test_optional_injects_provider_when_registered_bare_form(self) -> None:
        """Optional() resolves the provider when registered (bare T inner form)."""
        impl = _StoreImpl()
        ctx = _make_device_context(adapters={_StoreProto: impl})
        providers = build_providers(ctx, "testdevice")

        plan = _build_plan_from([("store", Annotated[_StoreProto, Optional()])])
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is impl

    def test_optional_falls_back_to_none_when_no_provider(self) -> None:
        """Optional() injects None when no provider is registered and no default."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")

        plan = _build_plan_from([("store", Annotated[_StoreProto | None, Optional()])])
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is None

    def test_optional_falls_back_to_explicit_default_when_no_provider(self) -> None:
        """Optional() injects the explicit default when no provider is registered."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")

        fallback = _StoreImpl()

        def handler(
            store: Annotated[_StoreProto | None, Optional()] = fallback,
        ) -> None:
            pass

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is fallback

    def test_optional_falls_back_to_none_default_when_no_provider(self) -> None:
        """Optional() injects None when default=None and no provider is registered."""
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")

        def handler(
            store: Annotated[_StoreProto | None, Optional()] = None,
        ) -> None:
            pass

        plan = build_injection_plan(handler)
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is None

    def test_bare_union_without_marker_rejected_mentions_optional(self) -> None:
        """Bare T | None (no marker) is rejected; message mentions Optional()."""

        def handler(store: _StoreProto | None) -> None: ...  # noqa: ARG001

        with pytest.raises(TypeError, match="Optional\\(\\)"):
            build_injection_plan(handler)

    def test_optional_combined_with_depends_raises(self) -> None:
        """Optional() + Depends() on one parameter raises at registration."""
        dep = lambda: "x"  # noqa: E731

        with pytest.raises(TypeError, match="multiple binding markers"):
            _build_plan_from([("x", Annotated[str, Optional(), Depends(dep)])])

    def test_optional_combined_with_payload_raises(self) -> None:
        """Optional() + Payload() on one parameter raises at registration."""
        from cosalette.mqtt import Payload as PayloadFactory

        with pytest.raises(TypeError, match="multiple binding markers"):
            _build_plan_from([("x", Annotated[str, Optional(), PayloadFactory()])])

    def test_binding_marker_in_later_metadata_slot_is_detected_payload(self) -> None:
        """A binding marker in metadata slot >=2 (after a doc string) is found."""
        from cosalette.mqtt import Payload as PayloadFactory

        plan = _build_plan_from([("x", Annotated[str, "doc", PayloadFactory()])])
        ctx = _make_device_context()
        providers = build_providers(ctx, "testdevice")
        result = resolve_request_kwargs(plan, providers, payload='"hello"')
        assert result["x"] == '"hello"'  # parse_payload returns raw str as-is

    def test_optional_marker_in_later_metadata_slot_resolves(self) -> None:
        """Optional() in a later metadata slot (after a doc string) still resolves."""
        impl = _StoreImpl()
        ctx = _make_device_context(adapters={_StoreProto: impl})
        providers = build_providers(ctx, "testdevice")

        plan = _build_plan_from([("store", Annotated[_StoreProto, "doc", Optional()])])
        result = resolve_request_kwargs(plan, providers)
        assert result["store"] is impl

    def test_topic_on_non_str_inner_raises(self) -> None:
        """Topic() on a non-str inner type raises at registration time."""
        from cosalette.mqtt import Topic as TopicFactory

        with pytest.raises(TypeError, match="Topic\\(\\) requires a str inner type"):
            _build_plan_from([("x", Annotated[int, TopicFactory()])])

    def test_ambiguous_subclass_match_raises(self) -> None:
        """Multiple providers matching via subclass raises TypeError."""

        class _Base:
            pass

        class _A(_Base):
            pass

        class _B(_Base):
            pass

        providers: dict[type, Any] = {_A: _A(), _B: _B()}
        with pytest.raises(TypeError, match="Ambiguous provider"):
            _try_resolve_single("x", _Base, providers)

    def test_generic_annotation_message_redirects_to_payload(self) -> None:
        """list[str] (no marker) is rejected with a message pointing to Payload()."""

        def handler(items: list[str]) -> None: ...  # noqa: ARG001

        with pytest.raises(TypeError, match="Payload\\(\\)"):
            build_injection_plan(handler)

    def test_t_or_none_message_redirects_to_optional(self) -> None:
        """T | None (no marker, one non-None member) message mentions Optional()."""

        def handler(store: _StoreProto | None) -> None: ...  # noqa: ARG001

        with pytest.raises(TypeError) as exc_info:
            build_injection_plan(handler)
        msg = str(exc_info.value)
        assert "Optional()" in msg
        assert "_StoreProto" in msg

    def test_optional_repr_no_default(self) -> None:
        """Optional() marker repr is 'Optional()' when no default is captured."""
        assert repr(Optional()) == "Optional()"

    def test_optional_repr_with_captured_default(self) -> None:
        """_OptionalMarker repr shows the captured default value."""
        from cosalette.di import _UNSET, _OptionalMarker

        marker = _OptionalMarker(default=42)
        assert repr(marker) == "Optional(default=42)"
        assert repr(_OptionalMarker(default=_UNSET)) == "Optional()"

    def test_optional_none_inner_type_rejected_at_registration(self) -> None:
        """Annotated[None, Optional()] is rejected; message names the parameter."""
        with pytest.raises(TypeError, match="requires a concrete inner type"):
            _build_plan_from([("x", Annotated[None, Optional()])])

    def test_optional_ambiguous_provider_raises_through_resolve(self) -> None:
        """Optional() ambiguity raises TypeError — not silently swallowed into None."""

        class _Base:
            pass

        class _Sub1(_Base):
            pass

        class _Sub2(_Base):
            pass

        sub1 = _Sub1()
        sub2 = _Sub2()

        ctx = _make_device_context(adapters={_Sub1: sub1, _Sub2: sub2})
        providers = build_providers(ctx, "testdevice")

        plan = _build_plan_from([("dep", Annotated[_Base | None, Optional()])])
        with pytest.raises(TypeError, match="Ambiguous provider"):
            resolve_request_kwargs(plan, providers)

    def test_optional_generic_bare_inner_rejected(self) -> None:
        """Annotated[list[str], Optional()] is rejected — generics are not injectable.

        Technique: Error Guessing — boundary between concrete type and generic alias.
        """
        with pytest.raises(TypeError, match="concrete type"):
            _build_plan_from([("x", Annotated[list[str], Optional()])])

    def test_optional_generic_union_inner_rejected(self) -> None:
        """Annotated[list[str] | None, Optional()] is rejected.

        The union branch strips NoneType and leaves list[str] — a generic alias, not
        a concrete type.  Distinct code path from the bare-generic case.

        Technique: Error Guessing — union stripping still leaves a non-type concrete.
        """
        with pytest.raises(TypeError, match="concrete type"):
            _build_plan_from([("x", Annotated[list[str] | None, Optional()])])

    @pytest.mark.parametrize(
        "annotation",
        [
            Annotated[str | int | None, Optional()],
            Annotated[str | int, Optional()],
        ],
        ids=["union_none_multi", "union_no_none_multi"],
    )
    def test_optional_multi_non_none_union_rejected(self, annotation: Any) -> None:
        """Optional() with multiple non-None union members raises TypeError.

        Technique: Boundary Value Analysis — exactly-one vs. more-than-one
        non-None member.
        """
        with pytest.raises(TypeError, match="single concrete type"):
            _build_plan_from([("x", annotation)])

    def test_try_resolve_single_exact_match(self) -> None:
        """_try_resolve_single returns the provider on an exact type match.

        Technique: Equivalence Partitioning — found path.
        """
        providers: dict[type, Any] = {str: "hello"}
        assert _try_resolve_single("x", str, providers) == "hello"

    def test_try_resolve_single_returns_sentinel_when_not_found(self) -> None:
        """_try_resolve_single returns _SENTINEL when no provider matches.

        Technique: Equivalence Partitioning — not-found path.
        """
        assert _try_resolve_single("x", int, {}) is _SENTINEL
