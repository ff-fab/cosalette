---
status: Accepted
date: 2026-07-11
impact: moderate
tags: [mqtt, health, persistence, lifecycle]
---

# ADR-048: Clear orphaned retained topics for removed entities

## Status

Accepted **Date:** 2026-07-11 | Amended **Date:** 2026-07-12

## Context

The framework publishes RETAINED availability (`{prefix}/{name}/availability` = `online`/`offline`, QoS 1) and RETAINED state (`{prefix}/{name}/state`, QoS 1) topics (ADR-002 topic conventions, ADR-012 health/availability). MQTT brokers persist a retained message until it is explicitly cleared by publishing an empty (zero-byte) retained message to the same topic.

When an operator removes a device or entity from an app's configuration between restarts, the framework simply stops publishing to that entity's topics — it never clears the previously-retained values. The stale retained availability and state then linger on the broker indefinitely and mislead subscribers: for example, Home Assistant renders a "ghost" entity/device that appears to still exist. This was observed during a `caldates2mqtt` smoke test — a removed `contact_birthdays` calendar left `caldates2mqtt/birthday/availability` = `offline` retained on the broker.

The framework already uses the "clear retained" convention (empty retained publish) for sub-entities on context-manager exit (ADR-031), but has no equivalent for entities removed across process restarts. Any app that adds or removes configured entities between deployments therefore accumulates orphaned retained topics, and today the only remediation is manual (`mosquitto_pub -r -n -t <topic>`).

Two hard constraints apply to any solution: (1) the internal `MqttPort` abstraction exposes `subscribe` and `publish` but has no `unsubscribe` and no way to read a retained message or detect the end of a retained-message burst, so a broker "scan" is not possible without expanding the port; (2) there is no app-scoped persistence wrapper today — `App(store=...)` resolves a single backend `Store` and only `DeviceStore` is injectable — though the framework can still read and write a reserved key on the raw `Store` internally.

Tracked by beads cos-5pt; related: ADR-002 (topic conventions), ADR-012 (health/availability and reconnect re-announce), ADR-031 (sub-entity retained-state clear-on-exit).

## Decision

Use an opt-in, store-backed previous-run entity snapshot to clear orphaned retained topics, because it works within the current MQTT abstractions and is deterministic. The framework persists the current run's resolved entity set (named device/telemetry/command entities plus the root-device flag, and which retained topic kinds each owns) under a reserved, prefix-namespaced key in the app's configured `Store`. On the first successful MQTT connect only (never on subsequent reconnects), it diffs the current resolved registrations against the persisted previous-run set and publishes empty retained messages to clear the `state` and `availability` topics of entities that were present before but are absent now, then persists the updated set. Cleanup is a no-op for apps that have not configured a `Store`. Dynamically created sub-entities (ADR-031) are explicitly out of scope, and command-only `/set` topics, `error` topics, and app-level `status`/`_meta`/`schema` topics are never cleared.

**Sub-decision: Snapshot storage and key.** Persist the entity set under a reserved key namespaced by the resolved topic prefix (e.g. an internal `__cosalette_entity_snapshot__` record), storing entity names, the root-device flag (so `{prefix}/state` versus `{prefix}/{name}/state` are distinguished), the retained topic kinds owned, and a schema version for forward compatibility. Only the raw backend `Store` is used internally; this is not exposed via DI.

**Sub-decision: Trigger point (once, not every reconnect).** Perform the diff-and-clear exactly once, on the first successful connect, reusing the connect-aware first-connect branch (`register_connect_reannounce`) for real clients and the eager `publish_startup_snapshot` path for non-connect-aware clients. Subsequent reconnects must not re-clear, so that a broker restart during normal service does not repeatedly delete topics.

**Sub-decision: Cleanup scope.** Clear only `state` and `availability` retained topics for removed named devices and the root device. Explicitly exclude runtime-created sub-entities (ADR-031), command `/set` and `/+/set` channels, `error` topics, and app-level `status`, `_meta/registry`, and `schema/status` topics.

## Decision Drivers

- Prevent ghost entities/devices in downstream consumers (Home Assistant, dashboards) when configured entities change between deployments
- Reuse the existing MQTT "clear retained" convention (empty retained publish) already used for sub-entity cleanup (ADR-031) rather than inventing new mechanics
- Avoid expanding the `MqttPort` abstraction, which currently lacks `unsubscribe` and any retained-message introspection
- Be safe by construction: never clear topics for entities that are still configured, dynamically created at runtime, or owned by another app
- Operate deterministically from the framework's own resolved registration knowledge, not from fragile broker scans that race against first-connect re-announce

## Considered Options

### Option 1: Store-backed previous-run entity snapshot (chosen)

