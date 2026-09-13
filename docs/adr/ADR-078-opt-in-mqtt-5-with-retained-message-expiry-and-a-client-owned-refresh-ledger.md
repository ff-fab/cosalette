---
status: Accepted
date: 2026-09-13
impact: moderate
tags: [mqtt, configuration, health, persistence, lifecycle]
---

# ADR-078: Opt-in MQTT 5 with Retained Message Expiry and a Client-Owned Refresh Ledger

## Status

Accepted **Date:** 2026-09-13

## Context

A framework enhancement proposal from the early adopter project `ff-fab/cosalette-apps` (2026-09-13, `tmp/framework-enhancement-proposal.md`, beads epic cos-i950) reports two independent production incidents with the same root cause. ADR-048 clears a removed entity's retained `state`/`availability` topics by diffing the current registrations against a previous-run snapshot in the app's `Store`; ADR-049 made that store zero-config, but the default path is an ephemeral in-container directory unless `<NAME>_STORE_PATH` points at a mounted volume. `caldates2mqtt` (0.1.5 -> 0.2.3, no store path set) removed a `birthday` calendar and its `caldates2mqtt/birthday/{state,availability}` topics plus the Home Assistant discovery config stayed on the broker; `airthings2mqtt` (edge-01) renamed its device `airthings` -> `airthings_edge01` and the old `airthings2mqtt-upper/airthings/{state,availability}` topics were found weeks later. Both were invisible until a log review, and both needed a manual `mosquitto_pub -r -n` per topic. Setting `*_STORE_PATH` prevents future orphans but is exactly the 'apps without a persisted store receive no cleanup' limitation ADR-048 lists under its negative consequences, and it cannot clear what is already stuck.

The underlying property is that MQTT 3.1.1 retained messages never expire, and the fleet's broker (mosquitto 2.0.22, `persistence true`) keeps them across its own restarts, so whether an orphan is ever cleared depends entirely on the publishing app having an unbroken memory of everything it ever published. MQTT 5 adds a per-message `Message Expiry Interval` property that the broker enforces itself: a retained message that is not refreshed inside its window is dropped by the broker, independent of whether the app that published it remembers it, has a store, or ever runs again.

The framework's production adapter (`packages/src/cosalette/_mqtt/_client.py`) builds `aiomqtt.Client(hostname, port, username, password, identifier, will, tls_context)` and never passes `protocol=`. The pinned `aiomqtt>=2.5.0` (2.5.1 installed, paho-mqtt 2.1.0 backend) already exposes `protocol=ProtocolVersion.V5`, `publish(..., properties=)` and `Will.properties`; nothing is wired up. `aiomqtt` 3.0.0-alpha.1 (2026-04-03) replaces the backend, drops 3.1.1 and removes `protocol=` — a separate, larger migration this decision must not depend on. The broker negotiates the protocol version per connection (mosquitto has served 3.1.1 and 5 clients side by side since 2.0.0), and openHAB's MQTT broker thing has an `mqttVersion` toggle, so nothing outside cosalette needs to change and adoption can be per app.

A spike against `eclipse-mosquitto` via testcontainers (2026-09-13, aiomqtt 2.5.1 in v5 mode) confirmed the mechanism end to end: a retained publish with `MessageExpiryInterval=2` is gone for a fresh subscriber after the window, a republish inside the window extends it, a 3.1.1 subscriber receives v5-published retained messages unchanged, and a last-will message carrying the property also expires.

The proposal's sketch assumes 'a healthy app keeps refreshing its retained topics inside their expiry window as part of its normal publish cycle' and therefore proposes an expiry derived from the poll interval (3-5x). That assumption does not hold for most framework-owned retained topics today: per-entity `availability` is published on transitions only (`HealthReporter`, ADR-077 deduplication), telemetry `state` under an `OnChange` publish strategy is suppressed while the value is unchanged, Home Assistant discovery config, `_meta/registry`, `_meta/state_model_drift` and `schema/status` are published on (re)connect only (`register_connect_reannounce`), and user `ctx.publish(..., retain=True)` has no cycle at all. Only the `{prefix}/status` heartbeat and non-deduplicated telemetry state are refreshed periodically. Expiry without a refresh mechanism would silently drop legitimate retained topics of a perfectly healthy app.

