---
status: Accepted
date: 2026-09-13
impact: moderate
tags: [mqtt, configuration, health, persistence, lifecycle]
---

# ADR-078: Opt-in MQTT 5 with Retained Message Expiry and a Client-Owned Refresh Ledger

## Status

Accepted **Date:** 2026-09-13 | Amended **Date:** 2026-09-14

## Context

Two production incidents in the early adopter fleet `ff-fab/cosalette-apps` left orphaned retained MQTT topics on the broker after an entity was removed and a device was renamed. The framework enhancement proposal that reports them (2026-09-13, summarised in beads epic cos-i950, whose description records the ask and the maintainer evaluation) traces both to the same root cause. ADR-048 clears a removed entity's retained `state`/`availability` topics by diffing the current registrations against a previous-run snapshot in the app's `Store`; ADR-049 made that store zero-config, but the default path is an ephemeral in-container directory unless `<NAME>_STORE_PATH` points at a mounted volume.

`caldates2mqtt` (0.1.5 -> 0.2.3, no store path set) removed a `birthday` calendar and its `caldates2mqtt/birthday/{state,availability}` topics plus the Home Assistant discovery config stayed on the broker; `airthings2mqtt` (edge-01) renamed its device `airthings` -> `airthings_edge01` and the old `airthings2mqtt-upper/airthings/{state,availability}` topics were found weeks later. Both were invisible until a log review, and both needed a manual `mosquitto_pub -r -n` per topic. Setting `*_STORE_PATH` prevents future orphans but is exactly the 'apps without a persisted store receive no cleanup' limitation ADR-048 lists under its negative consequences, and it cannot clear what is already stuck.

The underlying property is that MQTT 3.1.1 retained messages never expire, and the fleet's broker (mosquitto 2.0.22, `persistence true`) keeps them across its own restarts, so whether an orphan is ever cleared depends entirely on the publishing app having an unbroken memory of everything it ever published. MQTT 5 adds a per-message `Message Expiry Interval` property (an unsigned 32-bit number of seconds, so at most 4 294 967 295) that the broker enforces itself: a retained message that is not refreshed inside its window is dropped by the broker, independent of whether the app that published it remembers it, has a store, or ever runs again. The property travels with the message, so it only ever applies to messages published after an app opts in — a retained message left behind by a 3.1.1 client carries no expiry and stays until it is overwritten or cleared.

The framework's production adapter (`packages/src/cosalette/_mqtt/_client.py`) builds `aiomqtt.Client(hostname, port, username, password, identifier, will, tls_context)` and never passes `protocol=`; it is also where the `WillConfig` last will of ADR-012 is translated and where the `MqttConnectAware` reconnect re-announce of ADR-012's 2026-06-26 amendment runs. The declared dependency `aiomqtt>=2.5.0` (2.5.1 locked, paho-mqtt 2.1.0 backend) already exposes `protocol=ProtocolVersion.V5`, `publish(..., properties=)` and `Will.properties`; nothing is wired up. `aiomqtt` 3.0.0-alpha.1 (2026-04-03) replaces the backend, drops 3.1.1 and removes `protocol=` — a separate, larger migration this decision must not depend on, and one the open-ended `>=2.5.0` specifier would let a fresh install pick up the day 3.0.0 ships. The protocol level is not negotiated: the client names one level in its CONNECT packet and the broker either serves it or refuses the connection, so an MQTT 5 client needs a broker that speaks MQTT 5 — every mosquitto since 1.6 serves 3.1.1 and 5 clients side by side, and openHAB's MQTT broker thing has an `mqttVersion` toggle — and nothing outside cosalette needs to change for adoption to be per app.

A spike against `eclipse-mosquitto` via testcontainers (2026-09-13, aiomqtt 2.5.1 in v5 mode) confirmed the mechanism end to end: a retained publish with `MessageExpiryInterval=2` is gone for a fresh subscriber after the window, a republish inside the window extends it, a 3.1.1 subscriber receives v5-published retained messages unchanged, and a last-will message carrying the property also expires.

The proposal's sketch assumes 'a healthy app keeps refreshing its retained topics inside their expiry window as part of its normal publish cycle' and therefore proposes an expiry derived from the poll interval (3-5x). That assumption does not hold for most framework-owned retained topics today: per-entity `availability` is published on transitions only (`HealthReporter`, ADR-077 deduplication), telemetry `state` under an `OnChange` publish strategy is suppressed while the value is unchanged, Home Assistant discovery config is published on the first connect only (`register_connect_reannounce` deliberately skips it on reconnects), `schema/status` once at startup, `_meta/registry` and `_meta/state_model_drift` on every (re)connect, and user `ctx.publish(..., retain=True)` has no cycle at all. Only the `{prefix}/status` heartbeat and non-deduplicated telemetry state are refreshed periodically. Expiry without a refresh mechanism would silently drop legitimate retained topics of a perfectly healthy app.

