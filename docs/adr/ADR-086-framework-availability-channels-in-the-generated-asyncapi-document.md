---
status: Accepted
date: 2026-10-03
impact: high
tags: [mqtt, health, serialization]
---

# ADR-086: Framework availability channels in the generated AsyncAPI document

## Status

Accepted **Date:** 2026-10-03

## Context

Every entity that owns availability publishes a retained `online`/`offline` payload at runtime (ADR-012). The owners are devices, telemetry, commands and named streams (ADR-081 amendment). A named entity uses `{prefix}/{name}/availability`. A root entity (ADR-058) uses the app-wide `{prefix}/availability`. A telemetry and a command that share a name announce once. Root streams are heartbeat-only and publish no availability topic. Sub-entities from `ctx.sub_entity()` publish their own topic outside the health reporter (ADR-031).

The generated AsyncAPI document (`App.asyncapi()`, `schema dump`) did not describe any of these topics. The ADR-081 named-stream amendment asked for an availability channel next to each named stream's `/state`. That channel was left out because no other archetype had one, and a stream-only channel would have been inconsistent. Follow-up cos-0iyk asked for one decision that covers every owner.

Three constraints shape the answer:

- **ADR-054.** `x-cosalette-archetype` is a closed enum. Loaders released before a new value reject the whole document, so a new `availability` archetype would break every older consumer of a regenerated schema. Unknown `x-` keys are ignored by older loaders.
- **ADR-073 and the consumer generators.** Home Assistant and openHAB output already carries an availability list built from the same topic rule. A second, schema-driven source for those topics must not change their output.
- **ADR-072.** Addresses are composed from the resolved topic prefix, while `x-cosalette-app` stays the app name. ADR-072 also promised that an unprefixed document stays byte-identical to the output produced before ADR-072. Any new channel breaks that promise, so it has to be revised deliberately.

## Decision

Use one framework-owned availability channel per entity that owns availability at runtime, marked with a new `x-cosalette-framework: "availability"` extension instead of an archetype, because it documents every retained topic the app really publishes without breaking older loaders or changing any generated consumer artefact.

- **Coverage.** Devices, telemetry, commands and *named* streams each get a channel. Channels are deduplicated by topic, so a telemetry and a command that share a name get one channel, and the single root entity gets the app-wide `{prefix}/availability`, even when its name carries a Router prefix such as `sensors/status`. Root streams and sub-entities get no channel, matching the runtime. A literal `enabled=False` never registers and gets no channel. A callable `enabled=` is listed like the entity's `/state` channel.
- **Channel shape.** The channel id is `{camelName}Availability`, or `availability` for the root. The address is prefix-aware (ADR-072). The payload is `{"type": "string", "enum": ["online", "offline"]}` with `bindings.mqtt` `qos: 1`, `retain: true`. The channel carries `x-cosalette-app`, `x-cosalette-framework: "availability"` and `x-cosalette-discoverable: false`, and no `x-cosalette-archetype`. Each channel has a `publish{Camel}Availability` send operation (`publishAvailability` for the root).
- **One topic rule.** A shared `availability_topic(prefix, name, is_root=...)` helper in `cosalette._constants` is used by the health reporter, the AsyncAPI generator, the HA/openHAB generators and the validator's skip list.
- **Loader.** `x-cosalette-framework` parses into `ChannelSchema.framework_role` and is re-emitted by `schema slice`. A present value must be a non-empty string.
- **Exclusions.** Consumer generation (`_is_consumer_visible`, `silent_consumer_channels`) ignores framework channels, so HA discovery and openHAB output stay byte-identical. The broker ACL skips them in the per-channel grant loop, because the fixed `{prefix}/availability` and `{prefix}/+/availability` grants already cover them, so ACL output is unchanged. Device-name extraction ignores them because they carry no archetype, so enforcement and validator skip topics are unchanged. The MCP manifest table does not list them.
- **Contract version.** `x-cosalette-contract-version` goes from `"1"` to `"2"`. This deliberately revises ADR-072's guarantee: an unprefixed document is no longer byte-identical to the pre-ADR-072 output, and the golden fixture is regenerated once. Adopters who commit generated schema artefacts must regenerate them, and the retained `{prefix}/_meta/registry` snapshot now lists the availability channels.
- **Out of scope.** The app-wide `{prefix}/status` heartbeat and the `{prefix}/error` / `{prefix}/{device}/error` topics remain undocumented in the schema. They are follow-ups that can reuse `x-cosalette-framework` with roles such as `status` and `error`.

