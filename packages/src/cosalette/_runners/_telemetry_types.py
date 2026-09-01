"""Helper types and module-level functions for the telemetry runner."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cosalette._runners._trigger import TriggerRunSource

from cosalette._cron import CronSchedule
from cosalette._persistence._stores import DeviceStore
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._trigger import TriggerPayload
from cosalette._strategies import PublishStrategy

_TICK_PRECISION = 1000  # milliseconds


def _resolved_interval(reg: _TelemetryRegistration) -> float:
    """Return the interval, which must already be resolved to a concrete number.

    Replaces ``cast(float, reg.interval)`` with a runtime-validated
    narrowing.  The invariant is established by
    :func:`~cosalette._wiring.resolve_intervals` during bootstrap,
    before any runner starts.
    """
    interval = reg.interval
    if callable(interval):
        msg = (
            f"Interval for {reg.name!r} has not been resolved "
            f"(still a callable). Was resolve_intervals() called?"
        )
        raise TypeError(msg)
    return interval


def _to_ms(seconds: float) -> int:
    """Convert seconds to integer milliseconds for tick arithmetic.

    Positive intervals are clamped to a minimum of 1 ms so that
    scheduler ticks always advance in time.
    """
    if seconds <= 0:
        return 0
    ms = round(seconds * _TICK_PRECISION)
    return ms or 1


def _seconds_until_next_fire(schedule: CronSchedule) -> float:
    """Compute seconds from now until the next fire time for a cron schedule.

    Uses local timezone (system default) as the reference, consistent
    with ADR-032 design.

    Returns:
        Positive number of seconds to sleep.
    """
    now = datetime.datetime.now().astimezone()
    next_fire = schedule.next_fire_after(now)
    delta = (next_fire - now).total_seconds()
    return max(0.0, delta)


def _sleep_seconds(reg: _TelemetryRegistration) -> float:
    """Return the number of seconds to sleep before the next poll.

    Dispatches between interval-based and schedule-based telemetry.
    """
    if reg.schedule is not None:
        return _seconds_until_next_fire(reg.schedule)
    return _resolved_interval(reg)


@dataclasses.dataclass(slots=True)
class _GroupState:
    """Per-handler state produced by :meth:`TelemetryRunner._init_group_handlers`.

    Replaces a 10-element tuple so that call-sites use named
    attribute access instead of positional destructuring.
    """

    kwargs_arr: list[dict[str, Any]]
    providers_arr: list[dict[type, Any]]  # Store init-result providers
    device_stores: list[DeviceStore | None]
    strategies: list[PublishStrategy | None]
    last_published: list[dict[str, object] | None]
    last_error_type: list[type[Exception] | None]
    intervals_ms: list[int]
    heap: list[tuple[int, int]]
    sleep_ctx: Any  # DeviceContext — avoids circular import
    epoch: float
    active_stores: list[tuple[DeviceStore | None, str]]
    retry_counts: list[int]


@dataclasses.dataclass(slots=True)
class _RetryResult:
    """Outcome of a retry-loop execution."""

    result: dict[str, object] | None
    error: Exception | None
    retry_count: int
    outcome: str  # "success" | "error" | "exhausted" | "shutdown"


@dataclasses.dataclass(slots=True)
class _TriggerSlot:
    """Mutable trigger state for a triggerable telemetry handler.

    Stores the raw MQTT payload string and parses it lazily in
    :meth:`consume` — this avoids JSON parsing cost for triggers that
    are coalesced (replaced by a later message) before the handler runs.

    A slot can be armed by an inbound MQTT ``/set`` message
    (:meth:`arm`) or by an in-process :class:`~cosalette.EntityNotifier`
    call (:meth:`arm_local`, ADR-064).  Both coalesce into one pending
    run; the most recent arm decides the reported source.
    """

    event: asyncio.Event
    raw: str | None = None  # raw MQTT payload; None when no trigger is pending
    source: TriggerRunSource = "scheduled"  # pending run source

    def arm(self, raw: str) -> None:
        """Store raw payload string and signal. Coalesces: replaces pending."""
        self.raw = raw
        self.source = "mqtt"
        self.event.set()

    def arm_local(self) -> None:
        """Signal an in-process wake. Coalesces; carries no payload."""
        self.raw = None
        self.source = "local"
        self.event.set()

    def consume(self) -> TriggerPayload:
        """Parse raw payload lazily and return TriggerPayload, then clear state."""
        raw, source = self.raw, self.source
        self.event.clear()
        self.raw = None
        self.source = "scheduled"
        if source == "local":
            return TriggerPayload.local()
        if source == "mqtt":
            return TriggerPayload.from_mqtt(raw if raw is not None else "")
        return TriggerPayload.scheduled()