## Decision

Add opt-in MQTT 5 support to `MqttClient` and, when it is selected, stamp every retained publish and the last-will message with a `Message Expiry Interval`, because a broker-enforced TTL turns retained-topic cleanup from 'the app must remember everything it ever published, forever, across every restart' into 'the broker drops any retained claim this process stops refreshing'. To make that safe for topics the framework only publishes on transitions or on connect, `MqttClient` owns a **retained ledger** — the last payload and QoS per topic for every `publish(retain=True)` — and, while expiry is active, republishes the whole ledger every `expiry / 2` seconds and once on every (re)connect before the connect callbacks run. Expiry is therefore uniform for every retained topic the process owns (state, availability, status, discovery config, `_meta`, `schema`, user retained publishes) with no per-call-site change and no change to `MqttPort`, `MockMqttClient` or `NullMqttClient`; a renamed or removed entity's topics simply stop being refreshed and age out on their own. ADR-048's store-based diff is kept unchanged as the prompt cleanup path; expiry is the safety net for every case that store cannot cover.

**Sub-decision: settings and defaults.** `MqttSettings.protocol_version: Literal["3.1.1", "5"] = "3.1.1"` (`MQTT__PROTOCOL_VERSION`) keeps every existing deployment byte-identical — the `aiomqtt.Client` kwargs are untouched unless `"5"` is selected. `MqttSettings.message_expiry_interval: int | None = 86400` seconds (`MQTT__MESSAGE_EXPIRY_INTERVAL`, `>= 1`, MQTT 5 only) is inert under 3.1.1 so that `MQTT__PROTOCOL_VERSION=5` alone turns the safety net on; `None` selects MQTT 5 without expiry and without the refresh loop. Setting `message_expiry_interval` explicitly while `protocol_version` is `"3.1.1"` is a validation error, the same 'would be silently ignored' rule `_validate_tls_settings` applies to the TLS file settings. The default is a fixed constant rather than a multiple of the poll interval: with the ledger refresh the only thing expiry has to exceed is the downtime after which stale retained values should stop being presented as current, and 24 h bounds an orphan's lifetime to a day while surviving ordinary deploys, broker restarts and overnight outages.

**Sub-decision: refresh mechanics.** The refresh period is `expiry / 2`, so one missed pass (a disconnect at tick time) never lets a topic expire. The ledger is refreshed inside `_run_connect_callbacks` *before* the callbacks, so the framework's own reannounce publishes (fresher values such as `online` for a recovered device) always win the ordering. The ledger is read per topic at publish time, never as a snapshot, and there is no suspension point between reading an entry and paho enqueueing the packet, so a concurrent application publish can never be overwritten by a stale refresh. An empty retained payload — the clear convention of ADR-031 and ADR-048 — removes the ledger entry. When the client is disconnected the pass is skipped; the reconnect refresh covers it.

**Sub-decision: last will.** The `WillConfig` translation gains the same expiry property on the `WILLMESSAGE` packet, so the broker-published retained `offline` on `{prefix}/status` ages out as well; a decommissioned app leaves no permanent footprint.

**Sub-decision: answers to the proposal's open questions.** (1) No poll-scaled multiplier: the ledger refresh decouples expiry from poll cadence entirely. (2) No separate, longer expiry for discovery config: while the app runs every retained topic is refreshed alike, so only downtime matters and one knob covers it; a per-kind expiry can be added later without a breaking change if a fleet needs it. (3) ADR-048 store cleanup stays — it clears a removed entity on the very next start, whereas expiry needs up to a full window.

