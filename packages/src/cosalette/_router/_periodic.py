"""Periodic mixin for the Router class."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._helpers import _validate_periodic_early
from cosalette._injection import build_injection_plan
from cosalette._registration import EnabledSpec, IntervalSpec, _validate_init
from cosalette._runners._periodic import _PeriodicRegistration


class _RouterPeriodicMixin:
    """Mixin for periodic-task-related Router methods."""

    _periodic: list[_PeriodicRegistration]

    @property
    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _build_periodic_decorator_body(
        self,
        func: Callable[..., Any],
        name: str | None,
        interval: IntervalSpec | float,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        summary: str | None,
        behavior: list[str] | None,
        tags: list[str] | None,
    ) -> Callable[..., Any]:
        """Build periodic registration and return func unchanged."""
        from cosalette._utils import _callable_name

        effective_name = name if name is not None else _callable_name(func)
        _validate_periodic_early(effective_name, self.registered_names, interval)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        merged_tags = self._merge_tags(tags)
        reg = _PeriodicRegistration(
            name=effective_name,
            func=func,
            injection_plan=plan,
            interval=interval,
            enabled_spec=enabled,
            init=init,
            init_injection_plan=init_plan,
            tags=tuple(merged_tags),
            summary=summary,
            behavior=behavior,
        )
        self._periodic.append(reg)
        return func

    def periodic(
        self,
        name: str | None = None,
        *,
        interval: IntervalSpec | Any,
        enabled: EnabledSpec = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a background periodic task.

        Extends ``App.periodic`` with router-specific parameters (``tags``).

        Args:
            name: Task name for logging.
            interval: Polling interval in seconds, a timedelta, or a
                callable ``(Settings) -> float``.
            enabled: When ``False``, registration is skipped.
            init: Optional synchronous factory called once before the handler.
            summary: One-line description surfaced in the registry snapshot.
            behavior: Phrases describing what the task does, surfaced in
                the registry snapshot.
            tags: Additional tags for this periodic task.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a task with this name is already registered.
        """
        import datetime

        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()

        if callable(enabled):
            return lambda func: self._build_periodic_decorator_body(
                func, name, interval, enabled, init, summary, behavior, tags
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func
            return self._build_periodic_decorator_body(
                func, name, interval, enabled, init, summary, behavior, tags
            )

        return decorator
