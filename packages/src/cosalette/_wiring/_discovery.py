"""Runtime Home Assistant MQTT discovery publication (F23, ADR-059).

Builds HA discovery payloads from an app's live, post-configure/expand
registry — via :meth:`~cosalette.App.asyncapi` — and publishes them as
retained messages on the first successful MQTT connect, opt-in via
:meth:`~cosalette.App.discovery`. Because the source registry is built
*after* ``expand_name_specs`` has already resolved any callable ``name=``
(ADR-023), this dissolves the ADR-051 phantom-entity class entirely: the
callable has already run by the time a payload's topic is constructed.

Also extends ADR-048's orphaned retained-topic cleanup to discovery
``config`` topics, so an entity removed from config no longer leaves a
ghost device behind in Home Assistant.

See Also:
    ADR-059 — Runtime HA discovery publication with enrichment hook.
    ADR-048 — Orphaned retained-topic cleanup (state/availability).
    ADR-051 — Settings-aware schema pipeline for the static (offline) side.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cosalette._json import dumps as _json_dumps
from cosalette._schema._consumer_gen import HaDiscoveryGenerator, HaDiscoveryPayload
from cosalette._schema._loader import (
    InlineSchemaSource,
    _ensure_schema_deps,
    load_schema,
)

if TYPE_CHECKING:
    from cosalette._mqtt import MqttPort
    from cosalette._persistence._stores import Store
    from cosalette._schema._consumer_gen import HaEnrichHook

logger = logging.getLogger("cosalette._wiring")


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Opt-in runtime HA discovery configuration set via :meth:`App.discovery`."""

    discovery_prefix: str = "homeassistant"
    enrich: HaEnrichHook | None = None


#: Schema version for the persisted discovery-topic snapshot.
_DISCOVERY_SNAPSHOT_SCHEMA_VERSION = 1

#: Reserved store-key prefix for the discovery snapshot, namespaced by the HA
#: discovery prefix (not the MQTT topic prefix) since that is what discovery
#: topics are actually rooted under.
_DISCOVERY_SNAPSHOT_KEY_PREFIX = "__cosalette_discovery_snapshot__"


def _discovery_snapshot_key(discovery_prefix: str) -> str:
    """Return the reserved store key for *discovery_prefix*'s topic snapshot."""
    return f"{_DISCOVERY_SNAPSHOT_KEY_PREFIX}{discovery_prefix}"


async def build_discovery_payloads(
    app: Any,  # App — Any to avoid circular import
    config: DiscoveryConfig,
) -> list[HaDiscoveryPayload]:
    """Build (and cache on *app*) HA discovery payloads from the live registry.

    Round-trips the app's canonical AsyncAPI document (:meth:`App.asyncapi`,
    built from post-expand registrations) through the schema loader — the
    same dump-then-load pipeline ``schema dump`` followed by
    ``schema ha-discovery`` already perform as two separate CLI invocations
    against a hand-copied file, done here in-process against live data
    instead. Reusing the loader (rather than a bespoke dict-to-registry path)
    means every extension-validation and ``$ref``-resolution rule the static
    CLI enforces applies identically at runtime.

    The result is cached on the app instance (registrations are immutable
    after app setup), matching :func:`publish_registry_snapshot`'s cache.
    """
    cached: list[HaDiscoveryPayload] | None = getattr(
        app, "_discovery_payloads_cache", None
    )
    if cached is not None:
        return cached

    _ensure_schema_deps()
    import yaml

    doc = app.asyncapi()
    content = yaml.safe_dump(doc, sort_keys=False)
    registry = await load_schema(InlineSchemaSource(content))
    generator = HaDiscoveryGenerator(
        registry=registry,
        discovery_prefix=config.discovery_prefix,
        enrich=config.enrich,
    )
    payloads = generator.generate()
    with contextlib.suppress(TypeError, AttributeError):
        object.__setattr__(app, "_discovery_payloads_cache", payloads)
    return payloads


