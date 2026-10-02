"""Device status, heartbeat payload, LWT builder, and health reporter."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cosalette._clock import ClockPort
from cosalette._json import dumps
from cosalette._mqtt import MqttNotConnectedError, MqttPort, WillConfig

logger = logging.getLogger(__name__)


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

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dictionary."""
        data: dict[str, object] = {"status": self.status}
        if self.consecutive_failures is not None:
            data["last_success_at"] = self.last_success_at
            data["consecutive_failures"] = self.consecutive_failures
        return data


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
    """

    mqtt: MqttPort
    topic_prefix: str
    version: str
    clock: ClockPort
    include_version: bool = True
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

    def __post_init__(self) -> None:
        """Capture the start time for uptime calculation."""
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

    async def record_success(self, device: str) -> None:
        """Record a fresh cycle and clear a ``freshness`` offline mark.

        ``"online"`` is republished only when no other availability source
        still holds the device offline (ADR-077).
        """
        entry = self._freshness.get(device)
        if entry is None:
            return
        entry.last_success = self.clock.now()
        entry.last_success_at = datetime.now(UTC).isoformat(timespec="seconds")
        entry.consecutive_failures = 0
        if self.is_unavailable(device, source="freshness"):
            logger.info("Telemetry '%s' is fresh again", device)
            await self.publish_device_available(
                device, is_root=entry.is_root, source="freshness"
            )

    def record_failure(self, device: str, exc: BaseException) -> None:
        """Count a failed cycle for a tracked telemetry entity."""
        entry = self._freshness.get(device)
        if entry is None:
            return
        entry.consecutive_failures += 1
        entry.last_error_type = type(exc).__name__

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

    def _device_snapshot(self, device: str, status: DeviceStatus) -> DeviceStatus:
        """Return *status* with freshness applied for the heartbeat.

        ``stale`` outranks every other status while the ``freshness`` source
        is active (ADR-080).
        """
        entry = self._freshness.get(device)
        if entry is None:
            return status
        stale = self.is_unavailable(device, source="freshness")
        return DeviceStatus(
            status="stale" if stale else status.status,
            last_success_at=entry.last_success_at,
            consecutive_failures=entry.consecutive_failures,
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

    async def publish_heartbeat(self) -> None:
        """Publish a structured JSON heartbeat to ``{prefix}/status``.

        The payload includes current uptime, version (unless
        ``include_version`` is ``False``, F-DP6), and all tracked device
        statuses.
        """
        uptime = int(self.clock.now() - self._start_time)
        payload = HeartbeatPayload(
            status="online",
            uptime_s=uptime,
            version=self.version,
            devices={
                name: self._device_snapshot(name, status)
                for name, status in self._devices.items()
            },
        )
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
        live state. Devices currently marked unavailable are skipped and keep
        their last retained ``"offline"`` value — without that check a
        reconnect would republish ``"online"`` for a device that is still
        failing, and would do so again on every reconnect (ADR-077).

        See Also:
            ADR-012 — Health and availability reporting.
            ADR-077 — Automatic transport availability.
        """
        for device in list(self._devices):
            sources = self._unavailable.get(device, set())
            if "freshness" in sources:
                # A stale transition can occur while MQTT is unavailable.  Its
                # source still records the true state, so assert the retained
                # offline value on reconnect instead of leaving an older
                # retained online value in place.
                await self._safe_publish(self._availability_topic(device), "offline")
            elif sources:
                continue
            else:
                await self._safe_publish(self._availability_topic(device), "online")

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
