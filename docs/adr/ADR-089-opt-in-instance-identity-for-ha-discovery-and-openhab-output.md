---
status: Accepted
date: 2026-10-05
impact: moderate
tags: [mqtt, configuration, naming]
---

# ADR-089: Opt-in instance identity for HA discovery and openHAB output

## Status

Accepted **Date:** 2026-10-05

## Context

Every Home Assistant discovery identity cosalette emits is derived from the app name: the discovery topic `node_id`, `unique_id` (`cosalette_<app>_<object_id>`), the bridge and device `identifiers` and `via_device`, and the store key of the discovery orphan-cleanup snapshot (ADR-059). The openHAB generator does the same for Thing UIDs, Item ids, labels and the item group. ADR-072 made `mqtt.topic_prefix` a pure *transport* setting and explicitly kept the app name as identity, so `unique_id`s stay stable when an operator sets a prefix.

The early adopter cosalette-apps runs two instances of the same app (for example one per room) against one broker, separated only by `MQTT__TOPIC_PREFIX`. Both publish `homeassistant/binary_sensor/<app>/bridge/config` with the same `unique_id` and device identifier: the last writer wins in Home Assistant, the entities of one instance disappear, and orphan cleanup keyed by the app name can clear the other instance's configs when the instances share a store. Their proposal was to derive identity from a custom topic prefix automatically. That would change every `unique_id` of existing single-instance deployments that already use a custom prefix, which breaks the stability guarantee of ADR-058 and ADR-072 and orphans their entity history in Home Assistant.

## Decision

Use an opt-in instance identity, `settings.mqtt.instance_id` (env `MQTT__INSTANCE_ID`), that replaces the app name in every identity field of Home Assistant discovery and openHAB output, because it separates instances without a code change while leaving the output of every deployment that does not set it byte-identical.

Scope: the HA discovery `node_id`, `unique_id`, device `identifiers`, `via_device` and the bridge device name; the discovery snapshot store key; the openHAB Thing UID, Thing label, Item ids and item group. Transport topics (`state_topic`, availability, `{prefix}/status`) keep following `mqtt.topic_prefix` (ADR-072), and the HA `origin.name` stays the app name because it names the software. Allowed characters are letters, digits, `_` and `-`, so the id survives every generated identifier unchanged.

The offline generators take the same value as `cosalette schema ha-discovery --instance-id` and `cosalette schema openhab --instance-id`; they reject it for a document that describes more than one app, since one id cannot name several processes.

At startup the app logs a warning when runtime discovery is enabled, `mqtt.topic_prefix` is set and differs from the app name, and no instance id is set: a custom prefix is the usual sign of a second instance. The prefix never changes identity on its own.

```yaml
# docker-compose.yml: two instances of one app on one broker
services:
  wiz-attic:
    environment:
      MQTT__TOPIC_PREFIX: house/attic/wiz
      MQTT__INSTANCE_ID: wiz-attic
  wiz-cellar:
    environment:
      MQTT__TOPIC_PREFIX: house/cellar/wiz
      MQTT__INSTANCE_ID: wiz-cellar
```

## Decision Drivers

- unique_id stability for existing deployments, including those with a custom topic prefix (ADR-058, ADR-072)
- Operators must be able to separate instances without changing app code
- Orphan cleanup of one instance must never touch another instance's discovery configs
- HA and openHAB output should follow one identity rule

## Considered Options

### Option 1: Opt-in instance id setting (chosen)

Add `mqtt.instance_id` (default empty, meaning the app name) and use it for every identity field in HA discovery, the discovery snapshot key and openHAB output, with a `--instance-id` CLI option and a startup warning for the likely-collision case.

- *Advantages:* Unset means byte-identical output, so no existing unique_id changes; Settable per deployment through the environment; One rule covers runtime discovery, offline CLI output and orphan cleanup
- *Disadvantages:* Operators must know to set it; the startup warning only covers the custom-prefix case; Setting it on an existing deployment changes its ids once and leaves the old configs retained

### Option 2: Derive identity from the topic prefix

The adopter proposal: when `mqtt.topic_prefix` differs from the app name, build node_id and unique_ids from the prefix.

- *Advantages:* No new setting; multi-instance deployments that already use distinct prefixes work automatically
- *Disadvantages:* Changes every unique_id of single-instance deployments that use a custom prefix, breaking ADR-058/ADR-072 stability; Couples identity to transport again, which ADR-072 separated on purpose

### Option 3: Code-level option on App.discovery()

Accept an identity argument in `App.discovery(...)` only.

- *Advantages:* No settings surface
- *Disadvantages:* Every instance needs its own code or a hand-written settings lookup; Does not reach the offline openHAB generator

## Decision Matrix

| Criterion | Opt-in instance id setting | Derive identity from the topic prefix | Code-level option on App.discovery() |
| --- | --- | --- | --- |
| unique_id stability for existing deployments | 5 | 1 | 5 |
| Separable without code changes | 5 | 5 | 1 |
| Covers HA, openHAB and orphan cleanup alike | 5 | 4 | 2 |
| Operator effort | 3 | 5 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Several instances of one app can share a broker and a Home Assistant without overwriting each other's entities
- Orphan cleanup is keyed by the instance identity, so instances that share a store never diff against each other
- Deployments that do not set the id keep byte-identical discovery and openHAB output

### Negative

- Adding an instance id to a running deployment changes its ids once: Home Assistant creates new entities, and the old retained configs stay until removed (for example by deleting the device in Home Assistant or publishing empty retained messages), because cleanup under the new key cannot know the old identity
- The startup warning cannot detect two instances that share both the app name and the topic prefix

_2026-10-05_
