---
status: Accepted
date: 2026-09-16
impact: moderate
tags: [mqtt, health, openhab]
---

# ADR-079: OpenHAB Availability Wiring and Thing-Level Override Passthrough

## Status

Accepted **Date:** 2026-09-16

## Context

ADR-058 added availability wiring to `HaDiscoveryGenerator` — every HA entity now carries `availability`/`availability_mode`/`payload_available`/`payload_not_available`, driven by the retained `{prefix}/{device}/availability` topics that `HealthReporter` (ADR-012, ADR-077) already publishes. The openHAB consumer generator (`OpenHabGenerator`) was not touched: `_thing_block` emits the Thing header with no Thing-level config bracket (`[ ... ]`) at all, so `availabilityTopic`, `payloadAvailable`, and `payloadNotAvailable` — all supported by the openHAB MQTT binding's Generic MQTT Thing — are never emitted. An openHAB deployment has no way to learn that a device is unreachable; every Thing stays ONLINE while the broker is up, regardless of whether the device is actually publishing. The `OpenHabOverrides.channel_params` passthrough (ADR-056) operates at channel level and merges into a channel's `[ ... ]`; `availabilityTopic` is a Thing-level parameter, and no Thing-level override mechanism exists. An app author cannot work around this.

A key asymmetry: HA's MQTT discovery supports a multi-topic `availability` list with `availability_mode: "all"`, so the framework combines the device's own availability topic with the app-level `{prefix}/status` heartbeat/LWT topic — an unclean crash (which fires the LWT on `{prefix}/status` but leaves per-device availability topics stuck stale at "online") still marks entities unavailable. openHAB's `availabilityTopic` accepts a single topic string. The crash-stale scenario cannot be closed the same way.

ADR-076 declares openHAB a first-class deployment target and rejects gaps that make a capability difference permanent. No ADR records a deliberate exclusion of availability from the openHAB target. Source: cosalette-apps enhancement proposal (bead `cap-vrtd`).

## Decision

Extend `OpenHabGenerator._thing_block` to always emit a Thing-level `[ ... ]` config bracket carrying `availabilityTopic`, `payloadAvailable`, and `payloadNotAvailable`. The availability topic follows the same resolution as `HealthReporter._availability_topic` and the HA generator's `_availability_block`: named devices (those present in `registry.device_names`) get `{prefix}/{device}/availability`; root devices get `{prefix}/availability`. `payloadAvailable` is `"online"` and `payloadNotAvailable` is `"offline"`, matching the values `HealthReporter` publishes. The prefix used is `_framework_prefix(registry, app)` (ADR-072), consistent with the HA generator. Because the three availability parameters are computed for every Thing, the bracket is never empty and is never omitted — every generated Thing header changes shape from `(bridge) {` to `(bridge) [ ... ] {`.

The single-topic design: openHAB points at the per-device availability topic only, not the app-level `{prefix}/status`. This gives per-device granularity (a device goes offline independently) but does not close the crash-stale gap that HA covers with dual-topic + `availability_mode: "all"`. This applies to root devices too: `{prefix}/availability` is a retained topic written by `HealthReporter` on the same clean-shutdown path as the per-device topics, while the LWT lives only on `{prefix}/status` — so no Thing, root or named, gets crash coverage by default. This is an accepted, documented trade-off — the openHAB binding offers no multi-topic mode, so the only alternatives are (a) always pointing at `{prefix}/status` (crash-accurate but not per-device) or (b) the device topic (per-device but crash-stale). The device topic is chosen because per-device granularity is the more common and more useful signal.

Add `thing_params: dict[str, Any]` to `OpenHabOverrides` and its `OpenHabMeta` mirror — the Thing-level counterpart to `channel_params`. `thing_params` are merged last into the Thing's `[ ... ]` config bracket, so they can add a new Thing-level parameter or override a computed availability default (e.g. pointing `availabilityTopic` at `{prefix}/status` instead). Only emittable properties (the same `_is_emittable` gate that decides which channels a Thing renders) contribute `thing_params`. When multiple properties across channels in the same Thing specify `thing_params`, they are merged in channel-address then property-name order; later entries override earlier ones for the same key.

```python
# Before — no Thing-level config bracket:
Thing mqtt:topic:broker:wiz2mqtt_desk "wiz2mqtt desk" (mqtt:broker:broker) {
    Channels:
        Type number : temperature "Temperature" [
            stateTopic="wiz2mqtt/desk/state",
            transformationPattern="JSONPATH:$.temperature"
        ]
}

# After — availability wired, same channel structure:
Thing mqtt:topic:broker:wiz2mqtt_desk "wiz2mqtt desk" (mqtt:broker:broker) [
    availabilityTopic="wiz2mqtt/desk/availability",
    payloadAvailable="online",
    payloadNotAvailable="offline"
] {
    Channels:
        Type number : temperature "Temperature" [
            stateTopic="wiz2mqtt/desk/state",
            transformationPattern="JSONPATH:$.temperature"
        ]
}

# thing_params override in author code:
from cosalette.schema import consumer, openhab, merge

hsb: Annotated[
    list[int],
    pydantic.Field(json_schema_extra=merge(
        consumer(display_name="HSB"),
        openhab(
            item_type="Color",
            channel_type="color",
            channel_params={"colorMode": "HSB"},
            thing_params={"availabilityTopic": "custom/availability"},
        ),
    )),
]
```

