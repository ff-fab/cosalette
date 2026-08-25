"""Periodic background task registration and runner.

This private module contains the internal dataclass and async loop
for :meth:`cosalette._app.App.periodic`.

See Also:
    ADR-041 — @app.periodic design decision.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cosalette._clock import ClockPort
from cosalette._injection import resolve_request_kwargs
from cosalette._registration import (
    _UNSET,
    EnabledSpec,
    IntervalSpec,
    TimeoutSpec,
    _Unset,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PeriodicRegistration:
    """Internal record of a registered @app.periodic function."""

    name: str
    func: Callable[..., Awaitable[None]]
    injection_plan: list[tuple[str, type]]
    interval: IntervalSpec
    enabled_spec: EnabledSpec = True
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None
    # Per-invocation watchdog (ADR-060): _UNSET resolves to
    # interval * _DEFAULT_TIMEOUT_FACTOR at bootstrap; None disables it.
    timeout: TimeoutSpec | None | _Unset = _UNSET
    # Operation metadata
    tags: tuple[str, ...] = ()
    # Contract metadata
    summary: str | None = None
    behavior: list[str] | None = None


async def run_periodic(
    reg: _PeriodicRegistration,
    providers: dict[type, Any],
) -> None:
    """Sleep → invoke → repeat.

    Exceptions are logged at ERROR level and the loop continues —
    a single handler failure does not kill the background task.
    ``asyncio.CancelledError`` propagates immediately for clean shutdown.

    Args:
        reg: The periodic registration to run.
        providers: DI provider map (types → instances).
    """
    # Run init if present
    if reg.init is not None:
        init_kwargs = resolve_request_kwargs(reg.init_injection_plan or [], providers)
        init_result = reg.init(**init_kwargs)
        providers = {**providers, type(init_result): init_result}

    # interval is always a concrete float by the time this runs (resolved at bootstrap)
    interval: float = reg.interval  # ty: ignore[invalid-assignment]
    clock: ClockPort | None = providers.get(ClockPort)
    kwargs = resolve_request_kwargs(reg.injection_plan, providers)
    # Watchdog bound is resolved at bootstrap (ADR-060). Direct-constructed
    # registrations (tests) may still carry _UNSET/None — both disable it,
    # mirroring the isinstance guard in _command_runner._invoke_handler.
    raw_timeout = reg.timeout
    timeout: float | None = (
        raw_timeout
        if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool)
        else None
    )
    while True:
        if clock is not None:
            await clock.sleep(interval)
        else:
            await asyncio.sleep(interval)
        try:
            await _invoke_with_timeout(reg, kwargs, timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic '%s' error", reg.name)


async def _invoke_with_timeout(
    reg: _PeriodicRegistration,
    kwargs: dict[str, Any],
    timeout: float | None,
) -> None:
    """Invoke the handler once under the ADR-060 watchdog.

    A ``TimeoutError`` from the watchdog is logged at ERROR level and
    swallowed — the loop continues with the next cycle. All other
    exceptions propagate to :func:`run_periodic`.
    """
    try:
        if timeout is not None:
            async with asyncio.timeout(timeout):
                await reg.func(**kwargs)
        else:
            await reg.func(**kwargs)
    except TimeoutError:
        logger.error(
            "Periodic '%s' watchdog: handler exceeded %.1fs and was "
            "cancelled; continuing with next cycle",
            reg.name,
            timeout,
        )


#: Public alias for :class:`_PeriodicRegistration`.
#: Defined here rather than in ``_registration._model`` because
#: ``_PeriodicRegistration`` is co-located with its runner in this module;
#: re-exported from ``cosalette._registration`` and ``cosalette`` for
#: consistent public API access.
PeriodicRegistration = _PeriodicRegistration
