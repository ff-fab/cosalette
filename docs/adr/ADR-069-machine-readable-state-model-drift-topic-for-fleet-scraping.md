---
status: Accepted
date: 2026-09-04
impact: moderate
tags: [mqtt, serialization, telemetry, error-handling, health]
---

# ADR-069: Machine-Readable state_model Drift Topic for Fleet Scraping

## Status

Accepted **Date:** 2026-09-04

## Context

ADR-068 made an explicit `state_model=` authoritative on `@app.telemetry` and `@app.command` (clause A) and fail-closed on serialisation (clause B). Its clause F added a registration-time signal for the remaining silent contradiction — a handler that declares `state_model=M` but is annotated `-> N` with `M != N`. That signal is a Python `warnings.warn(...)` emitted once per registration at startup (`_registration/_shared.py:239-247`, called from `_router/_telemetry.py:216`, `_app/_telemetry.py:452,674`, `_app/_command.py:57`).

A once-per-boot warning is the right thing for a developer sitting at a terminal and the wrong thing for a fleet. cosalette apps are unattended daemons on many nodes; answering "which of my 40 devices ship a handler whose declared contract disagrees with its code?" means SSH-ing into each node and grepping journald. ADR-068 recorded this gap explicitly as the final bullet of its negative consequences: *"A machine-readable `state_model` drift topic (e.g. `{prefix}/_meta/state_model_drift`) for fleet-wide scraping is NOT part of this decision and is deferred to a follow-up ADR."* This is that ADR.

The fact is already computed — clause F does the comparison at registration and has both type labels in hand via `_annotation_label` (`_runners/_contracts.py:175-182`). What is missing is a transport. Everything the framework wants an operator to see over the wire already has one, and the conventions are settled: ADR-002 splits **retained last-known state** (`state`, `availability`, `status`) from **transient events** (`error` — *"errors are events, not last-known state"*); every framework publish uses QoS 1 (`_errors.py:288`, `_wiring/_infra.py:317`, `_schema/_validator.py:366`); and `{prefix}/_meta/registry` already establishes a reserved, always-on, retained introspection namespace carrying the app's full AsyncAPI document, republished on every connect from an immutable cached string (`_wiring/_infra.py:273-318`, `:373`, `:416`).

Fleet scraping is also not a hypothetical consumer shape in this project: `NetworkComplianceMonitor` (`_schema/_monitor.py:63-90`, `:185-186`) already subscribes `+/schema/status` and `+/status` across a fleet and reports per-app compliance, deriving the app name from the topic rather than from a payload field. A drift topic that follows the same shape drops straight into that tooling.

The scope of the drift being reported is narrow and static. Clause F applies only where a handler *returns* published state — `@app.telemetry` and `@app.command`; `@app.device` and `@app.stream` validate `ctx.publish_state()` payloads through `validate_state_payload` and have no return contract to contradict. The registration set is fixed once app setup completes, so the drift set is immutable for the process lifetime. Runtime failures are a different fact with a different lifetime, and ADR-068 clause B already gave them a home: a `ReturnValidationError` is published to `{prefix}/{name}/error` with field-level detail built only from framework-owned data (`_safe_error_summary`, `_runners/_contracts.py:159-172`) and the state publish suppressed.

## Decision

Publish declaration drift as a **single retained JSON snapshot** on `{prefix}/_meta/state_model_drift`, at QoS 1, always-on, alongside the existing `_meta/registry` snapshot — a machine-readable second rendering of the ADR-068 clause F fact, with no behaviour change to publishing and no new configuration surface. Eight clauses:

**A. Topic.** `{prefix}/_meta/state_model_drift` — one topic per app, in the reserved `_meta/` namespace that `{prefix}/_meta/registry` (`_wiring/_infra.py:296`) already established for framework introspection metadata, and which ADR-048 retained cleanup already refuses to touch (`_wiring/_retained_cleanup.py:17`). Not a per-entity topic: drift is a property of the app's registration set, and per-entity topics are the Home-Assistant-facing entity namespace (ADR-002). A fleet scrape is one subscription — `+/_meta/state_model_drift`.

**B. Retained, QoS 1, published unconditionally.** Declaration drift is last-known state, not an event, so ADR-002's retention rule puts it on the retained side: a monitor that connects an hour after boot must still see it. A clean app publishes a `drift_count: 0` snapshot rather than nothing, so absence of a retained message means "this app has never run a version that publishes drift" instead of being ambiguous with "no drift". QoS 1 matches every other framework publish. The snapshot is computed once after setup and cached as a serialised string, mirroring `_asyncapi_broker_cache` (`_wiring/_infra.py:300-315`), so reconnect republishes are byte-identical.