## Decision Drivers

- ADR-076 declares openHAB a first-class deployment target — capability gaps with that shape are explicitly rejected
- The data is already on the wire: HealthReporter publishes retained availability topics (ADR-012, ADR-077) that HA consumes but openHAB ignores
- The openHAB MQTT binding natively supports availabilityTopic/payloadAvailable/payloadNotAvailable at Thing level — no binding extension needed
- No existing ADR records a deliberate exclusion of availability from the openHAB target — the gap is an omission, not a scope choice
- App authors have no escape hatch: channel_params is channel-level, and _thing_block emits no Thing-level bracket or override hook

## Considered Options

### Option 1: Single device topic (chosen)

Point openHAB's availabilityTopic at the per-device topic ({prefix}/{device}/availability for named devices, {prefix}/availability for root devices). This matches the signal HealthReporter publishes and gives per-device granularity. The crash-stale gap is documented but accepted: an unclean crash leaves every device's availability topic — root and named alike — stuck at 'online', because only {prefix}/status carries an LWT.

- *Advantages:* Per-device granularity: each Thing reflects its own device's availability, matching what HA gets; Exact symmetry with HealthReporter._availability_topic and the HA generator's device-topic branch; Zero-config: every Thing is wired without the app author touching a property; thing_params provides an override for operators who prefer the crash-accurate status topic
- *Disadvantages:* Crash-stale: an unclean crash leaves the device availability topics (root and named) stuck at 'online' until the app restarts and re-announces; Asymmetry with HA's dual-topic+all mode — HA covers the crash case, openHAB does not

### Option 2: App-level status topic

Point all openHAB Things at {prefix}/status with a transformationPattern extracting the status field. This topic carries the LWT, so an unclean crash correctly marks Things as OFFLINE. However, every Thing in the app shares one availability signal — if one device is unavailable, there is no way to mark only that Thing offline.

- *Advantages:* Crash-accurate: the LWT on {prefix}/status fires on unclean disconnect, correctly marking Things OFFLINE; Simple: one topic, no per-device resolution needed
- *Disadvantages:* Loses per-device granularity: all Things go OFFLINE together, even when only one device is unreachable; Requires a transformationPattern on the availability topic (the payload is JSON, not plain 'online'/'offline'), adding complexity; Does not match HealthReporter's per-device availability design (ADR-012, ADR-077); Root devices and named devices cannot be distinguished — the signal is always app-level

### Option 3: No availability, thing_params only

Add the thing_params override passthrough but do not emit computed availability. App authors wire availability manually via thing_params. This closes the escape-hatch gap but does not solve the parity problem by default.

- *Advantages:* Simplest generator change — no computed availability logic needed; App authors have full control over what goes into the Thing-level bracket
- *Disadvantages:* Every cosalette app must manually wire availability in every consumer-annotated property — boilerplate that the framework should handle; Violates ADR-076's first-class-target premise: HA gets automatic availability, openHAB gets nothing unless the author opts in; HA discovery and openHAB diverge in default behaviour, making the framework inconsistent

## Decision Matrix

| Criterion | Single device topic | App-level status topic | No availability, thing_params only |
| --- | --- | --- | --- |
| Per-device granularity | 5 | 1 | 3 |
| Crash resilience | 2 | 5 | 3 |
| Consistency with HA target | 4 | 2 | 1 |
| Zero-config for app authors | 5 | 5 | 1 |
| Override flexibility | 5 | 3 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- openHAB deployments see per-device availability for the first time — Things go OFFLINE when a device is unreachable, matching the HA experience
- thing_params closes the Thing-level escape hatch: any Thing-level binding parameter (availabilityTopic override, custom parameters) can now be set from app code without hand-editing generated output
- Consistency: both consumer targets now receive availability wiring from the same device-resolution logic, reducing the surface for target-specific bugs

### Negative

- Crash-stale asymmetry: openHAB Things stay ONLINE after an unclean crash until the app restarts, while HA entities correctly go unavailable via dual-topic+all — this is a known, documented trade-off with no openHAB-side remedy
- Every generated .things file changes: each Thing header gains a [ ... ] config bracket, so operators who diff generated output will see the addition on the next regeneration — there is no byte-identical path, because availability is computed for every Thing

_2026-09-16_
