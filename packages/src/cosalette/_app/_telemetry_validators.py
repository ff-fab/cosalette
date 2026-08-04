"""Validation helpers and static utilities for telemetry registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cosalette._cron import CronSchedule
from cosalette._registration import (
    _UNSET,
    CronSpec,
    IntervalSpec,
    TimeoutSpec,
    _Unset,
    _validate_init,
)
from cosalette._retry import (
    _DEFAULT_BACKOFF,
    _DEFAULT_RETRY_ON,
    BackoffStrategy,
)
from cosalette._utils import _callable_qualname


def validate_group_name(group: str | None) -> None:
    """Raise if *group* is an empty string."""
    if group is not None and group == "":
        msg = "group must be non-empty"
        raise ValueError(msg)


def has_interval(interval: IntervalSpec) -> bool:
    """Return True if *interval* represents a real (non-sentinel) value."""
    return interval != 0.0 or callable(interval)


def validate_group_schedule_compat(
    schedule: CronSchedule | str | None,
    group: str | None,
) -> None:
    """Raise if *schedule* and *group* are combined (incompatible)."""
    if schedule is not None and group is not None:
        msg = (
            "schedule= and group= cannot be combined"
            " (coalescing groups require interval=)"
        )
        raise ValueError(msg)


def validate_retry_on_elements(
    retry_on: tuple[type[BaseException], ...],
) -> None:
    """Raise TypeError if any element of *retry_on* is not an exception type."""
    bad = [
        t
        for t in retry_on
        if not isinstance(t, type) or not issubclass(t, BaseException)
    ]
    if bad:
        msg = f"retry_on elements must be exception types, got {bad[0]!r}"
        raise TypeError(msg)


def resolve_telemetry_name_spec(
    name: str | Callable[..., Any],
    func: Callable[..., Any],
) -> tuple[str, Callable[..., Any] | None]:
    """Return (resolved_name, name_spec) for a telemetry registration."""
    if callable(name):
        return _callable_qualname(func), name
    return str(name), None


def parse_schedule(
    schedule: str | CronSchedule | None,
) -> CronSchedule | None:
    """Parse a schedule string or pass through a CronSchedule."""
    if isinstance(schedule, str):
        return CronSchedule(schedule)
    if isinstance(schedule, CronSchedule):
        return schedule
    return None


def prepare_schedule_spec(
    interval: IntervalSpec | None,
    schedule: str | CronSchedule | CronSpec | None,
    group: str | None,
) -> tuple[CronSpec | None, CronSchedule | None, IntervalSpec]:
    """Normalise schedule arguments for callable-name telemetry.

    Returns ``(schedule_spec, parsed_schedule, effective_interval)``.
    When *schedule* is a callable (per-device spec), schedule_spec is set and
    parsed_schedule is ``None``; mutual-exclusivity with interval/group is
    validated eagerly.
    """
    if not callable(schedule):
        return None, None, interval if interval is not None else 0.0
    if interval is not None:
        msg = "interval= and schedule= are mutually exclusive"
        raise ValueError(msg)
    if group is not None:
        msg = (
            "schedule= and group= cannot be combined"
            " (coalescing groups require interval=)"
        )
        raise ValueError(msg)
    return schedule, None, 0.0  # ty: ignore[invalid-return-type]


def validate_interval_schedule(
    interval: IntervalSpec | None,
    schedule: str | CronSchedule | None,
    group: str | None = None,
) -> None:
    """Validate interval/schedule mutual exclusivity and group compat."""
    if interval is not None and schedule is not None:
        msg = "interval= and schedule= are mutually exclusive"
        raise ValueError(msg)
    if interval is None and schedule is None:
        msg = "Either interval= or schedule= is required"
        raise ValueError(msg)
    validate_group_schedule_compat(schedule, group)


def validate_imperative_schedule(
    interval: IntervalSpec,
    parsed_schedule: CronSchedule | None,
    group: str | None = None,
) -> None:
    """Validate interval/schedule mutual exclusivity (imperative path)."""
    _has_interval = has_interval(interval)
    if _has_interval and parsed_schedule is not None:
        msg = "interval= and schedule= are mutually exclusive"
        raise ValueError(msg)
    if not _has_interval and parsed_schedule is None:
        msg = "Either interval= or schedule= is required"
        raise ValueError(msg)
    validate_group_schedule_compat(parsed_schedule, group)


def resolve_retry_defaults(
    retry: int,
    retry_on: tuple[type[BaseException], ...] | None,
    backoff: BackoffStrategy | None,
) -> tuple[tuple[type[BaseException], ...], BackoffStrategy | None]:
    """Apply default retry_on and backoff when retry > 0."""
    if retry > 0:
        if retry_on is None:
            retry_on = _DEFAULT_RETRY_ON
        if backoff is None:
            backoff = _DEFAULT_BACKOFF
    return retry_on if retry_on is not None else (), backoff


def interval_is_invalid(
    schedule: CronSchedule | None,
    schedule_spec: CronSpec | None,
    name: str | Callable[..., Any],
    interval: IntervalSpec,
) -> bool:
    """Return True when the interval value is clearly invalid."""
    _has_schedule = schedule is not None or schedule_spec is not None
    is_static_name = not callable(name)
    is_static_interval = not callable(interval)
    return not _has_schedule and is_static_name and is_static_interval and interval <= 0


def validate_schedule_spec_combinations(
    schedule_spec: CronSpec | None,
    name: str | Callable[..., Any],
    group: str | None,
    parsed_schedule: CronSchedule | None = None,
) -> None:
    """Raise if schedule_spec is combined with incompatible arguments."""
    if schedule_spec is None:
        return
    if not callable(name):
        msg = (
            "schedule= callable requires name= to be a callable "
            "(per-device dict/list spec).  "
            "Static names have no per-device config to pass to the callable."
        )
        raise ValueError(msg)
    if group is not None:
        msg = "schedule= callable cannot be combined with group="
        raise ValueError(msg)
    if parsed_schedule is not None:
        msg = "schedule_spec= cannot be combined with schedule="
        raise ValueError(msg)


def validate_retry_args(
    retry: int,
    retry_on: tuple[type[BaseException], ...] | None,
) -> None:
    """Raise for invalid retry arguments."""
    if not isinstance(retry, int) or retry < 0:
        msg = f"retry must be a non-negative integer, got {retry!r}"
        raise ValueError(msg)
    if retry > 0 and retry_on is not None and retry_on == ():
        msg = "retry > 0 with retry_on=() is invalid (nothing would be retried)"
        raise ValueError(msg)
    if retry_on is not None:
        validate_retry_on_elements(retry_on)


def validate_timeout(timeout: TimeoutSpec | None | _Unset) -> None:
    """Raise ValueError if *timeout* is a concrete invalid value.

    Deferred forms (``_UNSET``, ``None``, callable) are accepted without
    validation — their values are checked at bootstrap time.

    Args:
        timeout: The timeout value to validate.

    Raises:
        ValueError: If *timeout* is a ``bool``, a non-finite number,
            or a concrete ``int``/``float`` that is ≤ 0.
    """
    import math

    if timeout is _UNSET or timeout is None or callable(timeout):
        return
    if isinstance(timeout, bool):
        msg = f"timeout must be a number, not bool, got {timeout!r}"
        raise ValueError(msg)
    if isinstance(timeout, (int, float)) and (
        not math.isfinite(timeout) or timeout <= 0
    ):
        msg = f"timeout must be a finite positive number, got {timeout!r}"
        raise ValueError(msg)


def validate_triggerable(
    triggerable: bool,
    name: str | None,
    group: str | None,
    is_root: bool = False,
) -> None:
    """Raise ValueError for invalid triggerable combinations."""
    if not triggerable:
        return
    if name is None or is_root:
        msg = "triggerable=True requires a named device (name= must be set)"
        raise ValueError(msg)
    if group is not None:
        msg = (
            "triggerable= and group= cannot be combined"
            " (coalescing groups use a shared scheduler)"
        )
        raise ValueError(msg)


def validate_telemetry_args(
    name: str | Callable[..., Any],
    interval: IntervalSpec,
    persist: object,
    init: Callable[..., Any] | None,
    group: str | None,
    store_configured: bool,
    retry: int = 0,
    retry_on: tuple[type[BaseException], ...] | None = None,
    schedule: CronSchedule | None = None,
    schedule_spec: CronSpec | None = None,
    timeout: TimeoutSpec | None | _Unset = _UNSET,
) -> None:
    """Run all standard validation checks for a telemetry registration."""
    validate_group_name(group)
    if persist is not None and not store_configured:
        msg = (
            "persist= requires a store= backend on the App. "
            "Pass store=MemoryStore() (or another Store) to App()."
        )
        raise ValueError(msg)
    if init is not None:
        _validate_init(init)
    validate_schedule_spec_combinations(schedule_spec, name, group)
    # Skip interval validation when schedule is set (interval is sentinel 0.0)
    if interval_is_invalid(schedule, schedule_spec, name, interval):
        msg = f"Telemetry interval must be positive, got {interval}"
        raise ValueError(msg)
    validate_retry_args(retry, retry_on)
    validate_timeout(timeout)