## Decision

Add opt-in MQTT 5 support to `MqttClient` and, when it is selected, stamp every retained publish and the last-will message with a `Message Expiry Interval`, because a broker-enforced TTL turns retained-topic cleanup from 'the app must remember everything it ever published, forever, across every restart' into 'the broker drops any retained claim this process stops refreshing'. Expiry is uniform for every retained topic the process owns (state, availability, status, discovery config, `_meta`, `schema`, user retained publishes) with no per-call-site change and no change to `MqttPort`, `MockMqttClient` or `NullMqttClient`; a renamed or removed entity's topics simply stop being refreshed and age out on their own.

**Sub-decision: retained ledger.** To make expiry safe for topics the framework only publishes on transitions or on connect, `MqttClient` owns a **retained ledger**: while expiry is active it records, for every `publish(retain=True)`, the last wire payload, QoS and publish instant per topic, and republishes the ledger periodically and after every (re)connect. Under 3.1.1 nothing is recorded — the adapter carries no ledger at all. The ledger lives in the `MqttClient` instance only: it is discarded with the process, `stop()` cancels the refresh task and clears it, and a fresh instance starts empty and repopulates through the app's own first-connect announces. A retained topic therefore stays alive only if the running process publishes it at least once per lifetime — framework topics do (discovery, `_meta`, availability and the heartbeat on connect; `OnChange` telemetry publishes its first value because the deduplication memory starts empty), but event-driven retained state of the stream archetype and one-shot user `ctx.publish(..., retain=True)` issued before a restart age out one window after it unless the app republishes them on start. Retained payloads are treated as non-sensitive broker state: the ledger holds in memory exactly what the broker already retains. Its size is the number of retained topics the process owns and is unbounded by design; the adapter logs a one-time warning when it exceeds 1000 entries, which points at a dynamic topic scheme rather than a bug.

**Sub-decision: settings and defaults.** `MqttSettings.protocol_version: Literal["3.1.1", "5"] = "3.1.1"` (`MQTT__PROTOCOL_VERSION`) keeps every existing deployment byte-identical — the `aiomqtt.Client` kwargs are untouched unless `"5"` is selected; a `mode="before"` validator coerces the integer `5` that a TOML/JSON config file naturally produces to `"5"`. `MqttSettings.message_expiry_interval: int = 86400` seconds (`MQTT__MESSAGE_EXPIRY_INTERVAL`) keeps the MQTT property's name, and its field description states the scope: applied to retained publishes and the last will only, under MQTT 5 only. It is bounded `3 <= value <= 4_294_967_295`: the upper bound is the MQTT 5 four-byte limit, rejected at validation time rather than at packet packing; the lower bound keeps the refresh period at one second or more so a QoS 1 round trip fits inside it, and is deliberately permissive so integration tests can use seconds-long windows — production values are hours or days. It is inert under 3.1.1, so that `MQTT__PROTOCOL_VERSION=5` alone turns the safety net on, and there is no 'MQTT 5 without expiry' mode: the framework selects MQTT 5 for nothing else, and an operator who wants a longer window sets a larger interval (a very large value is a legitimate 'effectively never' and draws no warning). Setting `message_expiry_interval` explicitly while `protocol_version` is `"3.1.1"` is a validation error — an explicit `86400` included — detected via `"message_expiry_interval" in self.model_fields_set`; the TLS validator's `is not None` test does not transfer because this field's default is a value, but the rule is the same 'would be silently ignored' rule `_validate_tls_settings` applies to the TLS file settings. `validate_assignment=True` re-runs the model validator on attribute assignment, so the rule holds for init kwargs, env, config file and assignment alike. There is no protocol fallback: a v5 CONNECT that a 3.1.1-only broker refuses is an ordinary connection failure that goes through the reconnect backoff, and the first such failure logs a one-time hint naming `MQTT__PROTOCOL_VERSION`, mirroring the TLS-mismatch hint. The default is a fixed constant rather than a multiple of the poll interval: with the ledger refresh the only thing expiry has to exceed is the downtime after which stale retained values should stop being presented as current, and 24 h bounds an orphan's lifetime to a day while surviving ordinary deploys, broker restarts and overnight outages.

