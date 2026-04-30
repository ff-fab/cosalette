"""Health check protocol, adapter status, and periodic health check runner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    """

    def __init__(
        self,
        health_checkables: dict[type, object],
        adapter_device_map: Mapping[type, Sequence[tuple[str, bool]]],
        health_reporter: HealthReporter,
        clock: ClockPort,
        interval: float,
        shutdown_event: asyncio.Event,
        restart_after_failures: int = 0,
        max_restarts: int = 3,
        restart_cooldown: float = 5.0,
        sustained_health_reset: float = 300.0,
        on_restart_needed: Callable[[type, object], Awaitable[bool]] | None = None,
    ) -> None:
        self._checkables = health_checkables
        self._device_map = adapter_device_map
        self._health_reporter = health_reporter
        self._clock = clock
        self._interval = interval
        self._shutdown_event = shutdown_event
        self._restart_after_failures = restart_after_failures
        self._max_restarts = max_restarts
        self._restart_cooldown = restart_cooldown
        self._sustained_health_reset = sustained_health_reset
        self._on_restart_needed = on_restart_needed
        self.adapter_health_status: dict[type, AdapterHealthStatus] = {
            t: AdapterHealthStatus() for t in health_checkables
        }

    async def run_startup_checks(self) -> None:
        """Run one health check per adapter before device tasks start.

        Failed adapters start with availability ``"offline"`` for their
        dependent devices.  Failures are non-blocking.
        """
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
            for adapter_type, adapter in self._checkables.items():
                await self._probe(adapter_type, adapter)

    async def _probe(self, adapter_type: type, adapter: object) -> bool:
        """Execute a single health check with timeout and state tracking."""
        now = self._clock.now()
        timeout = self._interval / 2

        try:
            healthy: bool = await asyncio.wait_for(
                adapter.health_check(),  # ty: ignore[unresolved-attribute]
                timeout=timeout,
            )
        except Exception:
            healthy = False

        old = self.adapter_health_status[adapter_type]

        if healthy:
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
        if self._restart_after_failures <= 0 or old.restart_exhausted:
            return False
        if failures < self._restart_after_failures:
            return False
        if old.restart_count > 0 and (now - old.last_restart) < self._restart_cooldown:
            return False

        name = adapter_type.__qualname__
        if old.restart_count >= self._max_restarts:
            logger.critical(
                "Adapter %s exceeded max restarts (%d), staying offline permanently",
                name,
                self._max_restarts,
            )
            self.adapter_health_status[adapter_type] = AdapterHealthStatus(
                healthy=False,
                consecutive_failures=failures,
                last_check=now,
                restart_count=old.restart_count,
                restart_exhausted=True,
                last_restart=old.last_restart,
                last_healthy_since=0.0,
            )
            return True

        if self._on_restart_needed is None:
            return False

        success = await self._on_restart_needed(adapter_type, adapter)
        if success:
            new_count = old.restart_count + 1
            logger.warning(
                "Restarting adapter %s after %d consecutive failures (restart %d/%d)",
                name,
                failures,
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
            for dev_name, is_root in self._device_map.get(adapter_type, []):
                await self._health_reporter.publish_device_available(
                    dev_name,
                    is_root=is_root,
                )
        else:
            logger.critical(
                "Adapter %s restart failed, marking as permanently offline",
                name,
            )
            self.adapter_health_status[adapter_type] = AdapterHealthStatus(
                healthy=False,
                consecutive_failures=failures,
                last_check=now,
                restart_count=old.restart_count,
                restart_exhausted=True,
                last_restart=old.last_restart,
                last_healthy_since=0.0,
            )
        return True

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
