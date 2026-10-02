"""Health check protocol, adapter status, and periodic health check runner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cosalette._clock import ClockPort

if TYPE_CHECKING:
    from cosalette._health._reporter import HealthReporter

logger = logging.getLogger(__name__)


@runtime_checkable
class HealthCheckable(Protocol):
    """Adapter health check protocol (ADR-028).

    Adapters that implement this single-method protocol are periodically
    probed by the framework.  Return ``True`` when healthy, ``False``
    otherwise.  The framework sets per-device availability accordingly.
    """

    async def health_check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AdapterHealthStatus:
    """Per-adapter health state snapshot for the health check runner.

    Tracks whether an adapter is healthy, how many consecutive health
    check failures have occurred, and the monotonic timestamp of the
    last health check.  Exposed for Epic 6 (auto-restart decisions).
    """

    healthy: bool = True
    consecutive_failures: int = 0
    last_check: float = 0.0
    restart_count: int = 0
    restart_exhausted: bool = False
    last_restart: float = 0.0
    last_healthy_since: float = 0.0


class HealthCheckRunner:
    """Periodic health check loop for HealthCheckable adapters.

    Calls ``health_check()`` on each adapter at a fixed interval,
    toggling per-device availability via :class:`HealthReporter`.
    Tracks per-adapter health state in :attr:`adapter_health_status`.

    Only adapter types in *restartable* are restarted (``None`` means
    all); any other adapter that reaches the threshold logs one WARNING
    per unhealthy episode and stays offline (ADR-029 Decision 5,
    ADR-084).  :meth:`request_restart` lets the freshness watchdog
    request a restart; it is serialised with the probes.
    """

    def __init__(
        self,
        health_checkables: dict[type, object],
        adapter_device_map: Mapping[type, Sequence[tuple[str, bool]]],
        health_reporter: HealthReporter,
        clock: ClockPort,
        interval: float,
        shutdown_event: asyncio.Event,
        restart_after_failures: int = 5,
        max_restarts: int = 3,
        sustained_health_reset: float = 300.0,
        on_restart_needed: Callable[[type, object], Awaitable[bool]] | None = None,
        on_recovered: Callable[[type], Awaitable[bool]] | None = None,
        restartable: Collection[type] | None = None,
    ) -> None:
        self._checkables = health_checkables
        self._device_map = adapter_device_map
        self._health_reporter = health_reporter
        self._clock = clock
        self._interval = interval
        self._shutdown_event = shutdown_event
        self._restart_after_failures = restart_after_failures
        self._max_restarts = max_restarts
        self._sustained_health_reset = sustained_health_reset
        self._on_restart_needed = on_restart_needed
        self._on_recovered = on_recovered
        self._restartable = (
            frozenset(health_checkables) if restartable is None else restartable
        )
        self._not_restartable_warned: set[type] = set()
        # Probes and restart requests never interleave (ADR-084).
        self._lock = asyncio.Lock()
        self.adapter_health_status: dict[type, AdapterHealthStatus] = {
            t: AdapterHealthStatus() for t in health_checkables
        }

    async def run_startup_checks(self) -> None:
        """Run one health check per adapter before device tasks start.

        Failed adapters start with availability ``"offline"`` for their
        dependent devices.  Failures are non-blocking.
        """
        async with self._lock:
            for adapter_type, adapter in self._checkables.items():
                await self._probe(adapter_type, adapter)

    async def run_loop(self) -> None:
        """Periodic health check loop — run as an asyncio task.

        Sleeps for *interval* seconds (shutdown-aware), then checks
        all adapters.  Runs until cancelled.
        """
        while True:
            await self._shutdown_aware_sleep(self._interval)
            if self._shutdown_event.is_set():
                return
            async with self._lock:
                for adapter_type, adapter in self._checkables.items():
                    await self._probe(adapter_type, adapter)

    async def request_restart(self, adapter_type: type, reason: str) -> bool:
        """Restart *adapter_type* now, outside the failure threshold (ADR-084).

        Used for ``restart_on_stale``.  The attempt counts against
        ``max_restarts``, does nothing for a restart-exhausted or
        non-restartable adapter, and waits for a running probe round.
        Returns whether a restart was attempted.
        """
        async with self._lock:
            adapter = self._checkables.get(adapter_type)
            if adapter is None or adapter_type not in self._restartable:
                logger.debug(
                    "Adapter %s not restartable; ignoring restart for %s",
                    adapter_type.__qualname__,
                    reason,
                )
                return False
            old = self.adapter_health_status[adapter_type]
            if old.restart_exhausted:
                await self._mark_dependents_unavailable(adapter_type)
                logger.debug(
                    "Adapter %s restart-exhausted; ignoring restart for %s",
                    adapter_type.__qualname__,
                    reason,
                )
                return False
            if self._on_restart_needed is None:
                return False
            await self._mark_dependents_unavailable(adapter_type)
            return await self._attempt_restart(
                adapter_type,
                adapter,
                old,
                old.consecutive_failures,
                self._clock.now(),
                reason=reason,
            )

    async def _mark_dependents_unavailable(self, adapter_type: type) -> None:
        """Keep every dependent offline throughout an explicit restart attempt."""
        source = f"health:{adapter_type.__module__}.{adapter_type.__qualname__}"
        for name, is_root in self._device_map.get(adapter_type, []):
            await self._health_reporter.publish_device_unavailable(
                name, is_root=is_root, source=source
            )

    async def _probe(self, adapter_type: type, adapter: object) -> bool:
        """Execute a single health check with timeout and state tracking."""
        now = self._clock.now()
        timeout = self._interval / 2

        try:
            healthy = bool(
                await asyncio.wait_for(
                    adapter.health_check(),  # ty: ignore[unresolved-attribute]
                    timeout=timeout,
                )
            )
        except Exception:
            healthy = False

        old = self.adapter_health_status[adapter_type]
        if healthy and not old.healthy:
            healthy = await self._may_recover(adapter_type, old)

        if healthy:
            self._not_restartable_warned.discard(adapter_type)
            await self._handle_healthy_probe(adapter_type, old, now)
        else:
            failures = old.consecutive_failures + 1
            if old.healthy:
                logger.warning(
                    "Adapter %s health check failed",
                    adapter_type.__qualname__,
                )
                for name, is_root in self._device_map.get(adapter_type, []):
                    await self._health_reporter.publish_device_unavailable(
                        name,
                        is_root=is_root,
                        source=f"health:{adapter_type.__module__}.{adapter_type.__qualname__}",
                    )
            else:
                logger.debug(
                    "Adapter %s health check failed (consecutive: %d)",
                    adapter_type.__qualname__,
                    failures,
                )
            # Restart threshold detection
            restarted = await self._maybe_restart(
                adapter_type, adapter, old, failures, now
            )
            if restarted:
                return True
            self.adapter_health_status[adapter_type] = AdapterHealthStatus(
                healthy=False,
                consecutive_failures=failures,
                last_check=now,
                restart_count=old.restart_count,
                restart_exhausted=old.restart_exhausted,
                last_restart=old.last_restart,
                last_healthy_since=0.0,
            )

        return healthy

    async def _may_recover(self, adapter_type: type, old: AdapterHealthStatus) -> bool:
        """Return whether a passing probe may bring the adapter back online.

        An adapter whose restart budget is spent stays offline (ADR-029
        Decision 3).  *on_recovered* re-creates the device tasks a failed
        restart attempt left behind and returns ``False`` while the adapter
        is outside its context, so the probe counts as failed and the
        restart threshold keeps running.
        """
        name = adapter_type.__qualname__
        if old.restart_exhausted:
            logger.debug("Adapter %s healthy but restart-exhausted; offline", name)
            return False
        if self._on_recovered is None:
            return True
        try:
            recovered = await self._on_recovered(adapter_type)
        except Exception:
            # Same reason as the restart callback: nothing may escape this
            # framework loop (ADR-081).
            logger.exception("Adapter %s recovery raised", name)
            return False
        if not recovered:
            logger.debug("Adapter %s healthy but not entered; awaiting restart", name)
        return recovered

    async def _handle_healthy_probe(
        self,
        adapter_type: type,
        old: AdapterHealthStatus,
        now: float,
    ) -> None:
        healthy_since = old.last_healthy_since
        restart_count = old.restart_count
        if not old.healthy:
            logger.info(
                "Adapter %s health check recovered after %d failures",
                adapter_type.__qualname__,
                old.consecutive_failures,
            )
            for name, is_root in self._device_map.get(adapter_type, []):
                await self._health_reporter.publish_device_available(
                    name,
                    is_root=is_root,
                    source=f"health:{adapter_type.__module__}.{adapter_type.__qualname__}",
                )
            healthy_since = now
        elif restart_count > 0 and healthy_since >= 0:
            if now - healthy_since >= self._sustained_health_reset:
                restart_count = 0
                logger.info(
                    "Adapter %s restart counter reset after sustained health",
                    adapter_type.__qualname__,
                )
        self.adapter_health_status[adapter_type] = AdapterHealthStatus(
            healthy=True,
            consecutive_failures=0,
            last_check=now,
            restart_count=restart_count,
            restart_exhausted=old.restart_exhausted,
            last_restart=old.last_restart,
            last_healthy_since=healthy_since,
        )

    async def _maybe_restart(
        self,
        adapter_type: type,
        adapter: object,
        old: AdapterHealthStatus,
        failures: int,
        now: float,
    ) -> bool:
        """Attempt restart if threshold reached. Returns True if restarted."""
        if not self._restart_is_due(old, failures):
            return False
        if adapter_type not in self._restartable:
            self._warn_not_restartable(adapter_type, failures)
            return False
        return await self._attempt_restart(adapter_type, adapter, old, failures, now)

    def _warn_not_restartable(self, adapter_type: type, failures: int) -> None:
        """Log once per unhealthy episode that the adapter cannot restart."""
        if adapter_type in self._not_restartable_warned:
            return
        self._not_restartable_warned.add(adapter_type)
        logger.warning(
            "Adapter %s failed %d consecutive health checks but is not "
            "restartable; its devices stay offline until it recovers",
            adapter_type.__qualname__,
            failures,
        )

    async def _attempt_restart(
        self,
        adapter_type: type,
        adapter: object,
        old: AdapterHealthStatus,
        failures: int,
        now: float,
        *,
        reason: str | None = None,
    ) -> bool:
        """Run one restart attempt against the budget.  Returns True."""
        name = adapter_type.__qualname__
        if old.restart_count >= self._max_restarts:
            logger.critical(
                "Adapter %s exceeded max restarts (%d), staying offline permanently",
                name,
                self._max_restarts,
            )
            self._mark_restart_exhausted(adapter_type, old, failures, now)
            return True

        if self._on_restart_needed is None:
            return False

        try:
            success = await self._on_restart_needed(adapter_type, adapter)
        except Exception:
            # The callback runs inside this loop, which the task supervisor
            # treats as a framework loop (ADR-081): an escaping exception
            # would end the process.  It counts as a failed restart instead.
            logger.exception("Adapter %s restart raised", name)
            success = False
        if success:
            await self._record_successful_restart(
                adapter_type, old, reason or f"{failures} consecutive failures", now
            )
        else:
            self._record_failed_restart(adapter_type, old, now)
        return True

    def _restart_is_due(self, old: AdapterHealthStatus, failures: int) -> bool:
        """Return whether the failure state permits a restart attempt.

        Every attempt re-arms the threshold (``consecutive_failures``
        restarts from 0), so attempts are spaced by
        ``restart_after_failures`` health check intervals (ADR-029).
        """
        return (
            self._restart_after_failures > 0
            and not old.restart_exhausted
            and failures >= self._restart_after_failures
        )

    def _mark_restart_exhausted(
        self,
        adapter_type: type,
        old: AdapterHealthStatus,
        failures: int,
        now: float,
    ) -> None:
        """Persist the terminal offline state for an adapter."""
        self.adapter_health_status[adapter_type] = AdapterHealthStatus(
            healthy=False,
            consecutive_failures=failures,
            last_check=now,
            restart_count=old.restart_count,
            restart_exhausted=True,
            last_restart=old.last_restart,
            last_healthy_since=0.0,
        )

    def _record_failed_restart(
        self,
        adapter_type: type,
        old: AdapterHealthStatus,
        now: float,
    ) -> None:
        """Count a failed attempt against the restart budget (ADR-029).

        The adapter stays offline.  While budget remains, the next attempt
        waits for ``restart_after_failures`` further failed checks; the
        last failed attempt exhausts the budget.
        """
        new_count = old.restart_count + 1
        exhausted = new_count >= self._max_restarts
        if exhausted:
            logger.critical(
                "Adapter %s restart failed (restart %d/%d), "
                "staying offline permanently",
                adapter_type.__qualname__,
                new_count,
                self._max_restarts,
            )
        else:
            logger.error(
                "Adapter %s restart failed (restart %d/%d), retrying after "
                "%d more failed health checks",
                adapter_type.__qualname__,
                new_count,
                self._max_restarts,
                self._restart_after_failures,
            )
        self.adapter_health_status[adapter_type] = AdapterHealthStatus(
            healthy=False,
            consecutive_failures=0,
            last_check=now,
            restart_count=new_count,
            restart_exhausted=exhausted,
            last_restart=now,
            last_healthy_since=0.0,
        )

    async def _record_successful_restart(
        self,
        adapter_type: type,
        old: AdapterHealthStatus,
        reason: str,
        now: float,
    ) -> None:
        """Record a successful restart and restore dependent devices."""
        new_count = old.restart_count + 1
        logger.warning(
            "Restarting adapter %s after %s (restart %d/%d)",
            adapter_type.__qualname__,
            reason,
            new_count,
            self._max_restarts,
        )
        self.adapter_health_status[adapter_type] = AdapterHealthStatus(
            healthy=True,
            consecutive_failures=0,
            last_check=now,
            restart_count=new_count,
            restart_exhausted=False,
            last_restart=now,
            last_healthy_since=now,
        )
        for device_name, is_root in self._device_map.get(adapter_type, []):
            await self._health_reporter.publish_device_available(
                device_name,
                is_root=is_root,
                source=f"health:{adapter_type.__module__}.{adapter_type.__qualname__}",
            )

    async def _shutdown_aware_sleep(self, seconds: float) -> None:
        """Sleep that returns early if shutdown is requested."""
        if self._shutdown_event.is_set():
            return
        sleep_task = asyncio.ensure_future(self._clock.sleep(seconds))
        shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())
        _done, pending = await asyncio.wait(
            {sleep_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
