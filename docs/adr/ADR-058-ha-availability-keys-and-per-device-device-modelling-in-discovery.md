---
status: Accepted
date: 2026-08-11
impact: moderate
tags: [mqtt, health, devices, documentation]
---

# ADR-058: HA Availability Keys and Per-Device Device Modelling in Discovery

## Status

Accepted **Date:** 2026-08-11

## Context

cos-eq7p (Proposal Findings 18 & 19, tracked under epic cos-v0sg — consumer generator overhaul) identifies two related gaps in `HaDiscoveryGenerator` (`_consumer_gen.py`, built up across ADR-050/056/057). Finding 18: grepping the installed package for `availability_topic`, `payload_available`, `payload_not_available`, and `availability_mode` returns zero occurrences, even though `HealthReporter` (`_health/_reporter.py`) already maintains exactly the retained topics HA wants — `{app}/{device}/availability` for named devices, `{app}/availability` for root/unnamed devices (ADR-012) — publishing `online`/`offline` at QoS 1, driven by `unavailable_on=`, `ctx.mark_unavailable()`, the MQTT LWT, and reconnect re-announcement. Generated discovery payloads carry none of this, so HA entities never go unavailable — a device offline for a week still shows its last retained reading as current. A sharper gap sits underneath: the MQTT LWT is registered only on `{app}/status` (`build_will_config`); on an unclean crash, per-device `{app}/{device}/availability` topics have no LWT of their own and stay retained at their last `online` value, so even a correctly-wired single-topic `availability_topic` would still show a crashed app's devices as available. Finding 19: `_build_payload()`/`_build_composite_payload()` hardcode `device: {identifiers: [f'cosalette_{node_id}']}` — one HA device per **app**, not per resolved device. Verified: wiz2mqtt's twelve entities across four physical bulbs all carry `identifiers: ['cosalette_wiz2mqtt']`; HA prefixes entity names with the device name, so velux2mqtt's blind and window both display as 'velux2mqtt Cover Position', indistinguishable in the UI. This also means Finding 18's device-level availability has no device of its own to attach to. `SchemaRegistry` already carries `app_version` and `device_names` (populated by `_extract_device_names`, which runs the same archetype/template-based resolution `_resolve_device` in `_consumer_gen.py` already uses), so the ingredients for a proper per-device block exist without new wire extensions. A constraint carried on cos-eq7p from the PR #377 review (an IDENTITY-BREAK NOTE): this is the designated place to break HA `unique_id` if a uniform scheme is ever wanted, but adding a device block does not itself require an identity break — HA re-points device grouping from retained state while preserving entity history — so `unique_id` must stay exactly as ADR-056 computed it.

## Decision

Extend `HaDiscoveryGenerator` — schema-side only, with no touch to `_app.py`, `_wiring`, or `_asyncapi.py` channel-dict assembly, preserving the ADR-057 precedent that consumer-generation extensions stay off the registration/decorator pipeline — with three additive pieces, applied to both the scalar (`_build_payload`) and composite (`_build_composite_payload`) entity paths. (1) Availability: a device is 'root' (no distinct device-name segment, mirroring `HealthReporter.is_root`) exactly when `_resolve_device(channel)` is not a member of `registry.device_names` — `_extract_device_names` runs the identical archetype/template resolution and only fails to produce a name for the short, root-shaped addresses that `_resolve_device`'s own fallback guesses at, so set membership is a free, exact-enough signal without threading a new `is_root` extension through the AsyncAPI pipeline. Root devices get the single-topic form (`availability_topic`, `payload_available: 'online'`, `payload_not_available: 'offline'`) mirroring `HealthReporter._availability_topic`'s root branch exactly. Named devices get the multi-topic form: an `availability` list combining the device's own `{app}/{device}/availability` topic with the app-level `{app}/status` heartbeat/LWT topic (the latter carrying a `value_template` that reads `value_json.status` for the JSON heartbeat and falls back to the raw `value` for the bare LWT string), plus `availability_mode: 'all'` — so an entity shows available only when both its own device and the app process are alive, closing the gap where an unclean crash leaves per-device availability topics stuck stale. (2) Per-device device blocks: named devices get their own `device` block — `identifiers: ['cosalette_<node_id>_<device_slug>']`, `name: <device_name>`, `via_device: 'cosalette_<node_id>'` — instead of sharing the app-level device; root devices keep attaching straight to the app-level identity (`identifiers: ['cosalette_<node_id>']`, no `via_device`), since for an unnamed device the app is the device. (3) Origin + bridge: every payload gains `origin: {name: <app>, sw_version: <registry.app_version>}` (free diagnostics HA surfaces natively). Any app with at least one named device additionally gets one synthetic diagnostic `binary_sensor` per app — `device_class: connectivity`, `entity_category: diagnostic`, `state_topic: '{app}/status'`, mapping the heartbeat/LWT payload to `ON`/`OFF` — whose own `device` block is the bare app-level identity. This is the entity that actually causes HA to create the bridge device: HA's device registry does not materialise a device purely from a `via_device` reference inside another entity's config, so without this synthetic entity every named-device `via_device` link would be permanently inert in a multi-device app — the primary case Finding 19 targets. `unique_id` computation is untouched on both paths; only `device`, `availability`/`availability_topic`, and `origin` keys are added or restructured.

