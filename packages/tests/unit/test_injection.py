"""Tests for cosalette._injection — Signature-based dependency injection.

Test Techniques Used:
    - Specification-based Testing: Verify injection plan building rules
    - Boundary Value Analysis: Zero-parameter, single-parameter, multi-parameter
    - Error Guessing: Missing annotations, unknown types, unsupported param kinds
    - Equivalence Partitioning: Parameter kinds (allowed vs rejected)
    - Integration Testing: Full injection with DeviceContext + resolve_kwargs
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

import pytest

from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._injection import (
    build_injection_plan,
    build_providers,
    resolve_kwargs,
)
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

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