**Sub-decision: refresh mechanics.** The refresh task is created in `start()` alongside `_listen_task`, cancelled and awaited in `stop()`, and survives any failure of an individual pass. Its period is `expiry / 3`, measured from tick to tick on a fixed cadence rather than from the end of a pass: after a successful pass the next two ticks both fall inside the window, so a single failed or missed pass (a disconnect at tick time, a publish error) never lets a topic expire, and a topic expires only when the app cannot reach the broker for more than two thirds of the window. A pass publishes the ledger sequentially, one message in flight, through an internal raw publish path that bypasses the ledger bookkeeping (no re-serialisation, no ledger write, no per-topic debug log); a pass that overruns its period logs a warning, a publish failure aborts the pass with a warning and the next tick retries, and while the client is disconnected the pass is skipped. After a (re)connect the ADR-012 connect callbacks run first, each under its own exception guard as today, and then — as a further guarded step of `_run_connect_callbacks` — the ledger is refreshed for the entries whose publish instant predates the connect instant, so the framework's own reannounce publishes (fresher values such as `online` for a recovered device) are never republished twice and always win the ordering. The ledger stores the payload exactly as handed to the broker — the JSON string for a dict payload, never the caller's mutable object — and is written in the same synchronous step as the enqueue, before the first `await`, so ledger order equals wire order: a `publish()` that raises before the enqueue (client not connected) records nothing, and a publish that fails after it (`MqttError`, timeout) stays recorded and is re-asserted by the next pass. Stale-refresh safety rests on the same invariant: no `await` between a ledger read or write and paho's enqueue. `aiomqtt.Client.publish` hands the packet to paho before its first suspension as long as `max_concurrent_outgoing_calls` is left unset — its semaphore would insert one — and `MqttClient` never sets it; this is a property of aiomqtt 2.x internals, so a code comment in `_client.py` names it and a unit test pins the ordering (an application publish issued during a refresh pass is the last packet enqueued for its topic). The ledger mirrors the broker's retained slot: an empty retained payload — the clear convention of ADR-031 and ADR-048 — removes the entry, and a non-retained publish on the same topic leaves the entry untouched.

**Sub-decision: last will.** The ADR-012 `WillConfig` translation gains the same expiry property on the `WILLMESSAGE` packet, so the broker-published retained `offline` on `{prefix}/status` ages out as well; a decommissioned app leaves no permanent footprint. The will's interval starts when the broker publishes it, so it needs no refresh.

**Sub-decision: dependency bound.** The feature is built on the aiomqtt 2.x API, so the declared dependency becomes `aiomqtt>=2.5.0,<3` (changed in `pyproject.toml` together with this record). The cap is lifted by the separate aiomqtt 3 migration, which has to revisit `protocol=` and the 3.1.1 default anyway; Renovate surfaces the major as its own PR rather than pulling it into the grouped minor/patch update.

**Sub-decision: existing orphans and out-of-band clears.** Expiry covers only messages published after an app opts in. On the first refresh after opting in, every topic the app still owns is overwritten by an expiring copy; retained messages left behind under 3.1.1 that the app no longer publishes — the two reported incidents included — carry no expiry and need the one-off manual clear (`mosquitto_pub -r -n`) or an ADR-048 store with memory of them. From then on, an orphan can outlive its app by at most one window. The ledger also re-asserts what it holds: a topic an operator clears out of band while the app runs (`mosquitto_pub -r -n`, Home Assistant 'Delete device') comes back at the next refresh. The sanctioned clears are an empty retained publish from the app, which pops the ledger entry, or stopping the app and letting the window elapse.

**Sub-decision: answers to the proposal's open questions.** (1) Should the expiry be a multiple (3-5x) of the poll interval? No: the ledger refresh decouples expiry from poll cadence entirely. (2) Should Home Assistant discovery config get a separate, longer expiry than state and availability? No: while the app runs every retained topic is refreshed alike, so only downtime matters and one knob covers it; a per-kind expiry can be added later without a breaking change if a fleet needs it. (3) Does the ADR-048 store-based cleanup stay? Yes: it clears a removed entity on the very next start, whereas expiry needs up to a full window, so the store remains the prompt cleanup path and expiry is the safety net for every case the store cannot cover.

