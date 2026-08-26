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
        "message": "ValueError",   # exception class name by default
        "device": "blind" | null,
        "timestamp": "2026-02-14T12:34:56+00:00",
        "id": "9f2c1a4b7e0d",       # correlation id (matches the local log)
        "details": {}
    }

For **unmapped** (i.e. downstream/unexpected) exceptions, ``message`` is the
exception class name only — the raw ``str(error)`` is NOT published, because a
downstream handler's exception text can carry credentials/payloads and the
error topics are broker-visible. The framework's own mapped errors are already
sanitised and keep their message. The full message and traceback are always
logged locally under the correlation ``id``.

To opt **specific** app-owned exception types back into full-message
publishing without un-redacting everything, pass ``App(error_type_map=...)``;
the framework merges it into this publisher's map (framework entries stay
authoritative — see ADR-011). Note that under this legacy coupling a map
entry is both a label and a **message-disclosure decision** (F-DP1): to
register labels without opting into message publication, pass
``App(disclose_messages_for={...})`` — an explicit set that fully defines
which exception types' ``str(error)`` is published, independent of the
label map (see ADR-061). As a blunt alternative, set
``MqttSettings.error_publish_verbose`` (env ``MQTT__ERROR_PUBLISH_VERBOSE``)
to publish the raw message for **every** error.

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
    ADR-061 — Decoupled error-message disclosure (F-DP1).
    ADR-006 — Protocol-based ports (MqttPort).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from cosalette._json import dumps
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
    id: str = ""
    details: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to a JSON string."""
        return dumps(asdict(self))


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
    verbose: bool = False,
    correlation_id: str = "",
    disclose_messages_for: frozenset[type[Exception]] | None = None,
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
        verbose: When ``True``, ``message`` carries the raw ``str(error)``.
            When ``False`` (default), ``message`` is only the exception class
            name so sensitive downstream exception text is never published.
        correlation_id: Optional id echoed into the payload so a broker
            consumer can match it to the full locally-logged error.
        disclose_messages_for: Explicit set of exception types whose
            ``str(error)`` may be published (F-DP1, ADR-061).  When given, it
            **fully defines** the disclosure policy — map membership no longer
            implies disclosure, and framework-map entries must be re-listed
            here to keep their messages.  ``None`` (default) preserves the
            legacy conflated behaviour: mapping a type discloses its message.

    Returns:
        A frozen dataclass ready for serialisation.
    """
    resolved_map = error_type_map or {}
    error_type = resolved_map.get(type(error), "error")
    now = clock() if clock is not None else datetime.now(UTC)
    # Mapped exceptions are the framework's own, already-sanitised errors — keep
    # their message. Unmapped (downstream) exception text can carry secrets, so
    # publish only the class name unless verbose output is explicitly enabled.
    # disclose_messages_for decouples that decision from labeling (F-DP1).
    if verbose:
        disclose = True
    elif disclose_messages_for is not None:
        disclose = type(error) in disclose_messages_for
    else:
        disclose = type(error) in resolved_map
    message = str(error) if disclose else type(error).__name__
    return ErrorPayload(
        error_type=error_type,
        message=message,
        device=device,
        timestamp=now.isoformat(),
        id=correlation_id,
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
        verbose: When ``True``, ``message`` in the MQTT payload carries the
            raw ``str(error)``.  Keep ``False`` on broker-visible topics
            — downstream exception text can carry credentials (LEAK-01).
        disclose_messages_for: Explicit set of exception types whose
            ``str(error)`` may be published (F-DP1, ADR-061).  When given, it
            **fully defines** the disclosure policy, independent of
            ``error_type_map``.  ``None`` (default) preserves the legacy
            conflated behaviour: mapping a type discloses its message.
    """

    mqtt: MqttPort
    topic_prefix: str
    error_type_map: dict[type[Exception], str] = field(default_factory=dict)
    clock: Callable[[], datetime] | None = field(default=None, repr=False)
    verbose: bool = False
    disclose_messages_for: frozenset[type[Exception]] | None = None

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
        correlation_id = uuid.uuid4().hex[:12]
        try:
            payload = build_error_payload(
                error,
                error_type_map=self.error_type_map,
                device=device,
                clock=self.clock,
                verbose=self.verbose,
                correlation_id=correlation_id,
                disclose_messages_for=self.disclose_messages_for,
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
        # Log the full error (message + traceback) locally; the broker payload
        # carries only the type unless error_publish_verbose is set (LEAK-01).
        # exc_info=error passes the BaseException instance directly.
        # Python ≥3.2 logging converts it to (type, e, e.__traceback__)
        # automatically, so the real traceback is captured even outside an
        # except block. Do NOT pass exc_info=True outside an except block
        # — that would silently lose the traceback.
        logger.warning(
            "Publishing error [id=%s type=%s device=%s]: %s",
            correlation_id,
            payload.error_type,
            device,
            error,
            exc_info=error,
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
