"""Device status, heartbeat payload, LWT builder, and health reporter."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cosalette._clock import ClockPort
from cosalette._json import dumps
from cosalette._mqtt import MqttNotConnectedError, MqttPort, WillConfig

logger = logging.getLogger(__name__)

DEFAULT_ERROR_REMINDER_INTERVAL = 3600.0
"""Default ``App(error_reminder_interval=)``: one reminder per hour of outage."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """Immutable status snapshot for a single device.

    Used inside :class:`HeartbeatPayload` to report per-device health
    in the heartbeat JSON.
    """

    status: str = "ok"
    # Freshness fields (ADR-080), set only for telemetry entities. A ``None``
    # ``consecutive_failures`` means "not tracked" and omits both keys, so
    # device, command and stream entries keep their one-key shape.
    last_success_at: str | None = None
    consecutive_failures: int | None = None
    # Failure-streak fields (ADR-082), ``None`` while the entity is healthy.
    last_error: str | None = None
    failing_since: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary."""
        data: dict[str, object] = {"status": self.status}
        if self.consecutive_failures is not None:
            data["last_success_at"] = self.last_success_at
            data["consecutive_failures"] = self.consecutive_failures
            data["last_error"] = self.last_error
            data["failing_since"] = self.failing_since
        return data


