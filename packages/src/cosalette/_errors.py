"""Structured error publication for IoT-to-MQTT bridge applications.

Converts exceptions into structured JSON payloads and publishes them
to MQTT error topics.  Designed for unattended daemons where errors
must be observable remotely.

Topic layout::

    {prefix}/error              ← all errors (global, always published)
    {prefix}/{device}/error     ← per-device errors (when device is known)

Payload schema::

    {
        "error_type": "invalid_command",
        "message": "Human-readable error description",
        "device": "blind" | null,
        "timestamp": "2026-02-14T12:34:56+00:00",
        "details": {}
    }

Publication behaviour:

- **Not retained** — errors are events, not last-known state.
- **QoS 1** — at-least-once delivery for reliability.
- **Fire-and-forget** — publication failures are logged, never propagated.
- **Dual output** — errors are both logged (WARNING) and published.

Consumers supply their own ``error_type_map`` to map domain exception
classes to machine-readable type strings.  Unknown exceptions fall
back to the generic ``"error"`` type.

See Also:
    ADR-011 — Error handling and publishing.
    ADR-006 — Protocol-based ports (MqttPort).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from cosalette._mqtt import MqttPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """Immutable structured error payload.

    Represents a single error event ready for JSON serialisation
    and MQTT publication.
    """

    error_type: str
    message: str
    device: str | None
    timestamp: str
    details: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(asdict(self))


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_error_payload(
    error: Exception,
    *,
    error_type_map: dict[type[Exception], str] | None = None,
    device: str | None = None,
    details: dict[str, object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ErrorPayload:
    """Convert an exception into a structured :class:`ErrorPayload`.

    Looks up the exact class of the exception; subclasses are not matched.

    Args:
        error: The exception to convert.
        error_type_map: Optional mapping from exception types to machine-readable
            ``error_type`` strings.  Falls back to ``"error"`` for
            unmapped types.
        device: Optional device name to include in the payload.
        details: Optional dict of additional context to attach to the payload.
            Defaults to an empty dict when ``None``.
        clock: Optional callable returning a :class:`~datetime.datetime`.
            Defaults to ``datetime.now(UTC)``.

    Returns:
        A frozen dataclass ready for serialisation.
    """
    resolved_map = error_type_map or {}
    error_type = resolved_map.get(type(error), "error")
    now = clock() if clock is not None else datetime.now(UTC)
    return ErrorPayload(
        error_type=error_type,
        message=str(error),
        device=device,
        timestamp=now.isoformat(),
        details=details or {},
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class ErrorPublisher:
    """Publishes structured error payloads to MQTT.

    Wraps :func:`build_error_payload` with fire-and-forget MQTT
    publication.  Errors during publication are logged but never
    propagated — the main application loop must not crash because
    an error *report* failed.

    Args:
        mqtt: MQTT port used for publishing.
        topic_prefix: Base prefix for error topics (e.g. ``"velux2mqtt"``).
        error_type_map: Pluggable mapping from consumer exception types to
            machine-readable type strings.
        clock: Optional callable returning a :class:`~datetime.datetime`
            for deterministic testing.
    """

    mqtt: MqttPort
    topic_prefix: str
    error_type_map: dict[type[Exception], str] = field(default_factory=dict)
    clock: Callable[[], datetime] | None = field(default=None, repr=False)

    async def publish(
        self,
        error: Exception,
        *,
        device: str | None = None,
        is_root: bool = False,
    ) -> None:
        """Build an error payload and publish it to MQTT.

        Always publishes to ``{topic_prefix}/error``.  When *device*
        is provided, also publishes to ``{topic_prefix}/{device}/error``
        (skipped for root devices, whose per-device topic would
        duplicate the global topic).

        The entire pipeline (build → serialise → publish) is wrapped
        in fire-and-forget semantics: failures at *any* stage are
        logged but never propagated to the caller.
        """
        try:
            payload = build_error_payload(
                error,
                error_type_map=self.error_type_map,
                device=device,
                clock=self.clock,
            )
            payload_json = payload.to_json()
        except Exception:
            logger.exception(
                "Failed to build error payload for %r (device=%s)",
                error,
                device,
            )
            return

        global_topic = f"{self.topic_prefix}/error"
        logger.warning(
            "Publishing error: %s (type=%s, device=%s)",
            payload.message,
            payload.error_type,
            device,
        )
        await self._safe_publish(global_topic, payload_json)

        # Skip per-device topic for root devices (same as global)
        if device is not None and not is_root:
            device_topic = f"{self.topic_prefix}/{device}/error"
            await self._safe_publish(device_topic, payload_json)

    async def _safe_publish(self, topic: str, payload: str) -> None:
        """Publish to MQTT, swallowing any exceptions.

        Publication failures are logged at ERROR level but never
        propagated — fire-and-forget semantics per ADR-011.
        """
        try:
            await self.mqtt.publish(topic, payload, retain=False, qos=1)
        except Exception:
            logger.exception("Failed to publish error to %s", topic)