**C. Payload schema.** A `schema_version`-stamped envelope (key name as in the ADR-048 snapshot, `_wiring/_retained_cleanup.py:117`) over a list of records. Each record carries `handler` (resolved registration name), `archetype` (`"telemetry"` or `"command"`), `kind` (a discriminator; `"annotation_conflict"` is the only value in this decision), `declared_model` and `effective_annotation` — the last two rendered by `_annotation_label` (`_runners/_contracts.py:175-182`), so the wire labels are exactly the strings clause F's warning text already prints.

**Deliberately absent**, each of which was in scope for this decision: no `app` field (the topic prefix carries it, as `schema/status` already does — `_schema/_monitor.py:143`); no timestamp (the document is static for the process lifetime, and a timestamp would make every reconnect republish a byte-different retained message; liveness comes from `{prefix}/status`); no version field (app/framework version disclosure is gated by F-DP6 on `status` and `_meta/registry`, and a third disclosure site would have to re-implement that gate); no `severity` (with one `kind` it would be a constant, and a consumer derives severity from `kind`); no `fields` / offending field paths (see clause E — the only drift kind in scope is a whole-type disagreement with no field-level detail to report, and runtime field paths already have a home).

**D. Publication point.** Next to `publish_registry_snapshot` in both wiring paths — the connect-aware reannounce callback (`_wiring/_infra.py:373`) and `publish_startup_snapshot` for non-connect-aware adapters (`_wiring/_infra.py:416`) — with the same fire-and-forget `try` / `except: logger.exception(...)` shape. Retained messages are lost when a broker restarts, so republishing on every connect is what keeps the fleet view accurate.

**E. Relationship to `{prefix}/{name}/error`: a separate topic with disjoint scope.** The drift topic carries *declaration* drift (static, registration-time); a runtime `ReturnValidationError` stays on `{prefix}/{name}/error` exactly as ADR-068 clause B specifies. Neither fact is published twice. Four reasons the error topic cannot absorb this: **lifetime** — errors are deliberately not retained (ADR-002, `_errors.py:288`), and a non-retained drift record is invisible to precisely the operator this decision exists for; **scope** — the per-entity error topic needs a device name and is skipped for root devices, where it would collapse onto the global topic (`_errors.py:264`), while drift is an app-level fact; **disclosure policy** — error payloads are governed by ADR-061 / F-DP1 (`disclose_messages_for`, `error_publish_verbose`), and a record built purely from framework-owned identifiers must not inherit those switches; **consumer contract** — `ErrorPayload` is a frozen fixed shape (`_errors.py:77-95`) and `+/error` / `+/+/error` are ACL'd and consumed as an event stream (`_schema/_acl.py:127-130`), so injecting a boot-time static fact there would degrade an existing contract.

**F. Always-on; no new setting.** No `App(...)` keyword, no `MqttSettings` field. The record's content — handler names and type labels — is a strict subset of what the always-on `_meta/registry` snapshot already publishes (channel addresses, payload schemas, operation metadata; `_wiring/_infra.py:281-294`), so an opt-in gate would protect nothing that is not already exposed while defeating the purpose: a per-device opt-in is not a fleet-wide scrape. This also follows ADR-068 clause E's reasoning that a permanent flag is disproportionate surface. Deployments that must not expose introspection metadata use the existing `_meta/#` broker-ACL guidance (`_wiring/_infra.py:291-294`), which now covers both `_meta` topics.

**G. Framework-owned, not an app channel.** Like `status`, `error`, `schema/status` and `_meta/registry`, the topic is not emitted as an AsyncAPI channel and produces no Home Assistant discovery entity (`_schema/_consumer_gen.py` builds entities from schema channels only). Implementation therefore adds it to `build_skip_topics` (`_schema/_validator.py:326-338`), to the per-app principal's publish list and the `monitor` principal's subscribe list (`_schema/_acl.py:55-65`, `:126-131`).