**Sub-decision: verification.** Unit tests cover the settings decision table (protocol {3.1.1, 5} x expiry {default, explicit default, explicit other} x source {init kwargs, env, config file, assignment}) with boundary values (`"null"`, `""`, `0`, `2`, `3`, `2**32 - 1`, `2**32`, integer `5` for the protocol), unchanged `aiomqtt.Client` kwargs and publish calls under 3.1.1 (`properties=` is passed only for retained publishes with expiry active, and `_expiry_active` derives from settings, not from connection-loop state, so the existing `AsyncMock` inner-client pattern applies), ledger add/overwrite/clear, a dict mutated after publish, both failure paths (nothing recorded before the enqueue, recorded after it), a non-retained publish leaving the entry untouched, the enqueue-ordering test, a fake-clock (ADR-071) refresh test that skips one tick and asserts every entry is republished inside its window, and the reconnect refresh touching only pre-connect entries. Integration tests on the existing `MosquittoContainer` fixtures reproduce the spike: expiry drop, ledger keep-alive, empty clear stops the refresh, will expiry, a 3.1.1 subscriber, the renamed-device incident shape, and the restart shape (a fresh instance lets a one-shot topic of the old process age out while connect-republished topics survive).

```python
# packages/src/cosalette/_settings/__init__.py
class MqttSettings(BaseModel):
    ...
    protocol_version: Literal["3.1.1", "5"] = "3.1.1"          # MQTT__PROTOCOL_VERSION
    message_expiry_interval: Annotated[int, Field(ge=3, le=4_294_967_295)] = 86400
    # seconds; retained publishes and the last will only; MQTT 5 only —
    # explicit under 3.1.1 ("message_expiry_interval" in model_fields_set) is a ValueError

# Opt in per app — nothing else changes for the broker or other clients:
#   MQTT__PROTOCOL_VERSION=5
# Optional: MQTT__MESSAGE_EXPIRY_INTERVAL=172800

# packages/src/cosalette/_mqtt/_client.py (sketch)
client_kwargs = {...}
if self.settings.protocol_version == "5":
    client_kwargs["protocol"] = aiomqtt.ProtocolVersion.V5

@property
def _expiry_active(self) -> bool:            # settings-derived, not connection state
    return self.settings.protocol_version == "5"

async def publish(self, topic, payload, *, retain=False, qos=1):
    if self._client is None:
        raise RuntimeError("MqttClient is not connected")   # records nothing
    if isinstance(payload, dict):
        payload = dumps(payload)                # ledger holds the wire payload
    if retain and self._expiry_active:
        if payload == "":                       # clear convention (ADR-031/048): forget
            self._retained.pop(topic, None)
        else:
            self._retained[topic] = _Entry(payload, qos, self._clock.now())
    # Invariant: no await between the ledger write and paho's enqueue inside
    # aiomqtt 2.x publish (max_concurrent_outgoing_calls is never set),
    # so ledger order == wire order.
    await self._publish_raw(topic, payload, retain=retain, qos=qos)

async def _publish_raw(self, topic, payload, *, retain, qos):
    if retain and self._expiry_active:
        await self._client.publish(topic, payload, retain=retain, qos=qos,
                                   properties=self._publish_properties)
    else:                                       # 3.1.1 call stays byte-identical
        await self._client.publish(topic, payload, retain=retain, qos=qos)

async def _refresh_retained(self, *, before=None):   # every expiry/3, and after reconnect
    for topic in list(self._retained):
        entry = self._retained.get(topic)       # read at publish time, not a snapshot
        if entry is None or (before is not None and entry.published_at >= before):
            continue
        await self._publish_raw(topic, entry.payload, retain=True, qos=entry.qos)

async def _run_connect_callbacks(self):
    connected_at = self._clock.now()
    steps = (*self._on_connect_callbacks,
             partial(self._refresh_retained, before=connected_at))
    for step in steps:
        try:
            await step()
        except Exception:
            logger.exception("MQTT connect step failed")
```

## Decision Drivers

- Cleanup must not depend on the app having an unbroken, persisted memory of everything it ever published — that dependency is what failed in both incidents and is a recorded negative consequence of ADR-048
- Legitimately unchanged retained topics of a running app (transition-only availability, OnChange-suppressed state, first-connect-only discovery and connect-only _meta snapshots, user retained publishes) must never expire while the process that published them runs
- Opt-in and backward compatible: the default protocol stays 3.1.1 with byte-identical aiomqtt kwargs and publish calls, and 3.1.1 subscribers (Home Assistant, openHAB, dashboards) keep receiving v5-published retained messages unchanged
- Locality: one adapter and one settings model change, no MqttPort / MockMqttClient / NullMqttClient / call-site changes, no new dependency
- Build on the aiomqtt 2.x API where protocol= and properties= already exist, and bound the dependency to it (`<3`); do not design around aiomqtt 3.0 (alpha, drops 3.1.1), which is a separate future migration
- Operator burden of a single env var per app; broker and consumers need no reconfiguration beyond what they already support

