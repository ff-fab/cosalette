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

Signing (ADR-063, F-DP3, optional) adds tamper *detection* — not concurrency
control — for a `Store` backend an attacker may be able to write to. When the
app supplies ``retained_cleanup_snapshot_key``, the snapshot is HMAC-SHA256
signed on save and verified on load; verification failure (including a
pre-existing unsigned snapshot) is treated as no previous snapshot, the same
fail-closed path used for an unrecognized schema version.

See Also:
    ADR-048 — Clear orphaned retained topics for removed entities.
    ADR-031 — Sub-entity clear-on-exit (the empty-retained convention reused here).
    ADR-002 — MQTT topic conventions.
    ADR-063 — Optional HMAC-signed retained-cleanup snapshots.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import TYPE_CHECKING, cast

from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._registration._validation import validate_mqtt_name

if TYPE_CHECKING:
    from pydantic import SecretStr

    from cosalette._mqtt import MqttPort
    from cosalette._persistence._stores import Store

logger = logging.getLogger(__name__)

#: Schema version for the persisted entity snapshot. Bump on structural change.
_SNAPSHOT_SCHEMA_VERSION = 1

#: HMAC algorithm identifier written to the signed envelope (ADR-063).
#: Included in the authenticated payload itself so a tampered ``hmac_alg``
#: value cannot be substituted without also invalidating the digest.
_HMAC_ALG = "hmac-sha256"

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


def _canonical_signed_payload(
    hmac_alg: object, schema_version: object, entities: object
) -> bytes:
    """Return the canonical JSON bytes authenticated by the snapshot HMAC.

    Canonical form is ``sort_keys=True`` with fixed, whitespace-free
    separators so the same logical payload always serializes identically
    regardless of dict insertion order. ``hmac_alg`` is included in the
    authenticated payload (not just stored alongside it) so an attacker who
    can write the ``Store`` cannot swap the algorithm selector without also
    invalidating the digest (ADR-063).
    """
    payload = {
        "hmac_alg": hmac_alg,
        "schema_version": schema_version,
        "entities": entities,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign_snapshot(snapshot: dict[str, object], key: bytes) -> dict[str, object]:
    """Return a copy of *snapshot* with the ``hmac_alg``/``hmac_sha256`` fields set.

    Signs over the canonical JSON of ``hmac_alg``, ``schema_version``, and
    ``entities`` (ADR-063). *snapshot* itself is not mutated.
    """
    digest = hmac.new(
        key,
        _canonical_signed_payload(
            _HMAC_ALG, snapshot.get("schema_version"), snapshot.get("entities")
        ),
        hashlib.sha256,
    ).hexdigest()
    return {**snapshot, "hmac_alg": _HMAC_ALG, "hmac_sha256": digest}


def _snapshot_signature_valid(previous: dict[str, object], key: bytes) -> bool:
    """Return ``True`` when *previous* carries a valid HMAC signature for *key*.

    Fail-closed: an unrecognized (or missing) ``hmac_alg``, a missing or
    non-string ``hmac_sha256``, or a digest mismatch all return ``False``
    without raising. ``hmac_alg`` is checked *before* any digest is computed
    — a future/unrecognized algorithm value must never reach verification
    logic that does not understand it (forward-compat guard, ADR-063).
    Comparison uses :func:`hmac.compare_digest` for timing-safety.
    """
    hmac_alg = previous.get("hmac_alg")
    if hmac_alg != _HMAC_ALG:
        return False
    signature = previous.get("hmac_sha256")
    if not isinstance(signature, str):
        return False
    expected = hmac.new(
        key,
        _canonical_signed_payload(
            hmac_alg, previous.get("schema_version"), previous.get("entities")
        ),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _resolve_previous_snapshot(
    raw: object,
    signing_key: bytes | None,
) -> dict[str, object]:
    """Return the usable previous snapshot or ``{}`` on any verification failure.

    Normalises a non-dict *raw* value to ``{}``. When *signing_key* is given,
    also rejects snapshots that fail HMAC verification — same fail-closed path
    as an unrecognized ``schema_version``.
    """
    if not isinstance(raw, dict):
        return {}
    snapshot = cast("dict[str, object]", raw)
    if signing_key is None:
        return snapshot
    if _snapshot_signature_valid(snapshot, signing_key):
        return snapshot
    if snapshot:  # non-empty but unsigned/invalid/tampered
        logger.warning(
            "Ignoring entity snapshot that failed HMAC verification "
            "(missing, unrecognized, or invalid signature); skipping "
            "orphaned-topic cleanup this run"
        )
    return {}


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
    snapshot_key: SecretStr | None = None,
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

    *snapshot_key* is the opt-in HMAC signing key (ADR-063, F-DP3). ``None``
    (default) preserves today's unsigned behavior exactly: no ``hmac_alg``/
    ``hmac_sha256`` fields are read or written. When supplied, the persisted
    snapshot is signed on save and verified on load; a missing, invalid, or
    mismatched signature — including a pre-existing *unsigned* snapshot from
    before a key was configured — is treated as no previous snapshot (the
    same fail-closed path :func:`_removed_entities` already takes for an
    unrecognized ``schema_version``): this run's cleanup is skipped and the
    snapshot is overwritten with a freshly signed one. The raw secret is
    extracted from *snapshot_key* only at the point of use and is never
    logged or included in any published/stored payload beyond the digest.

    Intended to run exactly once, on the first successful MQTT connect.
    """
    if store is None:
        return
    key = _snapshot_key(prefix)
    signing_key = (
        snapshot_key.get_secret_value().encode("utf-8")
        if snapshot_key is not None
        else None
    )
    try:
        current = build_entity_snapshot(all_registrations)
        previous = _resolve_previous_snapshot(
            await asyncio.to_thread(store.load, key), signing_key
        )
        for name, info in _removed_entities(previous, current).items():
            for topic in _orphan_topics(prefix, name, info):
                await mqtt.publish(topic, "", retain=True, qos=1)
                logger.info("Cleared orphaned retained topic %s", topic)
        to_save = (
            _sign_snapshot(current, signing_key) if signing_key is not None else current
        )
        await asyncio.to_thread(store.save, key, to_save)
    except Exception:
        logger.exception(
            "Orphaned retained-topic reconciliation failed; orphaned topics from "
            "removed entities may persist on the broker until the next successful run"
        )
