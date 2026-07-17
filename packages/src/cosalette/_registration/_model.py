"""Registration value objects for the cosalette App.

Internal dataclasses, type aliases, and adapter resolution helpers used by
:class:`cosalette._app.App` for device, telemetry, command, and adapter registration.
"""

from __future__ import annotations

import enum
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from cosalette._context import AppContext
from cosalette._cron import CronSchedule
from cosalette._injection import resolve_request_kwargs
from cosalette._persistence._persist import PersistPolicy
from cosalette._retry import BackoffStrategy, CircuitBreaker
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._settings import Settings
from cosalette._strategies import PublishStrategy

type IntervalSpec = float | Callable[..., float]
"""Interval for telemetry: a concrete float or a settings-derived callable.

The callable form accepts either ``Settings`` or a per-device config
value (when using dict-based multi-device registration).
"""

type TimeoutSpec = float | Callable[..., float]
"""Per-invocation timeout backstop for telemetry handlers.

A concrete float (seconds) or a callable that receives ``Settings`` (or a
per-device config when using dict-based multi-device registration) and returns
a float.  After bootstrap resolution this is always either a concrete float or
``None`` (disabled).
"""


class _Unset(enum.Enum):
    """Private sentinel for optional parameters where absence differs from ``None``.

    Currently used for ``timeout=`` (replaced at bootstrap) and ``store=``
    (triggers default-path resolution in ``App.__init__``).
    """

    UNSET = enum.auto()


_UNSET = _Unset.UNSET
"""Sentinel indicating a parameter was not explicitly set.

Used for the ``timeout=`` field (replaced during bootstrap by either
``interval * _DEFAULT_TIMEOUT_FACTOR`` for interval-based telemetry or
``None`` for cron-scheduled telemetry) and for the ``store=`` parameter in
``App.__init__`` (triggers auto-resolution of the default store path).
"""

type CronSpec = Callable[..., str | CronSchedule]
"""Per-device cron schedule spec: a callable receiving per-device config.

Only valid with ``name=callable`` (dict-based multi-device registration).
The callable receives the per-device config object and must return either
a cron expression string or a :class:`CronSchedule` instance.
"""

type EnabledSpec = bool | Callable[..., bool]
"""Enabled flag for decorator registrations: bool or settings-derived callable.

When a callable is provided, the decision is deferred to the bootstrap phase
after settings resolution, alongside :func:`resolve_intervals`.  The callable
receives the resolved ``Settings`` instance (or per-device config when used
with dict-based multi-device registration).

The imperative ``add_telemetry``/``add_device``/``add_command`` methods
continue to accept only a literal ``bool`` — they already run inside
``on_configure`` where settings are available.
"""

type NameSpec = Callable[[Settings], list[str] | dict[str, Any]]
"""Name spec: a callable producing a list of names or a dict of name→config."""

RegistryType = Literal["device", "telemetry", "command"]
"""The kind of registration being added."""

type _AnyRegistration = (
    _DeviceRegistration
    | _TelemetryRegistration
    | _CommandRegistration
    | _StreamRegistration
    | _ReactorRegistration
)

logger = logging.getLogger("cosalette._registration")

# ---------------------------------------------------------------------------
# Internal value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DeviceRegistration:
    """Internal record of a registered @app.device function."""

    name: str
    func: Callable[..., AsyncIterator[Any]]
    injection_plan: list[tuple[str, type]]
    is_root: bool = False
    enabled_spec: EnabledSpec = True
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None
    per_device_config: Any | None = None
    name_spec: NameSpec | None = None
    # Operation metadata
    tags: tuple[str, ...] = ()
    # Contract metadata (FEP-003)
    summary: str | None = None
    behavior: list[str] | None = None
    effects: list[str] | None = None


@dataclass(frozen=True, slots=True)
class _TelemetryRegistration:
    """Internal record of a registered @app.telemetry function."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    interval: IntervalSpec
    is_root: bool = False
    enabled_spec: EnabledSpec = True
    publish_strategy: PublishStrategy | None = None
    persist_policy: PersistPolicy | None = None
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None
    group: str | None = None
    per_device_config: Any | None = None
    name_spec: NameSpec | None = None
    retry: int = 0
    retry_on: tuple[type[BaseException], ...] = ()
    backoff: BackoffStrategy | None = None
    circuit_breaker: CircuitBreaker | None = None
    timeout: TimeoutSpec | None | _Unset = _UNSET
    schedule: CronSchedule | None = None
    schedule_spec: CronSpec | None = None
    triggerable: bool = False
    # Operation metadata
    tags: tuple[str, ...] = ()
    # Contract metadata (FEP-003)
    summary: str | None = None
    state_model: type | None = None
    payload_model: type | None = None
    behavior: list[str] | None = None
    effects: list[str] | None = None


@dataclass(frozen=True, slots=True)
class _CommandRegistration:
    """Internal record of a registered @app.command handler."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    mqtt_params: frozenset[str]  # subset of {"topic", "payload"} declared by handler
    is_root: bool = False
    enabled_spec: EnabledSpec = True
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None
    per_device_config: Any | None = None
    name_spec: NameSpec | None = None
    # Operation metadata
    tags: tuple[str, ...] = ()
    # Contract metadata (FEP-003)
    summary: str | None = None
    state_model: type | None = None
    payload_model: type | None = None
    behavior: list[str] | None = None
    effects: list[str] | None = None
    # Sub-command dispatch
    sub: str | None = None  # sub-value this handler owns
    sub_key: str = "command"  # JSON field used for routing
    # Transport availability signaling
    unavailable_on: tuple[type[Exception], ...] | None = None