## Considered Options

### Option 1: MQTT 5 expiry on every retained publish plus a client-owned refresh ledger (chosen)

Opt-in `protocol_version="5"`; `MqttClient` stamps every retained publish and the will with `Message Expiry Interval`, remembers the last retained wire payload per topic in process memory, republishes the ledger every `expiry/3` on a fixed cadence and after the connect callbacks on every (re)connect, and forgets a topic on an empty retained publish.

- *Advantages:* Broker-enforced TTL: orphans created after opting in age out even with store=None, a wiped store, or an app that never runs again — both reported incident shapes are covered from then on with no STORE_PATH configuration; Uniform for every retained topic the process owns, including transition-only availability, OnChange-suppressed state, first-connect-only discovery, connect-only _meta snapshots and user retained publishes; Fixed default expiry independent of poll cadence — no per-app tuning, no multi-interval apps to reason about; Confined to MqttClient and MqttSettings; MqttPort, the test doubles and every call site are untouched; Verified end to end against a real mosquitto before adoption, and the same testcontainers scenarios become the integration tests
- *Disadvantages:* Adds a background refresh task and a per-topic ledger (bounded by the number of retained topics) to the production adapter, and every pass is a burst of one QoS 1 publish per retained topic delivered to every live subscriber; An app down longer than the expiry loses its retained footprint until it reconnects and republishes; after a Home Assistant restart in that window the entities stay absent until the app returns; Orphans linger for up to one full expiry window (24 h by default) rather than being cleared at the next start, and orphans that predate the opt-in are not covered at all; Requires MQTT 5 on the broker (mosquitto >= 1.6) — 3.1.1-only brokers refuse the connection

### Option 2: Expiry only on state, availability and discovery, scaled 3-5x off the poll interval (the proposal's sketch)

Wire up MQTT 5 and put an expiry derived from the app's telemetry interval on state/availability/discovery publishes only, relying on the normal publish cycle to refresh them.

- *Advantages:* Smallest code change — no refresh loop, no ledger; Expiry naturally tracks the app's cadence for non-deduplicated telemetry state
- *Disadvantages:* Availability is published on transitions only, so a device online for weeks would have its availability expire and appear unavailable; OnChange-suppressed state (a contact closed for weeks) and first-connect-only discovery / connect-only _meta and schema snapshots are never refreshed and would expire on a healthy app; Apps with several telemetry intervals, stream and device archetypes or user retained publishes have no single poll interval to scale from; Would need per-call-site knowledge of which retained publishes carry expiry, spreading the concern over health, wiring, context and discovery modules

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

- From the moment an app opts in, both reported incident shapes (removed entity, renamed device) are covered by the broker with no STORE_PATH configuration: orphaned state, availability and discovery topics expire within one window
- Every retained topic the running process owns is refreshed alike, with headroom for one missed pass, so enabling expiry never makes a healthy app's availability, deduplicated state or discovery config disappear
- Fully opt-in; the 3.1.1 path is byte-identical in client kwargs and publish calls, 3.1.1 subscribers keep receiving v5-published retained messages unchanged, and adoption is one env var per app on any mosquitto >= 1.6
- ADR-048's store-based cleanup keeps its prompt-cleanup role; the two layers compose rather than compete
- No new dependency and no coupling to aiomqtt 3.0 — the feature sits on API that has been in the 2.x line for years, and the `<3` bound keeps a fresh install on it

### Negative