**H. Additive; implementation deferred.** Clause F's registration warning stays exactly as it is — the topic is a second rendering of the same fact, not a replacement, so a developer at a terminal keeps the immediate signal. Nothing on the publish hot path changes and no existing payload changes, so this ships as a `feat:` on the 0.9.x line (per `release-please-config.json`, a `feat:` bumps the patch version pre-1.0 — `_ai_content/_meta.py:43-47`). This ADR decides only the contract; the implementation, its tests and the companion surfaces (`cosalette ai help` topic text, a `VERSION_FEATURES` entry, the guidance asset) are cos-kg2u.

```json
// topic: wallpanel/_meta/state_model_drift   (retained, QoS 1)
{
  "schema_version": 1,
  "drift_count": 1,
  "entries": [
    {
      "handler": "brightness",
      "archetype": "telemetry",
      "kind": "annotation_conflict",
      "declared_model": "Reading",
      "effective_annotation": "dict[str, object]"
    }
  ]
}

// a clean app still publishes, so "no drift" is distinguishable from "never ran"
{ "schema_version": 1, "drift_count": 0, "entries": [] }

// fleet scrape — one subscription, whole fleet, no polling, no SSH:
//   mosquitto_sub -v -t '+/_meta/state_model_drift'
```

## Decision Drivers

- ADR-068 deferred exactly this topic to a follow-up ADR and left a once-per-boot `warnings.warn` as the only signal (`_registration/_shared.py:239-247`); an unattended fleet cannot be scraped by grepping journald on every node.
- The fact is already computed at registration by ADR-068 clause F and both type labels are already in hand via `_annotation_label` — only a transport is missing, so the cheapest correct answer is a publish, not a new detection mechanism.
- ADR-002 draws the retained/transient line by lifetime: `state`, `availability` and `status` are retained last-known state; `error` is deliberately transient because *"errors are events, not last-known state"*. Declaration drift is state.
- `{prefix}/_meta/registry` already establishes the reserved namespace, the retained + QoS 1 + always-on posture, the republish-on-connect lifecycle and the immutable-cached-payload pattern (`_wiring/_infra.py:273-318`); a parallel style would be a regression.
- Fleet scraping is an existing first-class consumer shape here — `NetworkComplianceMonitor` (`_schema/_monitor.py:63-90`) already reports per-app compliance from `+/schema/status` and `+/status`, deriving the app from the topic.
- The change must be additive: no behaviour change to publishing, no cost on the publish hot path (ADR-013 / ADR-021), and no new permanent configuration surface (ADR-068 clause E).

## Considered Options

### Option 1: Retained _meta/state_model_drift snapshot, always-on (chosen)

Clauses A-H above: one retained, QoS 1, always-published JSON snapshot per app on `{prefix}/_meta/state_model_drift`, computed once after setup, cached as a serialised string and republished on every connect alongside `_meta/registry`. Records carry handler, archetype, kind, declared model and effective annotation. Runtime `ReturnValidationError` stays on `{prefix}/{name}/error`.

- *Advantages:* A monitor that connects at any time — hours after boot, after its own restart — immediately sees the current drift state of every app on the broker from one subscription; this is the property the whole decision exists for; Reuses the `_meta/registry` posture wholesale (reserved namespace, retained, QoS 1, always-on, republish-on-connect, cached immutable payload), so there is no new topic style to learn, ACL, or maintain; Zero hot-path cost: the snapshot is derived from the registration set after setup and never recomputed, so ADR-013 / ADR-021 publish-path budgets are untouched; Records are actionable on their own — handler, archetype, declared model and effective annotation are enough to open the right file and fix it without SSH-ing to the device; Payload is a strict subset of what `_meta/registry` already discloses, so it needs no new disclosure gate and no new opt-in; the existing `_meta/#` ACL guidance covers it; A `drift_count: 0` snapshot makes 'clean' positively observable, so a fleet report can distinguish a healthy app from one that has not been upgraded yet
- *Disadvantages:* A new framework-owned wire contract that must be kept in step in four places — publish path, `build_skip_topics`, both ACL principals, and the `ai help` companion text; Covers declaration drift only; a handler whose declaration is self-consistent but whose payloads fail at runtime does not appear here, and the operator must still correlate with `{prefix}/{name}/error`; One more always-on retained message per app on the broker, and one more topic to include in `_meta/#` ACL rules on shared brokers; The retained snapshot is only as fresh as the last connect; a code change is not visible until the app restarts (acceptable — the fact it reports is static per process, but it does mean the topic reflects the running build, not the deployed source)

### Option 2: Reuse {prefix}/{name}/error with a state_model_drift error_type