```python
# packages/src/cosalette/_settings/__init__.py
class MqttSettings(BaseModel):
    ...
    protocol_version: Literal["3.1.1", "5"] = "3.1.1"   # MQTT__PROTOCOL_VERSION
    message_expiry_interval: int | None = 86400          # seconds, MQTT 5 only

# Opt in per app — nothing else changes for the broker or other clients:
#   MQTT__PROTOCOL_VERSION=5
# Optional: MQTT__MESSAGE_EXPIRY_INTERVAL=172800  (or unset it: =null)

# packages/src/cosalette/_mqtt/_client.py (sketch)
client_kwargs = {...}
if self.settings.protocol_version == "5":
    client_kwargs["protocol"] = aiomqtt.ProtocolVersion.V5

async def publish(self, topic, payload, *, retain=False, qos=1):
    if retain and self._expiry_active:
        # "" is the clear convention (ADR-031/048): forget, never refresh
        if payload == "":
            self._retained.pop(topic, None)
        else:
            self._retained[topic] = (payload, qos)
    await self._client.publish(topic, payload, retain=retain, qos=qos,
                               properties=self._publish_properties if retain else None)

async def _refresh_retained(self):        # every expiry/2, and on (re)connect
    for topic in list(self._retained):
        entry = self._retained.get(topic)  # read at publish time, not a snapshot
        if entry is not None:
            await self.publish(topic, entry[0], retain=True, qos=entry[1])
```

## Decision Drivers

- Cleanup must not depend on the app having an unbroken, persisted memory of everything it ever published — that dependency is what failed in both incidents and is a recorded negative consequence of ADR-048
- Legitimately unchanged retained topics of a healthy app (transition-only availability, OnChange-suppressed state, connect-only discovery and _meta snapshots, user retained publishes) must never expire
- Opt-in and backward compatible: the default protocol stays 3.1.1 with byte-identical aiomqtt kwargs, and 3.1.1 subscribers (Home Assistant, openHAB, dashboards) keep receiving v5-published retained messages unchanged
- Locality: one adapter and one settings model change, no MqttPort / MockMqttClient / NullMqttClient / call-site changes, no new dependency
- Build on the current aiomqtt>=2.5.0 pin where protocol= and properties= already exist; do not design around aiomqtt 3.0 (alpha, drops 3.1.1), which is a separate future migration
- Operator burden of a single env var per app; broker and consumers need no reconfiguration beyond what they already support

## Considered Options

### Option 1: MQTT 5 expiry on every retained publish plus a client-owned refresh ledger (chosen)

Opt-in `protocol_version="5"`; `MqttClient` stamps every retained publish and the will with `Message Expiry Interval`, remembers the last retained payload per topic, republishes the ledger every `expiry/2` and before the connect callbacks on every (re)connect, and forgets a topic on an empty retained publish.

- *Advantages:* Broker-enforced TTL: orphans age out even with store=None, a wiped store, or an app that never runs again — covers both reported incidents with no STORE_PATH configuration; Uniform for every retained topic the process owns, including transition-only availability, OnChange-suppressed state, connect-only discovery/_meta snapshots and user retained publishes; Fixed default expiry independent of poll cadence — no per-app tuning, no multi-interval apps to reason about; Confined to MqttClient and MqttSettings; MqttPort, the test doubles and every call site are untouched; Verified end to end against a real mosquitto before adoption
- *Disadvantages:* Adds a background refresh task and a per-topic ledger (bounded by the number of retained topics) to the production adapter; An app down longer than the expiry loses its retained footprint until it reconnects and republishes; after a Home Assistant restart in that window the entities stay absent until the app returns; Orphans linger for up to one full expiry window (24 h by default) rather than being cleared at the next start; Requires MQTT 5 on the broker (mosquitto >= 2.0) — 3.1.1-only brokers cannot use it

### Option 2: Expiry only on state, availability and discovery, scaled 3-5x off the poll interval (the proposal's sketch)

Wire up MQTT 5 and put an expiry derived from the app's telemetry interval on state/availability/discovery publishes only, relying on the normal publish cycle to refresh them.

