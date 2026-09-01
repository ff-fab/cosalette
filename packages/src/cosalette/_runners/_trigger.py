"""Trigger payload and trigger-source declarations for telemetry devices.

Provides the :class:`TriggerPayload` frozen dataclass that telemetry
handlers opt into via dependency injection to access trigger context and
payload data, plus the :data:`TriggerSource` declaration accepted by
``@app.telemetry(triggerable=...)``.

See Also:
    ADR-036 — Triggerable telemetry (MQTT ``/set`` trigger).
    ADR-064 — Local (in-process) trigger source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, get_args

from cosalette._json import loads

logger = logging.getLogger(__name__)

type TriggerSource = Literal["mqtt", "local", "both"]
"""Which arming paths a triggerable telemetry entity accepts.

- ``"mqtt"`` — an inbound message on ``{prefix}/{name}/set`` (ADR-036).
- ``"local"`` — an in-process :class:`~cosalette.EntityNotifier` call.
- ``"both"`` — either path arms the entity.
"""

type TriggerableSpec = bool | TriggerSource
"""Accepted values for ``@app.telemetry(triggerable=...)``.

``False`` (the default) disables triggering, ``True`` is an alias for
``"mqtt"``, and the string forms select the arming path(s) explicitly.
"""

type TriggerRunSource = Literal["scheduled", "mqtt", "local"]
"""What caused the current handler run — see :attr:`TriggerPayload.source`."""

_TRIGGER_SOURCES: tuple[str, ...] = get_args(TriggerSource.__value__)

#: Singletons for the two payload-free run sources.  Assigned after the
#: class body because ``TriggerPayload()`` can only be called once the
#: class exists.  The ``scheduled()`` / ``local()`` classmethods are the
#: sole public accessors — never reference these names directly.
_SCHEDULED: TriggerPayload | None = None
_LOCAL: TriggerPayload | None = None


def normalize_trigger_source(triggerable: TriggerableSpec) -> TriggerSource | None:
    """Normalize a ``triggerable=`` value to a source, or ``None`` when off.

    ``True`` maps to ``"mqtt"`` (its ADR-036 meaning) and ``False`` to
    ``None``.  String values are returned unchanged after validation.

    Raises:
        ValueError: If *triggerable* is not a bool or a known source name.
    """
    if isinstance(triggerable, bool):
        return "mqtt" if triggerable else None
    if triggerable in _TRIGGER_SOURCES:
        return triggerable
    msg = (
        f"triggerable={triggerable!r} is not a valid trigger source. "
        f"Use True/False or one of {', '.join(map(repr, _TRIGGER_SOURCES))}."
    )
    raise ValueError(msg)


def arms_via_mqtt(source: TriggerSource | None) -> bool:
    """Return ``True`` when *source* subscribes ``{prefix}/{name}/set``."""
    return source in ("mqtt", "both")


def arms_locally(source: TriggerSource | None) -> bool:
    """Return ``True`` when *source* can be armed by an ``EntityNotifier``."""
    return source in ("local", "both")


@dataclass(frozen=True, slots=True)
class TriggerPayload:
    """Trigger context for triggerable telemetry handlers.

    Injected via DI when a handler declares a ``TriggerPayload``
    parameter.  On scheduled runs, ``is_triggered`` is ``False``,
    ``raw``/``data`` are ``None`` and ``source`` is ``"scheduled"``.  On
    MQTT-triggered runs, ``is_triggered`` is ``True``, ``source`` is
    ``"mqtt"`` and the MQTT payload is available.  On locally-woken runs
    ``source`` is ``"local"`` and there is no payload — the notifier
    passes a name, not data.

    Examples:
        Simple check::

            @app.telemetry("sensor", interval=60, triggerable=True)
            async def read_sensor(
                adapter: SensorPort,
                trigger: TriggerPayload,
            ) -> dict[str, object]:
                days = trigger.get("days", 7) if trigger.is_triggered else 7
                return await adapter.read(days=days)

        Distinguishing the wake source::

            if trigger.source == "local":
                ...  # woken by a hardware push
    """

    is_triggered: bool = False
    raw: str | None = None
    data: dict[str, Any] | None = None
    source: TriggerRunSource = "scheduled"

    def get(self, key: str, default: Any = None) -> Any:
        """Extract a key from parsed JSON data, with fallback.

        Returns *default* when not triggered, when the run was a local
        wake (no payload), when the payload was not valid JSON, or when
        *key* is absent.
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
    def local(cls) -> TriggerPayload:
        """Return the singleton local-wake instance.

        A local wake carries no payload: :attr:`raw` and :attr:`data`
        are ``None`` while :attr:`is_triggered` is ``True``.
        """
        assert _LOCAL is not None  # noqa: S101
        return _LOCAL

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
            parsed = loads(to_parse)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError, RecursionError:
            # JSONDecodeError is a ValueError; RecursionError is defensive
            # against a backend without a C-level nesting cap (CWE-674).
            logger.debug("Trigger payload is not valid JSON: %r", payload[:100])
        return cls(is_triggered=True, raw=payload, data=data, source="mqtt")


_SCHEDULED = TriggerPayload(is_triggered=False)
_LOCAL = TriggerPayload(is_triggered=True, source="local")