def format_duration(seconds: float) -> str:
    """Format *seconds* as ``45s``, ``12m05s`` or ``3h07m00s`` for log lines."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True, slots=True)
class FailureStreak:
    """A telemetry entity's run of consecutive failed cycles (ADR-082).

    *count* failed cycles over *duration* seconds since the first one, which
    happened at the wall-clock ISO time *first_seen_at*.  *remind* is set when
    this failure is due a reminder.
    """

    count: int
    first_seen_at: str
    duration: float
    remind: bool = False
    new_error_type: bool = False

    def details(self) -> dict[str, object]:
        """Return the error payload ``details`` for this streak."""
        return {"count": self.count, "first_seen": self.first_seen_at}


@dataclass(slots=True)
class _Freshness:
    """Mutable freshness record for one telemetry entity (ADR-080).

    *last_success* is monotonic (clock-port) time and drives the staleness
    check; *last_success_at* is the wall-clock ISO timestamp the heartbeat
    reports, ``None`` until the first fresh cycle.
    """

    stale_after: float | None
    is_root: bool
    last_success: float
    last_success_at: str | None = None
    consecutive_failures: int = 0
    last_error_type: str | None = None
    # Start of the current failure streak (ADR-082): monotonic and wall-clock,
    # plus the monotonic time the next interval reminder is due.
    failing_since: float = 0.0
    failing_since_at: str = ""
    next_reminder: float = 0.0


@dataclass(frozen=True, slots=True)
class HeartbeatPayload:
    """Immutable structured heartbeat payload.

    Represents an app-level status snapshot ready for JSON serialisation
    and MQTT publication.
    """

    status: str
    uptime_s: int
    version: str
    devices: dict[str, DeviceStatus] = field(default_factory=dict)

    def to_json(self, *, include_version: bool = True) -> str:
        """Serialise to a JSON string.

        Device entries are expanded to nested dicts via
        :meth:`DeviceStatus.to_dict`.  Pass ``include_version=False`` to omit
        the ``version`` key entirely (F-DP6: reduces CVE fingerprinting on
        shared brokers).
        """
        data: dict[str, object] = {
            "status": self.status,
            "uptime_s": self.uptime_s,
            "devices": {
                name: device.to_dict() for name, device in self.devices.items()
            },
        }
        if include_version:
            data["version"] = self.version
        return dumps(data)


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------


def build_will_config(topic_prefix: str) -> WillConfig:
    """Create a :class:`WillConfig` for the app's LWT.

    The resulting config targets ``{topic_prefix}/status`` with payload
    ``"offline"``, QoS 1, retained.  Pass this to :class:`MqttClient`
    so the broker publishes ``"offline"`` on unexpected disconnection.

    Args:
        topic_prefix: Application-level topic prefix (e.g. ``"velux2mqtt"``).

    Returns:
        Pre-configured LWT for the app status topic.
    """
    return WillConfig(
        topic=f"{topic_prefix}/status",
        payload="offline",
        qos=1,
        retain=True,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class HealthReporter:
    """Publishes app heartbeats and per-device availability to MQTT.

    Manages device tracking, uptime calculation (via monotonic clock),
    and graceful shutdown.  All publication is fire-and-forget — errors
    are logged but never propagated.

    Args:
        mqtt: MQTT port used for publishing.
        topic_prefix: Base prefix for health topics (e.g. ``"velux2mqtt"``).
        version: Application version string included in heartbeats.
        clock: Monotonic clock for uptime measurement (see :class:`ClockPort`).
        error_reminder_interval: Seconds between reminders for a telemetry
            failure that persists; ``None`` disables reminders (ADR-082).
    """

    mqtt: MqttPort
    topic_prefix: str
    version: str
    clock: ClockPort
    include_version: bool = True
    error_reminder_interval: float | None = DEFAULT_ERROR_REMINDER_INTERVAL
    _start_time: float = field(init=False, repr=False)
    _devices: dict[str, DeviceStatus] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    _root_devices: set[str] = field(
        init=False,
        default_factory=set,
        repr=False,
    )
    # Sources that currently believe each device is transport-unavailable.
    # Keeping sources distinct prevents one recovery path from declaring a
    # device online while a different path still reports it offline.
    _unavailable: dict[str, set[str]] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    _freshness: dict[str, _Freshness] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )
    # Heartbeat-only statuses of supervised streams (ADR-081).  Kept apart
    # from ``_devices``: a stream has no availability topic, so it must never
    # be re-announced or marked offline on shutdown.
    _stream_statuses: dict[str, DeviceStatus] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Capture the start time for uptime calculation."""
        interval = self.error_reminder_interval
        if interval is not None and (not math.isfinite(interval) or interval <= 0):
            raise ValueError("error_reminder_interval must be positive or None")
        self._start_time = self.clock.now()

    # --- Freshness (ADR-080) -------------------------------------------------

    def track_freshness(
        self,
        device: str,
        stale_after: float | None,
        *,
        is_root: bool = False,
    ) -> None:
        """Start tracking freshness for a telemetry entity.

        The staleness clock starts now, so an entity that never completes a
        cycle — a dead task, a failing ``init=`` — still goes stale.  With
        *stale_after* ``None`` the entity is tracked for the heartbeat only.
        """
        self._freshness[device] = _Freshness(
            stale_after=stale_after, is_root=is_root, last_success=self.clock.now()
        )

    async def record_success(self, device: str) -> FailureStreak | None:
        """Record a fresh cycle and clear a ``freshness`` offline mark.

        ``"online"`` is republished only when no other availability source
        still holds the device offline (ADR-077).  Returns the failure streak
        this success ended, or ``None`` when there was none (ADR-082).
        """
        entry = self._freshness.get(device)
        if entry is None:
            return None
        now = self.clock.now()
        streak = (
            FailureStreak(
                entry.consecutive_failures,
                entry.failing_since_at,
                now - entry.failing_since,
            )
            if entry.consecutive_failures
            else None
        )
        entry.last_success = now
        entry.last_success_at = _utc_now_iso()
        entry.consecutive_failures = 0
        entry.last_error_type = None
        entry.failing_since = 0.0
        entry.failing_since_at = ""
        entry.next_reminder = 0.0
        if self.is_unavailable(device, source="freshness"):
            logger.info("Telemetry '%s' is fresh again", device)
            await self.publish_device_available(
                device, is_root=entry.is_root, source="freshness"
            )
        return streak

    def record_failure(self, device: str, exc: BaseException) -> FailureStreak | None:
        """Count a failed cycle and decide whether it is due a reminder.

        Within the first ``error_reminder_interval`` of a streak the 2nd,
        4th, 8th, ... failure is due one; after that, the first failure in
        each further interval since the streak began (ADR-082).  Returns
        ``None`` for an untracked entity.
        """
        entry = self._freshness.get(device)
        if entry is None:
            return None
        now = self.clock.now()
        error_type = type(exc).__name__
        new_error_type = error_type != entry.last_error_type
        entry.consecutive_failures += 1
        entry.last_error_type = error_type
        count = entry.consecutive_failures
        interval = self.error_reminder_interval
        remind = False
        if count == 1:
            entry.failing_since = now
            entry.failing_since_at = _utc_now_iso()
            entry.next_reminder = now + (interval or 0.0)
        elif interval is not None:
            if now >= entry.next_reminder:
                remind = True
                missed = (now - entry.next_reminder) // interval
                entry.next_reminder += (missed + 1) * interval
            else:
                is_power_of_two = count & (count - 1) == 0
                remind = is_power_of_two and now - entry.failing_since < interval
        return FailureStreak(
            count,
            entry.failing_since_at,
            now - entry.failing_since,
            remind,
            new_error_type,
        )

    def min_stale_after(self) -> float | None:
        """Return the smallest enabled ``stale_after``, or ``None`` if none is."""
        bounds = [e.stale_after for e in self._freshness.values() if e.stale_after]
        return min(bounds, default=None)

    async def check_freshness(self) -> None:
        """Mark every entity with no fresh cycle for ``stale_after`` as stale.

        Publishes retained ``"offline"`` through the ``freshness`` source and
        logs one WARNING, both on the transition only.
        """
        now = self.clock.now()
        for device, entry in list(self._freshness.items()):
            if entry.stale_after is None:
                continue
            age = now - entry.last_success
            if age < entry.stale_after or self.is_unavailable(
                device, source="freshness"
            ):
                continue
            logger.warning(
                "Telemetry '%s' is stale: no fresh data for %.0fs "
                "(stale_after=%.0fs, last error: %s)",
                device,
                age,
                entry.stale_after,
                entry.last_error_type or "none",
            )
            await self.publish_device_unavailable(
                device, is_root=entry.is_root, source="freshness"
            )

    def longest_stale(self) -> tuple[str, float] | None:
        """Return the entity stale the longest and for how many seconds.

        Stale time counts from the moment the entity crossed ``stale_after``,
        so it does not include the ``stale_after`` window itself (ADR-083).
        ``None`` when no entity is currently stale.
        """
        now = self.clock.now()
        longest: tuple[str, float] | None = None
        for device, entry in self._freshness.items():
            if entry.stale_after is None or not self.is_unavailable(
                device, source="freshness"
            ):
                continue
            seconds = now - entry.last_success - entry.stale_after
            if longest is None or seconds > longest[1]:
                longest = (device, seconds)
        return longest

    def _device_snapshot(self, device: str, status: DeviceStatus) -> DeviceStatus:
        """Return *status* with freshness applied for the heartbeat.

        ``stale`` outranks every other status while the ``freshness`` source
        is active (ADR-080).
        """
        entry = self._freshness.get(device)
        if entry is None:
            return status
        stale = self.is_unavailable(device, source="freshness")
        failing = entry.consecutive_failures > 0
        return DeviceStatus(
            status="stale" if stale else status.status,
            last_success_at=entry.last_success_at,
            consecutive_failures=entry.consecutive_failures,
            last_error=entry.last_error_type if failing else None,
            failing_since=entry.failing_since_at if failing else None,
        )

    def set_device_status(self, device: str, status: str = "ok") -> None:
        """Update or add a device's status in the internal tracker.

        Args:
            device: Device name (used in topic paths and heartbeat payload).
            status: Free-form status string, defaults to ``"ok"``.
        """
        self._devices[device] = DeviceStatus(status=status)

    def is_unavailable(self, device: str, *, source: str | None = None) -> bool:
        """Report whether *device* is currently believed transport-unavailable.

        Lets callers publish availability only on a transition, so a device
        that keeps failing does not republish an unchanged retained value on
        every cycle (ADR-077).
        """
        sources = self._unavailable.get(device, set())
        return source in sources if source is not None else bool(sources)

    def remove_device(self, device: str) -> None:
        """Remove a device from internal tracking, if present."""
        self._devices.pop(device, None)
        self._unavailable.pop(device, None)
        self._freshness.pop(device, None)

    async def publish_device_available(
        self,
        device: str,
        *,
        is_root: bool = False,
        source: str | None = "manual",
    ) -> None:
        """Publish ``"online"`` to the device availability topic.

        For root devices (unnamed), publishes to ``{prefix}/availability``
        instead of ``{prefix}/{device}/availability``.

        Also registers the device as ``"ok"`` in internal tracking. When
        *source* is provided, it clears only that source's unavailable mark;
        another active source keeps the device offline.
        """
        if is_root:
            topic = f"{self.topic_prefix}/availability"
            self._root_devices.add(device)
        else:
            topic = f"{self.topic_prefix}/{device}/availability"
        if source is not None:
            sources = self._unavailable.get(device)
            if sources is not None:
                sources.discard(source)
                if not sources:
                    self._unavailable.pop(device, None)
        if self.is_unavailable(device):
            return
        await self._safe_publish(topic, "online")
        self.set_device_status(device)

    async def publish_device_unavailable(
        self,
        device: str,
        *,
        is_root: bool = False,
        source: str = "manual",
    ) -> None:
        """Publish ``"offline"`` to the device availability topic.

        For root devices (unnamed), publishes to ``{prefix}/availability``
        instead of ``{prefix}/{device}/availability``.

        Marks the device unavailable so :meth:`reannounce` will not resurrect
        it, and records it in the heartbeat roster as ``"unavailable"`` rather
        than dropping it: ``{prefix}/status`` is where an operator reads *why*
        a device failed, which is exactly when that entry must not vanish
        (ADR-077).  A caller that knows the specific reason overwrites it by
        calling :meth:`set_device_status` afterwards, as the telemetry runner
        does with ``"error"``.
        """
        if is_root:
            topic = f"{self.topic_prefix}/availability"
            self._root_devices.add(device)
        else:
            topic = f"{self.topic_prefix}/{device}/availability"
        was_unavailable = self.is_unavailable(device)
        self._unavailable.setdefault(device, set()).add(source)
        if not was_unavailable:
            await self._safe_publish(topic, "offline")
        self.set_device_status(device, "unavailable")

    async def clear_task_failure(self, device: str, *, is_root: bool = False) -> None:
        """Clear the task supervisor's mark after a re-created task recovers.

        Called at a supervised task's first successful cycle or first device
        ``yield`` (ADR-081); streams use :meth:`clear_stream_failure`.  Removes the
        ``"supervisor"`` availability source and the ``error`` status; a
        no-op when the supervisor never marked *device*.  Another source
        still holding the entity offline keeps it offline (ADR-077).
        """
        source = "supervisor"
        if not self.is_unavailable(device, source=source):
            return
        logger.info("Entity '%s' recovered after its task was restarted", device)
        await self.publish_device_available(device, is_root=is_root, source=source)
        if self.is_unavailable(device):
            self.set_device_status(device, "unavailable")

    def mark_stream_failed(self, stream: str) -> None:
        """Record a supervised stream's task failure in the heartbeat.

        A stream has no availability topic (it is not a device, and is
        excluded from discovery and AsyncAPI), so its failure shows only as
        ``"error"`` under its name in ``{prefix}/status`` (ADR-081).
        """
        self._stream_statuses[stream] = DeviceStatus(status="error")

    def clear_stream_failure(self, stream: str) -> None:
        """Set a failed stream back to ``"ok"`` once it yields an item again.

        A no-op unless :meth:`mark_stream_failed` marked *stream*.
        """
        entry = self._stream_statuses.get(stream)
        if entry is None or entry.status != "error":
            return
        logger.info("Stream '%s' recovered after its task was restarted", stream)
        self._stream_statuses[stream] = DeviceStatus(status="ok")

    def heartbeat_payload(self) -> HeartbeatPayload:
        """Build the current heartbeat snapshot without publishing it.

        Shared by :meth:`publish_heartbeat` and the health file (ADR-083),
        so both report the same view.
        """
        return HeartbeatPayload(
            status="online",
            uptime_s=int(self.clock.now() - self._start_time),
            version=self.version,
            devices={
                **self._stream_statuses,
                **{
                    name: self._device_snapshot(name, status)
                    for name, status in self._devices.items()
                },
            },
        )

    async def publish_heartbeat(self) -> None:
        """Publish a structured JSON heartbeat to ``{prefix}/status``.

        The payload includes current uptime, version (unless
        ``include_version`` is ``False``, F-DP6), and all tracked device
        statuses.
        """
        payload = self.heartbeat_payload()
        topic = f"{self.topic_prefix}/status"
        logger.debug("Publishing heartbeat to %s", topic)
        await self._safe_publish(
            topic, payload.to_json(include_version=self.include_version)
        )

    def _availability_topic(self, device: str) -> str:
        """Return the retained-availability MQTT topic for *device*.

        Root devices (registered with ``is_root=True``) publish to the flat
        ``{prefix}/availability``; all others use ``{prefix}/{device}/availability``.
        """
        if device in self._root_devices:
            return f"{self.topic_prefix}/availability"
        return f"{self.topic_prefix}/{device}/availability"

    async def reannounce(self) -> None:
        """Re-publish current availability for all currently-tracked devices.

        Called after an MQTT reconnect so retained availability reflects the
        live state: ``"online"`` for available devices and ``"offline"`` for
        devices marked unavailable by any source — the same rule as
        :meth:`announce_device` on first connect.  Re-asserting ``"offline"``
        repairs a transition that happened while the broker was unreachable,
        whose publish was dropped and would otherwise leave an older retained
        ``"online"`` in place; it also never resurrects a failing device
        (ADR-077).

        See Also:
            ADR-012 — Health and availability reporting (2026-10-02 amendment).
            ADR-077 — Automatic transport availability.
        """
        for device in list(self._devices):
            await self._publish_live_availability(device)

    async def announce_device(self, device: str, *, is_root: bool = False) -> None:
        """Track *device* and publish its live availability on first connect.

        Unlike :meth:`publish_device_available`, this never clears an
        unavailable mark: a device that went unavailable before the first
        MQTT connect, whose ``"offline"`` was dropped, is announced
        ``"offline"`` rather than optimistically ``"online"`` (ADR-012
        amendment).
        """
        if is_root:
            self._root_devices.add(device)
        if not self.is_unavailable(device):
            self.set_device_status(device)
        await self._publish_live_availability(device)

    async def _publish_live_availability(self, device: str) -> None:
        """Publish ``"offline"`` if any source marks *device* unavailable, else
        ``"online"``."""
        payload = "offline" if self.is_unavailable(device) else "online"
        await self._safe_publish(self._availability_topic(device), payload)

    async def shutdown(self) -> None:
        """Gracefully shut down: publish ``"offline"`` for everything.

        Publishes ``"offline"`` to each tracked device's availability
        topic (using root topic for root devices), then publishes
        ``"offline"`` to the app status topic, and clears internal
        device tracking.
        """
        logger.info("Health reporter shutting down — publishing offline")
        for device in list(self._devices):
            topic = self._availability_topic(device)
            await self._safe_publish(topic, "offline")

        status_topic = f"{self.topic_prefix}/status"
        await self._safe_publish(status_topic, "offline")
        self._devices.clear()
        self._stream_statuses.clear()
        self._root_devices.clear()
        self._unavailable.clear()
        self._freshness.clear()

    async def _safe_publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = True,
    ) -> None:
        """Publish to MQTT, swallowing any exceptions.

        A missing MQTT connection is an expected transport condition and is
        logged at DEBUG. Other publication failures are logged at ERROR level;
        neither is propagated — fire-and-forget semantics per ADR-012.
        """
        try:
            await self.mqtt.publish(topic, payload, retain=retain, qos=1)
        except MqttNotConnectedError:
            logger.debug("MQTT not connected, dropped health publish to %s", topic)
        except Exception:
            logger.exception("Failed to publish health to %s", topic)


def _utc_now_iso() -> str:
    """Return the current wall-clock time as a second-precision ISO string."""
    return datetime.now(UTC).isoformat(timespec="seconds")
