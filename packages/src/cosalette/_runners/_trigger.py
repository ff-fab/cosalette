"""Trigger payload for triggerable telemetry devices.

Provides the :class:`TriggerPayload` frozen dataclass that telemetry
handlers opt into via dependency injection to access trigger context
and MQTT payload data.

See Also:
    ADR-036 — Triggerable telemetry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Singleton for non-triggered (scheduled) runs.
#: Assigned after the class body because ``TriggerPayload()`` can only be
#: called once the class exists.  The ``scheduled()`` classmethod is the
#: sole public accessor — never reference this name directly.
_SCHEDULED: TriggerPayload | None = None


@dataclass(frozen=True, slots=True)
class TriggerPayload:
    """Trigger context for triggerable telemetry handlers.

    Injected via DI when a handler declares a ``TriggerPayload``
    parameter.  On scheduled runs, ``is_triggered`` is ``False``
    and ``raw``/``data`` are ``None``.  On MQTT-triggered runs,
    ``is_triggered`` is ``True`` and the MQTT payload is available.

    Examples:
        Simple check::

            @app.telemetry("sensor", interval=60, triggerable=True)
            async def read_sensor(
                adapter: SensorPort,
                trigger: TriggerPayload,
            ) -> dict[str, object]:
                days = trigger.get("days", 7) if trigger.is_triggered else 7
                return await adapter.read(days=days)
    """

    is_triggered: bool = False
    raw: str | None = None
    data: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Extract a key from parsed JSON data, with fallback.

        Returns *default* when not triggered, when payload was not
        valid JSON, or when *key* is absent.
        """
        if self.data is None:
            return default
        return self.data.get(key, default)

    @classmethod
    def scheduled(cls) -> TriggerPayload:
        """Return the singleton scheduled-run instance."""
        assert _SCHEDULED is not None  # noqa: S101
        return _SCHEDULED

    @classmethod
    def from_mqtt(cls, payload: str) -> TriggerPayload:
        """Create a triggered instance from an MQTT payload string.

        ``raw`` always preserves the exact payload string received.  A blank
        payload (empty or whitespace-only) is the documented bare ``/set``
        "just re-run" trigger and is treated as an empty JSON object, so
        ``data`` is ``{}`` (equivalent to sending ``"{}"``).  Non-blank
        payloads that are not a JSON object leave ``data`` as ``None``.
        """
        data: dict[str, Any] | None = None
        # A blank payload (empty or whitespace-only) is the documented bare
        # `/set` "just re-run" trigger; treat it as an empty JSON object so it
        # is equivalent to sending "{}" (framework-findings F-1).
        to_parse = payload.strip() or "{}"
        try:
            parsed = json.loads(to_parse)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            logger.debug("Trigger payload is not valid JSON: %r", payload[:100])
        return cls(is_triggered=True, raw=payload, data=data)


_SCHEDULED = TriggerPayload(is_triggered=False)
