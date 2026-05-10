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
from cosalette._registration import EnabledSpec, IntervalSpec

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
    while True:
        if clock is not None:
            await clock.sleep(interval)
        else:
            await asyncio.sleep(interval)
        try:
            await reg.func(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic '%s' error", reg.name)
