"""Health reporting and availability for IoT-to-MQTT bridge applications.

Publishes app-level heartbeats and per-device availability over MQTT,
with LWT (Last Will and Testament) integration for crash detection.

Topic layout::

    {prefix}/status                  ← app heartbeat (retained JSON)
    {prefix}/{device}/availability   ← device online/offline (retained)

Heartbeat payload schema::

    {
        "status": "online",
        "uptime_s": 3600,
        "version": "0.1.0",
        "devices": {
            "blind": {"status": "ok"},
            "window": {"status": "ok"}
        }
    }

LWT integration:

- The broker publishes ``"offline"`` to ``{prefix}/status`` if the
  client disconnects unexpectedly (crash, network loss).
- :func:`build_will_config` creates a :class:`WillConfig` pre-configured
  for this topic — consumers pass it when constructing their MqttClient.
- During graceful shutdown, the app publishes ``"offline"`` explicitly
  for all tracked devices and the app status topic.

Publication behaviour:

- **Retained** — heartbeats and availability are last-known state.
- **QoS 1** — at-least-once delivery for reliability.
- **Fire-and-forget** — publication failures are logged, never propagated.

See Also:
    ADR-012 — Health and availability reporting.
    ADR-006 — Protocol-based ports (MqttPort, ClockPort).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

from cosalette._clock import ClockPort
from cosalette._mqtt import MqttPort, WillConfig

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

    def to_dict(self) -> dict[str, str]:
        """Serialise to a plain dictionary."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeartbeatPayload:
    """Immutable structured heartbeat payload.

    Represents an app-level status snapshot ready for JSON serialisation
    and MQTT publication.
    """

    status: str
    uptime_s: float
    version: str
    devices: dict[str, DeviceStatus] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to a JSON string.

        Device entries are expanded to nested dicts via
        :meth:`DeviceStatus.to_dict`.
        """
        data: dict[str, object] = {
            "status": self.status,
            "uptime_s": self.uptime_s,
            "version": self.version,
            "devices": {
                name: device.to_dict() for name, device in self.devices.items()
            },
        }
        return json.dumps(data)


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------


def build_will_config(topic_prefix: str) -> WillConfig:
    """Create a :class:`WillConfig` for the app's LWT.

    The resulting config targets ``{topic_prefix}/status`` with payload
    ``"offline"``, QoS 1, retained.  Pass this to :class:`MqttClient`
    so the broker publishes ``"offline"`` on unexpected disconnection.

    Parameters
    ----------
    topic_prefix:
        Application-level topic prefix (e.g. ``"velux2mqtt"``).

    Returns
    -------
    WillConfig
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

    Parameters
    ----------
    mqtt:
        MQTT port used for publishing.
    topic_prefix:
        Base prefix for health topics (e.g. ``"velux2mqtt"``).
    version:
        Application version string included in heartbeats.
    clock:
        Monotonic clock for uptime measurement (see :class:`ClockPort`).
    """

    mqtt: MqttPort
    topic_prefix: str
    version: str
    clock: ClockPort
    _start_time: float = field(init=False, repr=False)
    _devices: dict[str, DeviceStatus] = field(
        init=False,
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        """Capture the start time for uptime calculation."""
        self._start_time = self.clock.now()

    def set_device_status(self, device: str, status: str = "ok") -> None:
        """Update or add a device's status in the internal tracker.

        Parameters
        ----------
        device:
            Device name (used in topic paths and heartbeat payload).
        status:
            Free-form status string, defaults to ``"ok"``.
        """
        self._devices[device] = DeviceStatus(status=status)

    def remove_device(self, device: str) -> None:
        """Remove a device from internal tracking, if present."""
        self._devices.pop(device, None)

    async def publish_device_available(self, device: str) -> None:
        """Publish ``"online"`` to ``{prefix}/{device}/availability``.

        Also registers the device as ``"ok"`` in internal tracking.
        """
        topic = f"{self.topic_prefix}/{device}/availability"
        await self._safe_publish(topic, "online")
        self.set_device_status(device)

    async def publish_device_unavailable(self, device: str) -> None:
        """Publish ``"offline"`` to ``{prefix}/{device}/availability``.

        Also removes the device from internal tracking.
        """
        topic = f"{self.topic_prefix}/{device}/availability"
        await self._safe_publish(topic, "offline")
        self.remove_device(device)

    async def publish_heartbeat(self) -> None:
        """Publish a structured JSON heartbeat to ``{prefix}/status``.

        The payload includes current uptime, version, and all tracked
        device statuses.
        """
        uptime = self.clock.now() - self._start_time
        payload = HeartbeatPayload(
            status="online",
            uptime_s=uptime,
            version=self.version,
            devices=dict(self._devices),
        )
        topic = f"{self.topic_prefix}/status"
        logger.debug("Publishing heartbeat to %s", topic)
        await self._safe_publish(topic, payload.to_json())

    async def shutdown(self) -> None:
        """Gracefully shut down: publish ``"offline"`` for everything.

        Publishes ``"offline"`` to each tracked device's availability
        topic, then publishes ``"offline"`` to the app status topic,
        and clears internal device tracking.
        """
        logger.info("Health reporter shutting down — publishing offline")
        for device in list(self._devices):
            topic = f"{self.topic_prefix}/{device}/availability"
            await self._safe_publish(topic, "offline")

        status_topic = f"{self.topic_prefix}/status"
        await self._safe_publish(status_topic, "offline")
        self._devices.clear()

    async def _safe_publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = True,
    ) -> None:
        """Publish to MQTT, swallowing any exceptions.

        Publication failures are logged at ERROR level but never
        propagated — fire-and-forget semantics per ADR-012.
        """
        try:
            await self.mqtt.publish(topic, payload, retain=retain, qos=1)
        except Exception:
            logger.exception("Failed to publish health to %s", topic)