- *Advantages:* Smallest code change — no refresh loop, no ledger; Expiry naturally tracks the app's cadence for non-deduplicated telemetry state
- *Disadvantages:* Availability is published on transitions only, so a device online for weeks would have its availability expire and appear unavailable; OnChange-suppressed state (a contact closed for weeks) and connect-only discovery/_meta/schema snapshots are never refreshed and would expire on a healthy app; Apps with several telemetry intervals, stream and device archetypes or user retained publishes have no single poll interval to scale from; Would need per-call-site knowledge of which retained publishes carry expiry, spreading the concern over health, wiring, context and discovery modules

### Option 3: Framework-level periodic re-announce on the heartbeat task

Keep expiry on selected publishes and extend the existing heartbeat task to re-run the connect-time reannounce (availability, discovery, registry) periodically; leave state to the runners.

- *Advantages:* Reuses the existing reannounce and heartbeat machinery; No new state in the MQTT adapter
- *Disadvantages:* Still misses OnChange-suppressed state and user retained publishes, so those would need their own refresh paths; Spreads one invariant ('every retained topic is refreshed inside its window') over the heartbeat task, the health reporter, the discovery wiring and each runner; Couples the safety net to heartbeat_interval, which apps may disable

### Option 4: Default the store to an auto-detected mounted data directory

Resolve the ADR-049 default store path to a conventional writable mount (e.g. `/app/data`) so ADR-048 cleanup works out of the box for the common bind-mount deployment shape.

- *Advantages:* No protocol change; works with 3.1.1-only brokers; Clears a removed entity on the next start rather than after an expiry window
- *Disadvantages:* Only helps deployments that happen to mount the guessed directory, and only prevents future orphans — nothing is ever cleared retroactively; Still depends on an unbroken store across every restart; a wiped volume or a fresh node recreates the incident; Rejected by the proposal itself and already covered by ADR-049's explicit <NAME>_STORE_PATH override

## Decision Matrix

| Criterion | MQTT 5 expiry on every retained publish plus a client-owned refresh ledger | Expiry only on state, availability and discovery, scaled 3-5x off the poll interval (the proposal's sketch) | Framework-level periodic re-announce on the heartbeat task | Default the store to an auto-detected mounted data directory |
| --- | --- | --- | --- | --- |
| Cleanup independent of app memory and store | 5 | 4 | 4 | 1 |
| Safety for legitimately unchanged retained topics | 5 | 1 | 3 | 5 |
| Locality of the change (modules touched) | 4 | 2 | 2 | 4 |
| Backward compatibility for 3.1.1 deployments | 5 | 5 | 5 | 4 |
| Operator configuration burden | 5 | 3 | 4 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Both reported incident shapes (removed entity, renamed device) are covered by the broker with no STORE_PATH configuration: the orphaned state, availability and discovery topics expire within one window
- Every retained topic the process owns is refreshed alike while it runs, so enabling expiry never makes a healthy app's availability, deduplicated state or discovery config disappear
- Fully opt-in; the 3.1.1 path is byte-identical, 3.1.1 subscribers are unaffected, and adoption is one env var per app on any mosquitto >= 2.0
- ADR-048's store-based cleanup keeps its prompt-cleanup role; the two layers compose rather than compete
- No new dependency and no coupling to aiomqtt 3.0 — the feature sits on API that has been in the pinned 2.x line for years

### Negative

- A new background task and per-topic ledger in MqttClient, and a settings validator that rejects an explicit message_expiry_interval under 3.1.1
- An app offline for longer than the expiry loses its retained footprint until it reconnects; if Home Assistant restarts in that window its MQTT entities stay absent until the app republishes discovery on connect
- Orphans linger for up to a full expiry window (24 h by default) instead of being cleared at the next start — apps that want both keep a persisted store for ADR-048
- Two settings whose meaning depends on each other: message_expiry_interval is inert under the default protocol and must be documented as MQTT 5 only
- 3.1.1-only brokers gain nothing from this decision

_2026-09-13_