Publish drift as an ordinary structured error at startup: a `state_model_drift` entry in the `error_type_map`, the declared/effective labels carried in `ErrorPayload.details`, published through the existing `ErrorPublisher` to `{prefix}/error` and `{prefix}/{name}/error`.

- *Advantages:* No new topic, no ACL change, no skip-list change — the publisher, payload shape and monitor subscriptions all exist already; Operators already watch `+/error` and `+/+/error`, so the record lands in tooling that is deployed today; `ErrorPayload.details` is an open `dict[str, object]`, so the drift fields fit without changing the dataclass
- *Disadvantages:* Error topics are deliberately not retained (ADR-002, `_errors.py:288`), so a boot-time record is gone by the time a fleet scraper connects — it fails the one requirement the decision exists to satisfy; Mixes a static, boot-time configuration fact into a stream defined as transient events, degrading the meaning of `+/error` for every existing consumer; The per-device topic needs a device name and is skipped for root devices where it would duplicate the global topic (`_errors.py:264`), so app-level drift has no natural address; Couples the record to ADR-061 / F-DP1 message-disclosure switches (`disclose_messages_for`, `error_publish_verbose`) that have nothing to do with framework-owned type labels; Drift is not an exception, so it would have to be reported through an exception-shaped API purely to reuse the transport

### Option 3: Extend {prefix}/schema/status with a drift count

Add `state_model_drift_count` (and possibly a nested list) to the existing retained `{prefix}/schema/status` payload published by `SchemaStatusPublisher` (`_schema/_validator.py:342-368`), and teach `NetworkComplianceMonitor` to surface it.

- *Advantages:* The topic is already retained, QoS 1, ACL'd for the `monitor` principal and actively scraped fleet-wide by `NetworkComplianceMonitor` — the consumer exists today; No new topic at all; one payload key and one monitor field; Puts every 'is this app well-formed?' signal in a single place an operator already watches
- *Disadvantages:* `{prefix}/schema/status` is only published when the ADR-033 schema pipeline is active (`_app/_helpers.py:62-64`), so apps that do not use schema enforcement — the common case — would report no drift, silently; Conflates two unrelated subsystems: MQTT schema enforcement (ADR-033) versus handler return contracts (ADR-046 / ADR-068); `violation_count` and a drift count answer different questions and would be read as one number; A bare count is not actionable — the operator still has to reach the device to learn which handler drifted; Changes the meaning of a payload existing consumers already parse, which is not an additive change

### Option 4: Transient per-event drift topic on every failed validation

Publish a non-retained record to `{prefix}/_meta/state_model_drift` each time a handler return value is rejected at runtime, carrying the offending field paths and Pydantic error-type codes from `_safe_error_summary`.

- *Advantages:* Reports what actually happens in production, not just what the declarations say — catches a handler whose declaration is self-consistent but whose payloads are wrong; Carries field-level detail (`loc` path plus error-type code), the most precise signal available; Needs no startup snapshot machinery — it is a publish on an existing failure branch
- *Disadvantages:* Duplicates `{prefix}/{name}/error`, which ADR-068 clause B already publishes on exactly this branch — two topics for one fact, and they can disagree; Not retained, so a fleet scraper still sees nothing on connect; it must sit subscribed and wait for a failure to recur; Adds work to the rejection path on the publish hot path, against ADR-013 / ADR-021 budgets; Message volume is unbounded — a handler failing every interval floods the broker, requiring dedup/throttle plumbing of the kind ADR-068 rejected as disproportionate

### Option 5: Keep the clause F warning only and document a log-scraping recipe

Ship no wire surface. Document how to collect the clause F `UserWarning` across a fleet — journald/`logging` handler configuration, a grep pattern for the stable warning string, and a note to pin that string.

- *Advantages:* Zero code, zero broker surface, zero new contract to maintain or version; The warning already names the handler, both types and the ADR, so the information content is identical to the proposed payload; No disclosure question at all: nothing new reaches the broker
- *Disadvantages:* Directly restates the problem ADR-068 recorded as deferred work — the premise of this decision is that per-node log scraping across unattended devices is not workable; Requires log shipping infrastructure on every node, which cosalette does not provide and cannot assume in a home/edge deployment; Makes a stable warning *string* a de facto API, which is more brittle than a versioned JSON payload; Leaves 'no drift' unobservable — a node with no matching log line is indistinguishable from a node whose logs are not being collected

## Decision Matrix