```jsonc
# Before (velux2mqtt's blind — one HA device per app):
{
  "name": "Cover Position", "unique_id": "cosalette_velux2mqtt_blind_position",
  "state_topic": "velux2mqtt/blind/state", "value_template": "{{ value_json.position }}",
  "device": {"identifiers": ["cosalette_velux2mqtt"], "name": "velux2mqtt", "manufacturer": "cosalette"}
}

# After — same unique_id, per-device block + availability + origin:
{
  "name": "Cover Position", "unique_id": "cosalette_velux2mqtt_blind_position",
  "state_topic": "velux2mqtt/blind/state", "value_template": "{{ value_json.position }}",
  "availability": [
    {"topic": "velux2mqtt/blind/availability"},
    {"topic": "velux2mqtt/status",
     "value_template": "{{ value_json.status if value_json is mapping else value }}"}
  ],
  "availability_mode": "all",
  "payload_available": "online", "payload_not_available": "offline",
  "device": {
    "identifiers": ["cosalette_velux2mqtt_blind"], "name": "blind",
    "manufacturer": "cosalette", "via_device": "cosalette_velux2mqtt"
  },
  "origin": {"name": "velux2mqtt", "sw_version": "1.4.0"}
}

# New — one synthetic bridge entity per app with a named device:
{
  "name": "Bridge", "unique_id": "cosalette_velux2mqtt_bridge",
  "state_topic": "velux2mqtt/status",
  "value_template": "{{ 'ON' if (value_json.status if value_json is mapping else value) == 'online' else 'OFF' }}",
  "device_class": "connectivity", "entity_category": "diagnostic",
  "device": {"identifiers": ["cosalette_velux2mqtt"], "name": "velux2mqtt", "manufacturer": "cosalette"},
  "origin": {"name": "velux2mqtt", "sw_version": "1.4.0"}
}
```

## Decision Drivers