```json
{
  "deskAvailability": {
    "address": "house/wiz/desk/availability",
    "x-cosalette-app": "wiz2mqtt",
    "messages": {"message": {"payload": {"type": "string", "enum": ["online", "offline"]}}},
    "bindings": {"mqtt": {"qos": 1, "retain": true}},
    "x-cosalette-framework": "availability",
    "x-cosalette-discoverable": false
  }
}
```

## Decision Drivers

- The schema should describe every retained topic an app publishes, so contract-first consumers do not have to know the availability convention out of band
- Coverage must match the runtime exactly, including named streams (cos-0iyk, ADR-081) and the root and shared-name deduplication
- Older loaders must keep accepting regenerated documents (ADR-054 closed archetype enum)
- Home Assistant, openHAB and broker ACL output must not change
- One topic rule shared by the reporter and every generator, so they cannot drift apart

## Considered Options

### Option 1: Framework channels with x-cosalette-framework (chosen)

One availability channel per owning entity, marked with a new x-cosalette-framework extension and x-cosalette-discoverable: false, no archetype. Consumer generators, ACL and device-name extraction skip it.

- *Advantages:* Documents every availability topic, for every owner kind, from the same rule the runtime uses; Older loaders ignore the unknown x- key and accept the document; HA, openHAB and ACL output stay byte-identical; x-cosalette-discoverable: false also protects older consumer generators; The extension can later carry status and error roles without another schema change
- *Disadvantages:* Changes every generated document and bumps the contract version, so committed schema artefacts must be regenerated; Every downstream tool needs an explicit framework-channel skip where it iterates channels

### Option 2: New availability archetype

Add availability to the x-cosalette-archetype enum and emit the channels with that archetype.

- *Advantages:* Reuses the existing archetype mechanism that tools already group and filter by; No new extension key to learn
- *Disadvantages:* Older loaders reject the whole document because the archetype enum is closed (ADR-054); Archetypes describe what an app author registers; availability is framework plumbing that no author registers; Device-name extraction treats archetype channels as devices, so it would need a special case anyway

### Option 3: Streams-only availability channels

Add an availability channel only next to named streams, as the ADR-081 amendment originally asked.

- *Advantages:* Smallest change to generated documents; Answers the original stream-specific request
- *Disadvantages:* Inconsistent: devices, telemetry and commands publish the same kind of topic but stay undocumented; Consumers would have to special-case streams to find availability

### Option 4: No availability channels

Keep availability out of the schema and document the topic convention in prose only.

- *Advantages:* No change to generated documents, the golden fixture or the contract version; No new skip logic in downstream tools
- *Disadvantages:* The schema stays incomplete: retained topics the app publishes are invisible to contract-first consumers; The ADR-081 follow-up stays open with no decision

## Decision Matrix

| Criterion | Framework channels with x-cosalette-framework | New availability archetype | Streams-only availability channels | No availability channels |
| --- | --- | --- | --- | --- |
| Schema completeness | 5 | 5 | 2 | 1 |
| Compatibility with older loaders | 5 | 1 | 5 | 5 |
| Consistency across owner kinds | 5 | 5 | 1 | 3 |
| Stability of HA, openHAB and ACL output | 5 | 3 | 4 | 5 |
| Churn for adopters with committed schema artefacts | 2 | 1 | 3 | 5 |
| Room for status and error follow-ups | 5 | 2 | 2 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Every availability topic an app publishes is in its AsyncAPI document, with its retained QoS 1 binding and its two payloads
- The reporter, the AsyncAPI generator, the HA/openHAB generators and the validator share one availability_topic helper, and a parity test checks the generated addresses against a real AppHarness run
- Older loaders keep working, and HA discovery, openHAB and broker ACL output are byte-identical to before
- Resolves cos-0iyk and the ADR-081 named-stream follow-up with one uniform rule

### Negative

- Every generated document changes and the contract version moves to 2; ADR-072's byte-identical guarantee for unprefixed documents is revised, and adopters must regenerate committed schema artefacts
- The retained {prefix}/_meta/registry snapshot grows by one channel per owning entity
- Tools that iterate channels must skip framework channels explicitly; the consumer generators, ACL, device-name extraction and MCP manifest do so today
- Sub-entity availability ({prefix}/{device}/{sub}/availability) and the status and error topics remain undocumented in the schema

_2026-10-03_