| Criterion | Retained _meta/state_model_drift snapshot, always-on | Reuse {prefix}/{name}/error with a state_model_drift error_type | Extend {prefix}/schema/status with a drift count | Transient per-event drift topic on every failed validation | Keep the clause F warning only and document a log-scraping recipe |
| --- | --- | --- | --- | --- | --- |
| Fleet scrapability (a monitor connecting at any time sees current state) | 5 | 1 | 3 | 2 | 1 |
| Separation of concerns (retained state vs transient events, ADR-002 / ADR-011) | 5 | 1 | 2 | 2 | 4 |
| Hot-path cost (ADR-013 / ADR-021) | 5 | 4 | 5 | 2 | 5 |
| Actionability of the record (fixable without reaching the device) | 5 | 4 | 2 | 5 | 3 |
| Broker/wire surface and disclosure risk | 4 | 3 | 4 | 2 | 5 |
| Implementation and maintenance surface | 4 | 4 | 4 | 3 | 5 |
| Backwards compatibility (no change to existing payloads or behaviour) | 5 | 3 | 2 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The ADR-068 clause F fact becomes scrapeable fleet-wide: `mosquitto_sub -t '+/_meta/state_model_drift'` answers "which apps ship a handler whose declared `state_model=` disagrees with its return annotation?" for every app on the broker, with no polling, no per-node log shipping and no SSH.
- Closes the deferred item ADR-068 recorded in its negative consequences, so the state_model enforcement decision is complete rather than carrying an open observability gap.
- Follows the `_meta/registry` posture exactly — reserved namespace, retained, QoS 1, always-on, republished on connect from a cached immutable payload — so the framework keeps one topic style rather than growing a parallel one (ADR-002).
- Fully additive: no existing payload changes, no publish behaviour changes, no hot-path cost, and no new configuration knob to document or migrate (ADR-068 clause E's reasoning about disproportionate flag surface).
- `drift_count: 0` on a clean app makes contract health positively observable, so a fleet report can tell a healthy app from one that has not been upgraded — something neither log scraping nor an event stream can do.
- Slots into the existing fleet tooling shape: `NetworkComplianceMonitor` already derives the app from the topic and aggregates per-app status, so a drift column is a natural extension rather than new infrastructure.
- The record is built only from framework-owned identifiers — handler names and `_annotation_label` type labels — so it carries no user payload and needs no ADR-061 / F-DP1 disclosure gate.

### Negative

- **Open question for a future additive extension:** this decision scopes the topic to *declaration* drift and leaves *runtime* rejection on `{prefix}/{name}/error`. An operator asking "which devices are currently failing validation?" still has to sit subscribed to the error stream; a retained per-handler rollup (first seen, last seen, count, field paths) would answer it directly but costs mutable state, republish debouncing and hot-path work. The `kind` discriminator in clause C exists so such a record can be added without a `schema_version` bump. Recommendation is to keep the current scope until a downstream pilot reports the need; this is the one point in this ADR a maintainer may reasonably decide differently.
- A new framework-owned wire contract must be kept in step across four sites — the publish path (`_wiring/_infra.py:373`, `:416`), `build_skip_topics` (`_schema/_validator.py:326-338`), both ACL principals (`_schema/_acl.py:55-65`, `:126-131`) and the `cosalette ai help` companion text. A drift guard test that asserts the topic appears in all of them is required, in the spirit of the ADR-068 companion guards.
- One more always-on retained message per app reaches the broker, and shared-broker deployments must extend their `_meta/#` ACL rules to cover it. The payload is a strict subset of `_meta/registry`'s disclosure, so this widens no exposure, but it does add a second topic to the same reasoning.
- The snapshot reflects the running build, not the deployed source: a fixed declaration only disappears from the topic after the app restarts. For unattended daemons with long uptimes this can mean a stale-looking drift record between a fix and the next deployment.
- Drift for `@app.device` and `@app.stream` is out of scope by construction — clause F only fires where a handler returns published state — so the topic is not a complete "contract health" view of an app, and its name must not be read as one.
- Records name handler identifiers and model class names on the broker. On an unauthenticated shared broker this is minor reconnaissance value on top of what `_meta/registry` already exposes, and it is mitigated only by broker ACLs, not by anything the framework enforces.
- Implementation, tests and companion-surface updates (`ai help` topic text, `VERSION_FEATURES` entry, guidance asset) are deferred to cos-kg2u, so until that lands the decision is recorded but the gap ADR-068 identified remains open in shipped code.

_2026-09-04_