Persist the resolved entity set to a reserved, prefix-namespaced key in the app's `Store`; on first connect, diff current versus persisted and publish empty retained messages to clear removed entities' `state` and `availability` topics, then persist the new set. This is a no-op when no `Store` is configured.

- *Advantages:* Works with the current `MqttPort` — needs only retained publishes, not retained reads, so no new port APIs or protocol changes are required; Deterministic diff from the resolved registrations, including root devices and multi-segment router names; Runs once on first connect, so normal broker-reconnect recovery is unaffected; Needs no wildcard `{prefix}/#` subscription and reuses the established empty-retained-publish convention
- *Disadvantages:* Does nothing for apps that have not configured `store=`; Requires a reserved app-scoped snapshot key plus a schema-version and collision policy; Must deliberately exclude dynamically created sub-entities; Cannot protect against two independent apps intentionally sharing one MQTT prefix

### Option 2: Broker retained-topic enumeration on startup

On first connect, subscribe to `{prefix}/#`, collect retained messages, diff the observed topics against current registrations, publish empty retained messages to clear orphans, then unsubscribe.

- *Advantages:* Requires no local persistence and inspects what is actually retained on the broker; Could in principle clean orphans even when `store=` is unset; Could observe stale dynamic sub-entity topics
- *Disadvantages:* The current `MqttPort` has no `unsubscribe`, no retained-flag exposure, and no end-of-retained-burst signal, so it cannot be implemented without expanding the port and protocol; A `{prefix}/#` subscription would remain active for the whole process lifetime and receive unrelated traffic; High risk with shared prefixes, dynamic sub-entities, and races against the first-connect re-announce; Would require real-broker integration coverage

### Option 3: No framework mechanism (document manual cleanup)

Keep the status quo; document that operators must manually clear stale retained topics (e.g. `mosquitto_pub -r -n`) after removing an entity.

- *Advantages:* Zero framework complexity and zero risk of clearing a still-valid topic; No new persistence, lifecycle, or MQTT surface
- *Disadvantages:* Ghost entities persist by default, and every app hits this whenever configured entities change; Poor operator DX; Contradicts the Home Assistant discovery-cleanup conventions the framework otherwise aligns with

## Decision Matrix

| Criterion | Store-backed previous-run entity snapshot | Broker retained-topic enumeration on startup | No framework mechanism (document manual cleanup) |
| --- | --- | --- | --- |
| Effectiveness at clearing orphans | 4 | 4 | 1 |
| Safety (never clears active, dynamic, or other-app topics) | 4 | 2 | 5 |
| Fit with current MQTT abstractions (no new port APIs) | 5 | 1 | 5 |
| Implementation simplicity | 3 | 1 | 5 |
| Downstream DX (no ghost entities) | 4 | 4 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Removed entities no longer leave ghost availability/state retained on the broker; Home Assistant and dashboards reflect configuration changes across restarts.
- Reuses the established empty-retained-publish convention; no new `MqttPort` surface or protocol change.
- Deterministic, framework-owned diff that runs once per process on first connect.
- Fully opt-in via the existing `Store` backend — zero behaviour change for store-less apps.

### Negative

- Apps without a configured `Store` receive no orphan cleanup (a documented limitation of this approach).
- Introduces a reserved app-scoped `Store` key plus a snapshot schema-version/collision policy.
- Dynamic sub-entities (ADR-031) are out of scope; their orphaned retained topics are not cleared by this mechanism.
- Independent apps that intentionally share one MQTT prefix remain a hazard the framework cannot fully guard against.
- Adds a small persistence read/write and diff step to first-connect startup.

## Amendment (2026-07-12) — Minor

!!! note "Editorial note (2026-07-12)"
    Implemented 2026-07-12. The chosen store-backed previous-run entity snapshot ships in `cosalette._wiring._retained_cleanup` (`build_entity_snapshot` + `reconcile_retained_topics`), wired into the first-connect branch of `register_connect_reannounce` and the eager `publish_startup_snapshot` path for non-connect-aware adapters.

!!! note "Editorial note (2026-07-12)"
    The snapshot is persisted under the reserved, prefix-namespaced key `__cosalette_entity_snapshot__{prefix}` with `schema_version: 1`. An unrecognised schema version is ignored (cleanup skipped, snapshot overwritten with the current version).

!!! note "Editorial note (2026-07-12)"
    Safety properties as decided: no-op without a configured `Store`; fail-closed (any error is logged and swallowed so startup is never interrupted); persisted entity names are validated against the MQTT name grammar before any publish (defense against a tampered snapshot); only `state` and `availability` retained topics are ever cleared — `/set`, `error`, `status`, `_meta`, and `schema` topics are never touched. Covered by 18 unit and integration tests.
