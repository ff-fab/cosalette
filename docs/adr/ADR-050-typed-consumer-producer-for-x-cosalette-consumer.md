---
status: Accepted
date: 2026-07-27
impact: moderate
tags: [architecture, mqtt, serialization, documentation]
---

# ADR-050: Typed consumer() Producer for x-cosalette-consumer

## Status

Accepted **Date:** 2026-07-27

## Context

The framework already READS the `x-cosalette-consumer` schema extension into the `ConsumerMetadata` frozen dataclass and ships `HaDiscoveryGenerator` and `OpenHabGenerator` that turn it into Home Assistant discovery entities and OpenHAB items. However, apps had no typed way to WRITE the block: there were zero `json_schema_extra` usages across `src`. Authors hand-built `{"x-cosalette-consumer": {...}}` dicts inline, with no key checking — a misspelled `state_clas` or `unit_of_measurement` (the HA name, not the reader key `unit`) silently produced no discovery output. The reader key set (`device_class`, `unit`, `display_name`, `icon`, `state_class`, `read_only`) lived only in the dataclass, so producer and reader could drift with nothing to catch it. The block is not HA-specific: the same six keys feed both the HA and OpenHAB generators. This ADR extends ADR-033 (MQTT schema enforcement) by adding the producer side of a block whose reader ADR-033 already owns.

## Decision

Use a public, typed `cosalette.schema.consumer(**meta)` helper plus a `ConsumerMeta` TypedDict and an `X_COSALETTE_CONSUMER` constant for producing the `x-cosalette-consumer` block, because it makes cosalette own both sides of the extension with a single shared key set. The `ConsumerMeta` key set is exactly the fields of the `ConsumerMetadata` reader dataclass, enforced by a drift-guard test. Typing is keys-only (no value-enum validation — deferred). The key `unit` is emitted (the reader-owned name; HA maps it to `unit_of_measurement`). The surface is named `cosalette.schema`, not `cosalette.ha`, because the block feeds BOTH the HA and OpenHAB generators — consumer-neutral naming. `consumer()` returns a plain dict ready for pydantic `Field(json_schema_extra=...)`; the block rides on the field and survives regeneration via `TypeAdapter(model).json_schema()`.

```python
from typing import Annotated
import pydantic
from cosalette.schema import consumer

class CoverState(pydantic.BaseModel):
    position: Annotated[int, pydantic.Field(json_schema_extra=consumer(
        display_name="Cover Position",
        unit="%",
        state_class="measurement",
        icon="mdi:window-shutter",
    ))]
```

## Decision Drivers

- Single source of truth: the producer key set must not drift from the ConsumerMetadata reader.
- Type safety: catch misspelled or non-existent keys at author time instead of silent no-op discovery output.
- Consumer-neutral naming: the block feeds both the Home Assistant and OpenHAB generators, so the surface must not imply HA-only.
- Regeneration survival: metadata attached to a field must persist through TypeAdapter(model).json_schema() with no hand re-adding.
- Minimal surface: ship the smallest useful producer now and defer value-level validation to avoid over-committing to enum vocabularies.

## Considered Options

### Option 1: Typed consumer() + ConsumerMeta + X_COSALETTE_CONSUMER in cosalette.schema (chosen)

A public shim module `cosalette.schema` re-exports a `consumer(**meta)` helper, a `ConsumerMeta` TypedDict(total=False) whose keys mirror the ConsumerMetadata dataclass, and the `X_COSALETTE_CONSUMER` key constant. The reader is refactored to read via the same constant, and a drift-guard test asserts key-set parity.

- *Advantages:* Typo-checked keys via Unpack[ConsumerMeta] at author time.; Single shared key set with the reader, guarded against drift by a test.; Consumer-neutral name matches that the block feeds both HA and OpenHAB.; Returns a plain dict — no framework coupling, survives regen via TypeAdapter.
- *Disadvantages:* Adds a new public module and three exported names to maintain.; Keys-only typing does not catch invalid values (e.g. an unknown device_class).

### Option 2: No helper — apps hand-build dicts (status quo)

Leave producing the block to app authors, who write literal `{"x-cosalette-consumer": {...}}` dicts and pass them to `Field(json_schema_extra=...)` by hand.

- *Advantages:* Zero new public surface to maintain.; Maximum flexibility — any key can be written.
- *Disadvantages:* No key checking: typos silently produce no discovery output.; The reader key set is duplicated informally in every app, free to drift.; Discoverability is poor — nothing signals which keys are valid.

### Option 3: HA-namespaced helper with value-enum validation (cosalette.ha)

Expose the helper under `cosalette.ha` and validate values against Home Assistant enum vocabularies (device_class, state_class) with pydantic.

- *Advantages:* Catches invalid values, not just invalid keys.; Reads naturally for Home Assistant-first users.
- *Disadvantages:* Misnames a block that also drives the OpenHAB generator.; Couples cosalette to HA's evolving enum vocabularies, creating a maintenance treadmill and version skew.; Larger surface and stricter contract than the current need justifies.

## Decision Matrix

| Criterion | Typed consumer() + ConsumerMeta + X_COSALETTE_CONSUMER in cosalette.schema | No helper — apps hand-build dicts (status quo) | HA-namespaced helper with value-enum validation (cosalette.ha) |
| --- | --- | --- | --- |
| Single source of truth with reader | 5 | 1 | 4 |
| Type safety at author time | 4 | 1 | 5 |
| Consumer-neutral naming | 5 | 3 | 1 |
| Maintenance burden (lower is better) | 4 | 5 | 2 |
| Regeneration survival | 5 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Apps get a typed, discoverable, single-source way to attach consumer discovery metadata to model fields.
- Producer and reader share one key set, guarded against drift by a test — misspellings fail fast instead of silently.
- The consumer-neutral cosalette.schema name keeps the door open for the block to serve additional consumers beyond HA and OpenHAB.
- The helper returns a plain dict, so it composes with pydantic Field(json_schema_extra=) and survives schema regeneration with no framework coupling.

### Negative

- Keys-only typing does not validate values; an invalid device_class or state_class still passes producer-side.
- A new public module and three exported names widen the API surface that must be kept stable.
- Value-enum validation and higher-level presets (cos-xgsw) are deferred, so authors still consult HA/OpenHAB docs for valid values.

_2026-07-27_