async def publish_discovery(
    mqtt: MqttPort,
    app: Any,  # App — Any to avoid circular import
    config: DiscoveryConfig,
) -> None:
    """Publish retained HA discovery payloads for *app* (F23 item 1).

    Fail-closed: any error (including a missing ``[schema]`` extra) is
    logged and swallowed so a discovery-publication failure never breaks
    app startup.
    """
    try:
        payloads = await build_discovery_payloads(app, config)
        for payload in payloads:
            await mqtt.publish(
                payload.topic,
                _json_dumps(payload.config),
                retain=True,
                qos=1,
            )
    except Exception:
        logger.exception("Failed to publish Home Assistant discovery payloads")


def _build_discovery_snapshot(topics: list[str]) -> dict[str, object]:
    """Build a JSON-serializable snapshot of the current run's discovery topics."""
    return {
        "schema_version": _DISCOVERY_SNAPSHOT_SCHEMA_VERSION,
        "topics": topics,
    }


def _is_safe_discovery_topic(topic: str, discovery_prefix: str) -> bool:
    """True if *topic* is safe to clear as an orphaned discovery config topic.

    Defense-in-depth mirroring ADR-048's ``_orphan_topics`` name guard: a
    tampered or corrupted snapshot must never cause a publish to an
    attacker-chosen topic, so a stored topic is only honored when it is
    actually shaped like one of ours (rooted under *discovery_prefix*,
    ending in ``/config``) and free of MQTT wildcards / control characters.
    """
    if not topic.startswith(f"{discovery_prefix}/") or not topic.endswith("/config"):
        return False
    return not any(c in "+#\0" or c <= "\x1f" or c == "\x7f" for c in topic)


def _previous_topic_set(previous: object) -> set[str]:
    """Return the topic set from a loaded snapshot, or empty if absent/invalid.

    Fail-closed: an unrecognized schema version, a corrupted non-dict payload,
    or a malformed ``topics`` field are all treated as "no previous snapshot"
    rather than raising, matching ADR-048's ``_removed_entities`` convention.
    """
    if (
        not isinstance(previous, dict)
        or previous.get("schema_version") != _DISCOVERY_SNAPSHOT_SCHEMA_VERSION
    ):
        return set()
    topics = previous.get("topics")
    if not isinstance(topics, list):
        return set()
    return {t for t in topics if isinstance(t, str)}


async def _clear_orphaned_discovery_topics(
    mqtt: MqttPort, orphaned: set[str], discovery_prefix: str
) -> None:
    """Publish an empty retained message for each safe, orphaned topic."""
    for topic in sorted(orphaned):
        if not _is_safe_discovery_topic(topic, discovery_prefix):
            continue
        await mqtt.publish(topic, "", retain=True, qos=1)
        logger.info("Cleared orphaned discovery config topic %s", topic)


async def reconcile_discovery_topics(
    mqtt: MqttPort,
    app: Any,  # App — Any to avoid circular import
    config: DiscoveryConfig,
    store: Store | None,
) -> None:
    """Clear orphaned discovery config topics for entities removed since last run.

    Extends ADR-048's orphaned retained-topic cleanup (``state``/
    ``availability``) to discovery ``config`` topics (F23 item 2). Diffs the
    *topic string* set directly rather than reusing the entity-name-keyed
    ADR-048 snapshot: a discovery topic is keyed by
    ``(component, node_id, object_id)``, not by entity name + retained kind,
    so it needs its own snapshot shape. No-op when *store* is ``None``.
    Fail-closed: any error is logged and swallowed.

    Intended to run exactly once, on the first successful MQTT connect —
    the same point :func:`~cosalette._wiring.reconcile_retained_topics` runs.
    """
    if store is None:
        return
    key = _discovery_snapshot_key(config.discovery_prefix)
    try:
        payloads = await build_discovery_payloads(app, config)
        curr_topics: list[str] = sorted({p.topic for p in payloads})
        current = _build_discovery_snapshot(curr_topics)
        previous = await asyncio.to_thread(store.load, key)
        orphaned = _previous_topic_set(previous) - set(curr_topics)
        await _clear_orphaned_discovery_topics(mqtt, orphaned, config.discovery_prefix)
        await asyncio.to_thread(store.save, key, current)
    except Exception:
        logger.exception(
            "Discovery config-topic reconciliation failed; orphaned discovery "
            "entities from removed devices may persist in Home Assistant until "
            "the next successful run"
        )
