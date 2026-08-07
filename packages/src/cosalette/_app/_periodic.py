"""Periodic mixin for the App class."""

from __future__ import annotations

import datetime
import logging
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from cosalette._app._helpers import _validate_periodic_early
from cosalette._injection import build_injection_plan
from cosalette._registration import EnabledSpec, IntervalSpec, _validate_init
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._utils import _callable_name

logger = logging.getLogger(__name__)


class _PeriodicMixin:
    """Mixin for periodic-related App methods."""

    _periodic: list[_PeriodicRegistration]

    @property
    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    def periodic(
        self,
        name: str | None = None,
        *,
        interval: IntervalSpec | datetime.timedelta,
        enabled: EnabledSpec = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a background periodic task.

        The decorated coroutine is called at the specified *interval*.
        It runs purely for side-effects — no return value is published.
        Exceptions are logged at ERROR level and the loop continues.

        Parameters are injected by type annotation (Settings, adapter
        ports, Logger, ClockPort, ``@app.state`` instances).

        Args:
            name: Task name for logging.  When ``None``, the function
                name is used.  Must be unique across all registrations.
            interval: Polling interval in seconds, a
                :class:`datetime.timedelta`, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Must be positive.
            enabled: When ``False``, registration is silently skipped.
                Accepts a callable ``(Settings) -> bool`` for deferred
                resolution (same as ``@app.telemetry``).
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into the
                handler by type.
            summary: One-line description.  Surfaced in the registry
                snapshot (:func:`~cosalette.build_registry_snapshot`,
                :func:`~cosalette.format_registry_table`, and the
                ``cosalette_inspect_app`` MCP tool).
            behavior: Phrases describing what the task does.  Surfaced in
                the registry snapshot.

        Note:
            Periodic tasks have no MQTT presence by design (ADR-041), so
            they carry no ``state_model``/``payload_model``/``effects`` and
            never appear in the generated AsyncAPI document.

        Raises:
            ValueError: If a registration with this name already exists.
            ValueError: If *interval* is a literal float/timedelta and
                not positive.
            TypeError: If any handler parameter lacks a type annotation.

        Example::

            @app.periodic("cache-refresh", interval=60)
            async def refresh_cache(cache: CachePort) -> None:
                await cache.refresh()
        """
        # Normalise timedelta to float immediately
        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name = name if name is not None else _callable_name(func)
                _validate_periodic_early(
                    effective_name, self.registered_names, interval
                )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                plan = build_injection_plan(func)
                self._periodic.append(
                    _PeriodicRegistration(
                        name=effective_name,
                        func=func,
                        injection_plan=plan,
                        interval=interval,
                        enabled_spec=enabled,
                        init=init,
                        init_injection_plan=init_plan,
                        summary=summary,
                        behavior=behavior,
                    )
                )
                return func

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            effective_name = name if name is not None else _callable_name(func)
            self.add_periodic(
                effective_name,
                func,
                interval=interval,
                enabled=enabled,
                init=init,
                summary=summary,
                behavior=behavior,
            )
            return func

        return decorator

    def add_periodic(
        self,
        name: str,
        func: Callable[..., Awaitable[None]],
        *,
        interval: IntervalSpec | datetime.timedelta,
        enabled: bool = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
    ) -> None:
        """Register a background periodic task imperatively.

        Imperative counterpart to :meth:`periodic`.

        Args:
            name: Task name for logging.
            func: Async callable — the periodic handler.
            interval: Polling interval in seconds, a
                :class:`datetime.timedelta`, or a callable
                ``(Settings) -> float`` for deferred resolution.
            enabled: When ``False``, registration is silently skipped.
            init: Optional synchronous init factory.
            summary: One-line description surfaced in the registry snapshot.
            behavior: Phrases describing what the task does, surfaced in
                the registry snapshot.

        Raises:
            ValueError: If a registration with this name already exists.
            ValueError: If *interval* is a literal float/timedelta and
                not positive.
        """
        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()
        if not enabled:
            return
        _validate_periodic_early(name, self.registered_names, interval)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        self._periodic.append(
            _PeriodicRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                interval=interval,
                init=init,
                init_injection_plan=init_plan,
                summary=summary,
                behavior=behavior,
            )
        )