@dataclass(frozen=True, slots=True)
class _StreamRegistration:
    """Internal record of a registered @app.stream handler."""

    name: str
    func: Callable[..., Any]
    injection_plan: list[tuple[str, Any]]
    enabled_spec: EnabledSpec = True
    is_root: bool = False
    maxsize: int = 0
    backpressure: BackpressurePolicy = "drop_newest"
    # Operation metadata
    tags: tuple[str, ...] = ()
    # Contract metadata (FEP-003)
    summary: str | None = None
    behavior: list[str] | None = None
    effects: list[str] | None = None


@dataclass(frozen=True, slots=True)
class _ReactorRegistration:
    """Internal record of a registered @app.react function."""

    state_type: type
    func: Callable[..., Any]
    injection_plan: list[tuple[str, type]]
    drain: Callable[[Any], Any] | None
    events_param: str | None


# ---------------------------------------------------------------------------
# Lifespan type + no-op default
# ---------------------------------------------------------------------------

type LifespanFunc = Callable[[AppContext], AbstractAsyncContextManager[Any]]
"""Type alias for the lifespan parameter."""


@asynccontextmanager
async def _noop_lifespan(_ctx: AppContext) -> AsyncIterator[None]:
    """No-op lifespan used when no user lifespan is provided."""
    yield


# ---------------------------------------------------------------------------
# Adapter resolution helpers
# ---------------------------------------------------------------------------


def _validate_init(init: Callable[..., Any]) -> None:
    """Reject async callables passed as ``init=``.

    The init callback is invoked synchronously before the handler
    loop.  An ``async def`` would silently return an unawaited
    coroutine instead of the desired result.

    Raises:
        TypeError: If *init* is a coroutine function.
    """
    # inspect.iscoroutinefunction is used rather than asyncio.iscoroutinefunction
    # (deprecated 3.12, removed 3.16). The asyncio variant additionally checked
    # the legacy _is_coroutine marker; cosalette does not support that pattern.
    if inspect.iscoroutinefunction(init):
        msg = (
            "init= must be a synchronous callable, not async. "
            "Use a regular function or a class with __call__."
        )
        raise TypeError(msg)
    # Catch callable instances whose __call__ is async (iscoroutinefunction
    # only inspects the object itself, not its __call__ dunder).
    if inspect.iscoroutinefunction(type(init).__call__):
        msg = (
            "init= must be a synchronous callable, not async. "
            "The __call__ method is a coroutine function."
        )
        raise TypeError(msg)


def _call_init(
    init: Callable[..., Any],
    init_plan: list[tuple[str, type]] | None,
    providers: dict[type, Any],
) -> Any:
    """Invoke an init callback with signature-based injection.

    Validates the return type does not shadow framework-provided
    types, then returns the result.

    Raises:
        TypeError: If the init result type shadows a known injectable.
    """
    from cosalette._injection import KNOWN_INJECTABLE_TYPES

    _validate_init(init)  # defense-in-depth

    kwargs = resolve_request_kwargs(init_plan or [], providers)
    result = init(**kwargs)

    result_type = type(result)
    if result_type in KNOWN_INJECTABLE_TYPES:
        msg = (
            f"init= callback returned {result_type.__name__!r}, which "
            f"shadows a framework-provided type. Use a wrapper class "
            f"or a different type."
        )
        raise TypeError(msg)

    return result


# ---------------------------------------------------------------------------
# Public type aliases — stable API for downstream type annotations.
# ---------------------------------------------------------------------------

#: Public alias for :class:`_TelemetryRegistration`.
TelemetryRegistration = _TelemetryRegistration

#: Public alias for :class:`_CommandRegistration`.
CommandRegistration = _CommandRegistration

#: Public alias for :class:`_DeviceRegistration`.
DeviceRegistration = _DeviceRegistration