- A new background task and per-topic ledger in MqttClient, a settings validator that rejects an explicit message_expiry_interval under 3.1.1, and a stale-refresh invariant (no await between ledger access and paho enqueue, max_concurrent_outgoing_calls never set) that future MqttClient changes must preserve
- Refresh traffic is visible to every live subscriber: every retained topic the process owns is re-emitted every expiry/3 (8 h by default) and once after every reconnect, so OnChange telemetry and user retained topics produce periodic duplicate events — Home Assistant last_updated, MQTT-trigger automations and openHAB rules keyed on message arrival fire on unchanged data (the same shape ADR-012's amendment records for the reconnect burst), and the cost of a pass grows with the number of retained topics divided by the expiry
- An app offline for longer than the expiry loses its retained footprint until it reconnects; if Home Assistant restarts in that window its MQTT entities stay absent until the app republishes discovery on connect
- The ledger is process memory: retained state that the new process never republishes — event-driven stream state, one-shot user retained publishes — ages out one window after a restart
- Orphans linger for up to a full expiry window (24 h by default) instead of being cleared at the next start — apps that want both keep a persisted store for ADR-048 — and orphans left behind before opting in still need a one-off manual clear
- The refresh re-asserts what the ledger holds, so a retained topic cleared out of band while the app runs comes back; clears go through the app (empty retained publish) or by stopping it
- Two settings whose meaning depends on each other: message_expiry_interval is inert under the default protocol and must be documented as MQTT 5 only, on retained publishes and the will only, in docs/reference/settings.md, docs/guides/configuration.md, docs/guides/deployment.md and the _ai_content help (cos-i950.3, cos-i950.4); the implementation is cos-i950.2 and this record gets an editorial amendment when it ships, as ADR-048 did
- 3.1.1-only brokers gain nothing from this decision, and pointing an MQTT 5 client at one fails to connect rather than falling back
- The `aiomqtt<3` bound must be lifted deliberately by the aiomqtt 3 migration; until then Renovate cannot propose the major

## Amendment (2026-09-14) — Corrective

**Rationale:** PR #458 review found that warning after more than 1,000 entries did not bound the in-memory retained ledger. The implementation now enforces a fixed maximum before accepting a new distinct retained topic.

> **Justification for amendment (not supersession):** ADR-078 has not shipped: the change is confined to its unreleased implementation in MqttClient and introduces no migration for deployed applications, so supersession would add record churn without clarifying a downstream compatibility boundary.

!!! note "Editorial note (2026-09-14)"
    The retained ledger is capped at 1,000 distinct topics. At capacity, a retained publish to a new topic raises RuntimeError before it is sent; updating an existing topic and clearing one with an empty retained payload remain allowed. This replaces the original unbounded-memory design and one-time warning.

!!! note "Editorial note (2026-09-14)"
    Protocol version and message expiry interval are captured when MqttClient.start() begins a lifecycle. Later MqttSettings assignment is valid but takes effect only on the next start, ensuring a running MQTT 3.1.1 connection never receives MQTT 5 properties.

### Additional Positive Consequences

- The ledger has a deterministic per-client memory bound and the active wire protocol cannot drift from a live connection's CONNECT packet.

### Additional Negative Consequences

- Applications using dynamic retained-topic schemes must clear old topics or redesign their topic cardinality before exceeding 1,000 entries.

## Amendment (2026-09-14) — Corrective

**Rationale:** Review of the initial implementation found that the published ledger policy lacked a byte bound and that refresh lifecycle details needed precision for slow passes and long-running connect callbacks.

> **Justification for amendment (not supersession):** ADR-078 has not yet been released and the correction is confined to MqttClient, tests, and operator guidance, so no downstream migration is required and supersession would add unnecessary record churn.

!!! note "Editorial note (2026-09-14)"
    The retained ledger rejects a publish that would exceed either 1,000 distinct topics or 16 MiB of combined UTF-8 topic and payload data. Replacing or clearing an existing retained topic remains permitted when it stays within the aggregate byte budget.

!!! note "Editorial note (2026-09-14)"
    Protocol version and message-expiry interval are captured at start for the active connection lifecycle. A setting mutation takes effect only after stop and start, including for WILLMESSAGE properties created on a reconnect.

!!! note "Editorial note (2026-09-14)"
    While connect callbacks are still running, periodic refresh republishes only entries older than that connection. This keeps pre-connect retained state alive without duplicating a callback's newer reannounce. An overlong refresh pass waits a full period before the next pass instead of spinning back-to-back.

!!! note "Editorial note (2026-09-14)"
    The current Mosquitto integration tests verify retained-message expiry, ledger refresh received by an MQTT 3.1.1 subscriber, and MQTT 5 will-property connection wiring. They do not simulate unclean-disconnect will delivery or historical entity-removal scenarios.

### Additional Positive Consequences

- Ledger memory and periodic broker traffic now have explicit per-client upper bounds, and a blocked connect callback cannot suppress expiry maintenance for state that predates the connection.

### Additional Negative Consequences

- Applications with more than 1,000 retained topics or 16 MiB of retained topic and payload data must clear obsolete state or use smaller retained payloads before publishing additional entries.