- ADR-012's HealthReporter already publishes exactly the retained availability topics and online/offline payloads HA wants; discovery output has never surfaced them, so HA-side entities never go unavailable even after a crash.
- Finding 19: one HA device per app collapses distinct physical devices (14 wiz2mqtt bulbs, velux2mqtt's blind+window) into a single flat entity list, breaking HA's per-device name prefixing and leaving Finding 18's device-level availability with no device to attach to.
- The PR #377 identity-break note requires this change to leave unique_id untouched; HA re-points device grouping from existing retained state without losing entity history, so the fix must be additive at the device/availability/origin keys only.
- ADR-057 established that x-cosalette-* consumer-generation extensions stay schema-side with zero touch to _app.py/_wiring/_asyncapi.py registration; reusing the existing SchemaRegistry.device_names signal instead of adding new wire extensions preserves that architecture.
- MQTT's LWT is registered only on {app}/status (ADR-012), not on per-device availability topics, so a naive single-topic availability_topic per device would still misreport availability after an unclean crash.

## Considered Options

### Option 1: Bridge diagnostic entity + device_names membership root check (chosen)

Detect root devices via existing SchemaRegistry.device_names membership (no new wire extension). Emit per-device device blocks with via_device, a multi-topic availability merge (device topic + app status topic, availability_mode: all) for named devices, a single availability_topic for root devices, an origin block on every payload, and one synthetic per-app diagnostic binary_sensor tied to {app}/status that establishes the bridge device in HA's registry so via_device actually resolves.

- *Advantages:* Zero changes to _app.py, _wiring, or _asyncapi.py — stays schema-side like every ADR-057 extension; Closes the real LWT gap: an unclean crash flips availability_mode: all to unavailable even though no per-device availability topic changed; The synthetic bridge entity makes via_device functional in HA's UI for every multi-device app, not just a hint that never renders; unique_id computation is untouched on both scalar and composite paths
- *Disadvantages:* Root detection is inferred from device_names membership rather than the runtime is_root flag itself — a 2-segment address that happens to coincide with a real device name elsewhere in the registry could misclassify (accepted as a vanishingly unlikely edge case); Adds one extra discovery payload (the bridge sensor) per app with named devices — a small, deliberate increase in discovery traffic; Existing HA installations see entities re-parented from the old single app-level device to new per-device devices; the old app-level device becomes empty in HA's registry until removed

### Option 2: Thread runtime is_root through the AsyncAPI pipeline

Add an x-cosalette-root boolean to the channel dict in _asyncapi.py (mirroring x-cosalette-app/x-cosalette-archetype), parse it back into ChannelSchema in _loader_helpers.py, and use that exact flag instead of inferring root-ness from device_names membership. Availability, per-device blocks, origin, and the bridge entity are built the same way as the chosen option.

- *Advantages:* Exact root detection sourced directly from the same is_root the router/health layers already use — no inference, no edge cases; Same availability/device/origin/bridge behaviour as the chosen option
- *Disadvantages:* Touches _app.py-adjacent registration output (_build_channel_dict/_build_channel_entry) and the loader, the exact registration/decorator-pipeline blast radius ADR-057 explicitly avoided for consumer-generation extensions; Every existing generated AsyncAPI schema gains a new field just to cover a root-detection case the device_names signal already handles correctly for every real topic shape in the codebase; Larger diff and larger schema-compatibility surface for a precision gain that has no known real-world failure case

### Option 3: Per-device blocks only — no bridge entity, single-topic availability

Emit per-device device blocks with via_device and origin blocks as in the chosen option, but skip the app-status availability merge (single availability_topic per device, no availability_mode) and skip the synthetic bridge entity, accepting via_device as best-effort.

- *Advantages:* Smallest possible diff — no new discovery payload, no value_template complexity; Still fixes the one-device-per-app collapse (Finding 19's headline symptom)
- *Disadvantages:* Does not close Finding 18's actual gap — the app's LWT never touches per-device availability topics, so a crashed app's devices keep showing retained 'online' from before the crash, the exact 'week-old bridge shows current' bug the proposal quotes; via_device is permanently inert for every multi-device app (wiz2mqtt, velux2mqtt) since nothing ever publishes an entity carrying the bare app-level identifiers once per-device blocks take over — HA's device registry does not synthesise a device from a via_device reference alone; Ships a topology hint that silently never renders as topology in Home Assistant's UI, undermining the stated purpose of Finding 19

## Decision Matrix

| Criterion | Bridge diagnostic entity + device_names membership root check | Thread runtime is_root through the AsyncAPI pipeline | Per-device blocks only — no bridge entity, single-topic availability |
| --- | --- | --- | --- |
| Closes the LWT stale-availability gap (Finding 18) | 5 | 5 | 2 |
| HA device topology actually renders (via_device resolves in HA's UI) | 5 | 5 | 1 |
| Root-device detection accuracy | 4 | 5 | 4 |
| Implementation blast radius (schema-only vs. registration pipeline touch) | 5 | 2 | 5 |
| Backward compatibility (unique_id / entity history preserved) | 5 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- HA entities correctly go unavailable when their device or the owning app is down, instead of showing a stale retained reading indefinitely.
- Each physical device (wiz2mqtt's bulbs, velux2mqtt's blind and window) gets its own HA device with its own name prefix, fixing the indistinguishable-entity-names symptom quoted in Finding 19.
- The origin block and bridge connectivity sensor are surfaced in HA's diagnostics/device pages for free, using data (app_version, {app}/status) the framework already publishes.
- unique_id is untouched, so HA re-points existing entity history to the new per-device devices instead of creating duplicate entities.

### Negative

- Existing HA installations see every entity re-parented from the old single app-level device to new per-device devices; the old app-level device is left empty in HA's registry until an operator removes it manually.
- One additional discovery payload (the bridge binary_sensor) and a small amount of extra MQTT discovery traffic is emitted per app that has at least one named device.
- Root-device detection relies on SchemaRegistry.device_names membership rather than the exact runtime is_root flag; a pathological topic collision could misclassify a device (no known real-world case triggers this).
- The availability value_template for named devices assumes HA's Jinja `value_json`/`value` globals behave as documented (value_json undefined on non-JSON payloads) — a HA-side behaviour this codebase does not control.

_2026-08-11_
