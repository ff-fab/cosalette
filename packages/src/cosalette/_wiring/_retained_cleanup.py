"""Orphaned retained-topic cleanup for entities removed from config (ADR-048).

When an operator removes a device/telemetry/command entity from an app's
configuration between restarts, removed entity RETAINED ``state`` and
``availability`` topics remain on the broker forever, which misleads
subscribers (e.g. Home Assistant renders a "ghost" entity).

This module persists the current run's resolved entity set under a reserved,
prefix-namespaced key in the app's configured :class:`Store`, and on the first
successful MQTT connect diffs it against the previous run's set, publishing
empty (zero-byte) retained messages to clear the ``state``/``availability``
topics of entities that were present before but are absent now.

The mechanism is opt-in: a no-op for apps without a configured store. It is
fail-closed: any error is logged and swallowed so startup never breaks.
Dynamically created sub-entities (ADR-031) are out of scope, and command
``/set``, ``error``, ``status``, ``_meta``, and ``schema`` topics are never
cleared (only ``state``/``availability`` are ever touched).

The mechanism assumes a single writer — one running app instance per
``(store, prefix)`` pair; concurrent instances sharing both can last-save-win
the persisted entity set and mis-diff retained entities.

See Also:
    ADR-048 — Clear orphaned retained topics for removed entities.
    ADR-031 — Sub-entity clear-on-exit (the empty-retained convention reused here).
    ADR-002 — MQTT topic conventions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._registration._validation import validate_mqtt_name

if TYPE_CHECKING:
    from cosalette._mqtt import MqttPort
    from cosalette._persistence._stores import Store

logger = logging.getLogger(__name__)

#: Schema version for the persisted entity snapshot. Bump on structural change.
_SNAPSHOT_SCHEMA_VERSION = 1

#: Reserved store-key prefix for the entity snapshot. Namespaced by the MQTT
#: topic prefix so co-located apps sharing a backend store never collide.
_SNAPSHOT_KEY_PREFIX = "__cosalette_entity_snapshot__"

#: The only retained topic kinds this mechanism ever clears.
_STATE_AND_AVAILABILITY: tuple[str, ...] = ("state", "availability")
_AVAILABILITY_ONLY: tuple[str, ...] = ("availability",)


def _snapshot_key(prefix: str) -> str:
    """Return the reserved store key for *prefix*'s entity snapshot."""
    return f"{_SNAPSHOT_KEY_PREFIX}{prefix}"


def _retained_kinds(
    reg: _DeviceRegistration | _TelemetryRegistration | _CommandRegistration,
) -> tuple[str, ...]:
    """Return the retained topic kinds a registration owns.

    Devices and telemetry own both ``state`` and ``availability``; commands
    own ``availability`` only (they listen on ``/set`` and never retain state).
    """
    if isinstance(reg, _CommandRegistration):
        return _AVAILABILITY_ONLY
    return _STATE_AND_AVAILABILITY


def build_entity_snapshot(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
) -> dict[str, object]:
    """Build a JSON-serializable snapshot of the resolved entity set.

    Entities sharing a name (scoped uniqueness — e.g. a telemetry and a
    command on the same name) merge into one entry whose ``retained_kinds``
    is the union of what each registration owns.
    """
    entities: dict[str, dict[str, object]] = {}
    for reg in all_registrations:
        entry = entities.setdefault(
            reg.name, {"is_root": reg.is_root, "retained_kinds": []}
        )
        kinds = cast("list[str]", entry["retained_kinds"])
        for kind in _retained_kinds(reg):
            if kind not in kinds:
                kinds.append(kind)
    return {"schema_version": _SNAPSHOT_SCHEMA_VERSION, "entities": entities}


def _removed_entities(
    previous: dict[str, object],
    current: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Return entries present in *previous* but absent from *current*.

    Fail-closed: returns an empty mapping when the previous snapshot is
    missing, has an unrecognized schema version, or is structurally invalid.
    """
    if previous.get("schema_version") != _SNAPSHOT_SCHEMA_VERSION:
        if previous:  # non-empty but wrong/unknown version
            logger.warning(
                "Ignoring entity snapshot with unsupported schema_version %r "
                "(expected %d); skipping orphaned-topic cleanup this run",
                previous.get("schema_version"),
                _SNAPSHOT_SCHEMA_VERSION,
            )
        return {}
    prev_entities = previous.get("entities")
    curr_entities = current.get("entities")
    if not isinstance(prev_entities, dict) or not isinstance(curr_entities, dict):
        return {}
    return cast(
        "dict[str, dict[str, object]]",
        {
            name: info
            for name, info in prev_entities.items()
            if name not in curr_entities and isinstance(info, dict)
        },
    )


def _orphan_topics(prefix: str, name: str, info: dict[str, object]) -> list[str]:
    """Return the retained topic addresses to clear for one removed entity.

    Validates *name* against the MQTT name grammar (defense-in-depth: a
    tampered snapshot must never cause a publish to an attacker-chosen topic)
    and restricts cleared kinds to ``state``/``availability`` only.
    """
    try:
        validate_mqtt_name(name)
    except ValueError:
        logger.warning(
            "Skipping orphaned-topic cleanup for invalid entity name %r", name
        )
        return []
    # Strict identity: only a real bool True marks a root device, so a corrupted
    # or tampered snapshot value (e.g. the string "False") cannot widen the
    # cleanup scope to the root-level {prefix}/state and {prefix}/availability.
    base = prefix if info.get("is_root") is True else f"{prefix}/{name}"
    kinds = info.get("retained_kinds", ())
    if not isinstance(kinds, (list, tuple)):
        return []
    return [f"{base}/{kind}" for kind in kinds if kind in _STATE_AND_AVAILABILITY]


async def reconcile_retained_topics(
    mqtt: MqttPort,
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    prefix: str,
    store: Store | None,
) -> None:
    """Clear orphaned retained topics for entities removed since the last run.

    No-op when *store* is ``None``. Loads the previous run's entity snapshot,
    diffs it against the current resolved registrations, publishes empty
    retained messages to clear the ``state``/``availability`` topics of removed
    entities, then persists the current snapshot. Fail-closed: any error is
    logged and swallowed so app startup is never interrupted.

    The synchronous ``store.load`` and ``store.save`` calls are offloaded to a
    worker thread via :func:`asyncio.to_thread` so the event loop is not blocked
    by file, SQLite, or network-backed store I/O.

    Intended to run exactly once, on the first successful MQTT connect.
    """
    if store is None:
        return
    key = _snapshot_key(prefix)
    try:
        current = build_entity_snapshot(all_registrations)
        previous = await asyncio.to_thread(store.load, key)
        if not isinstance(previous, dict):
            # None (no prior snapshot) or a corrupted non-dict payload: start
            # fresh so reconciliation stays fail-closed and always overwrites
            # the stored snapshot with the current schema.
            previous = {}
        for name, info in _removed_entities(previous, current).items():
            for topic in _orphan_topics(prefix, name, info):
                await mqtt.publish(topic, "", retain=True, qos=1)
                logger.info("Cleared orphaned retained topic %s", topic)
        await asyncio.to_thread(store.save, key, current)
    except Exception:
        logger.exception(
            "Orphaned retained-topic reconciliation failed; orphaned topics from "
            "removed entities may persist on the broker until the next successful run"
        )
