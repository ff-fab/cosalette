"""Structured MQTT error publication.

Converts domain exceptions into machine-readable JSON payloads and
publishes them to MQTT error topics for monitoring and diagnostics.

**Topic layout** (relative to ``topic_prefix``)::

    {prefix}/error              ← all errors (global)
    {prefix}/{actuator}/error   ← per-actuator errors (when actuator is known)

**Payload schema** (JSON, UTF-8)::

    {
        "error_type":  "invalid_command",
        "message":     "Invalid command: 'hello' (not a recognised command …)",
        "actuator":    "blind",
        "timestamp":   "2026-02-13T12:34:56+00:00",
        "details":     { "payload": "hello" }
    }

``error_type`` values map 1-to-1 with domain error classes:

============================  =========================
Domain Error                  ``error_type``
============================  =========================
``InvalidCommandError``       ``invalid_command``
``PositionOutOfRangeError``   ``position_out_of_range``
``HomingRequiredError``       ``homing_required``
``ActuatorNotFoundError``     ``actuator_not_found``
``DomainError``               ``domain_error``
============================  =========================

Design choices:

- **Wall-clock timestamps** (``datetime.now(UTC)``) rather than monotonic
  time, because operators correlate error events with real time.
- **Clock injection** via a callable for deterministic test assertions.
- **Retained = False** — errors are events, not last-known state.
- **QoS 1** — at-least-once delivery; errors should survive
  brief network hiccups.
- Errors are logged *and* published — logging provides local
  observability, MQTT provides remote monitoring.

See Also:
    :mod:`velux2mqtt.domain.errors` for the exception hierarchy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from velux2mqtt.domain.errors import (
    ActuatorNotFoundError,
    DomainError,
    HomingRequiredError,
    InvalidCommandError,
    PositionOutOfRangeError,
)
from velux2mqtt.ports.protocols import MqttPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type mapping
# ---------------------------------------------------------------------------

#: Maps each domain error class to a stable, machine-readable string
#: used as the ``error_type`` field in published payloads.
_ERROR_TYPE_MAP: dict[type[DomainError], str] = {
    InvalidCommandError: "invalid_command",
    PositionOutOfRangeError: "position_out_of_range",
    HomingRequiredError: "homing_required",
    ActuatorNotFoundError: "actuator_not_found",
}

_FALLBACK_ERROR_TYPE: str = "domain_error"


def _error_type_for(error: DomainError) -> str:
    """Return the ``error_type`` string for a domain error.

    Uses ``type()`` for exact matching — subclasses not explicitly
    registered fall back to ``"domain_error"``.
    """
    return _ERROR_TYPE_MAP.get(type(error), _FALLBACK_ERROR_TYPE)


# ---------------------------------------------------------------------------
# ErrorPayload — structured value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """Structured error event for MQTT publication.

    All fields are JSON-serialisable.  The ``to_json()`` method produces
    the UTF-8 string published to the broker.

    Attributes:
        error_type: Machine-readable error category (see module-level table).
        message: Human-readable error description (``str(error)``).
        actuator: Actuator name, or ``None`` when the error is global.
        timestamp: ISO 8601 wall-clock timestamp of the error event.
        details: Extra context specific to the error type (e.g. the
            rejected payload, the out-of-range position value).
    """

    error_type: str
    message: str
    actuator: str | None
    timestamp: str
    details: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to a compact JSON string (no unnecessary whitespace).

        ``None`` values in ``actuator`` are serialised as JSON ``null``
        so that the schema is always present and parseable.

        Uses compact separators (no trailing spaces) to minimise
        payload size over MQTT.
        """
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Error → payload conversion
# ---------------------------------------------------------------------------


def _extract_actuator(error: DomainError) -> str | None:
    """Extract the actuator name from a domain error, if present."""
    return getattr(error, "actuator_name", None)


def _extract_details(error: DomainError) -> dict[str, object]:
    """Build the ``details`` dict from error-specific attributes."""
    details: dict[str, object] = {}
    if isinstance(error, InvalidCommandError):
        details["payload"] = error.payload
    elif isinstance(error, PositionOutOfRangeError):
        details["position"] = error.position
    # HomingRequiredError, ActuatorNotFoundError: actuator_name is
    # already captured in the top-level 'actuator' field.
    return details


def build_error_payload(
    error: DomainError,
    *,
    clock: Callable[[], datetime] | None = None,
) -> ErrorPayload:
    """Convert a domain error to a structured :class:`ErrorPayload`.

    Args:
        error: The domain exception to convert.
        clock: Optional callable returning the current wall-clock time.
            Defaults to ``datetime.now(UTC)``.  Inject a fixed callable
            in tests for deterministic timestamps.

    Returns:
        A frozen :class:`ErrorPayload` ready for serialisation.
    """
    now = (clock or _default_clock)()
    return ErrorPayload(
        error_type=_error_type_for(error),
        message=str(error),
        actuator=_extract_actuator(error),
        timestamp=now.isoformat(),
        details=_extract_details(error),
    )


def _default_clock() -> datetime:
    """Return the current UTC wall-clock time."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# ErrorPublisher — application service
# ---------------------------------------------------------------------------


@dataclass
class ErrorPublisher:
    """Publishes structured domain errors to MQTT error topics.

    Wired by the composition root with a concrete :class:`MqttPort`
    adapter.  The publisher is intentionally *fire-and-forget* —
    a failed error publication should not crash the application.

    Topic layout::

        {topic_prefix}/error              ← every error
        {topic_prefix}/{actuator}/error   ← when actuator is known

    Args:
        mqtt: MQTT adapter satisfying :class:`MqttPort`.
        topic_prefix: Root topic path (e.g. ``"velux2mqtt"``).
        clock: Optional wall-clock callable for deterministic testing.
    """

    mqtt: MqttPort
    topic_prefix: str = "velux2mqtt"
    clock: Callable[[], datetime] | None = field(default=None, repr=False)

    async def publish(self, error: DomainError) -> None:
        """Convert *error* to a JSON payload and publish to MQTT.

        Publishes to the global error topic, and additionally to
        the per-actuator error topic when the error carries an
        actuator name.

        Publication failures are logged but **never propagated** —
        error reporting must not crash the main control loop.

        Args:
            error: The domain exception to publish.
        """
        payload = build_error_payload(error, clock=self.clock)
        json_str = payload.to_json()

        logger.warning(
            "Publishing error: %s (type=%s, actuator=%s)",
            payload.message,
            payload.error_type,
            payload.actuator,
        )

        # Global error topic — always published
        global_topic = f"{self.topic_prefix}/error"
        await self._safe_publish(global_topic, json_str)

        # Per-actuator error topic — when actuator is known
        if payload.actuator is not None:
            actuator_topic = f"{self.topic_prefix}/{payload.actuator}/error"
            await self._safe_publish(actuator_topic, json_str)

    async def _safe_publish(self, topic: str, payload: str) -> None:
        """Publish with exception swallowing — errors must not cascade."""
        try:
            await self.mqtt.publish(topic, payload, retain=False, qos=1)
        except Exception:
            logger.exception("Failed to publish error to %s", topic)
