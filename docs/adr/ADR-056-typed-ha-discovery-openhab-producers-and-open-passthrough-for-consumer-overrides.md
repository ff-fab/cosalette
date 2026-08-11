---
status: Accepted
date: 2026-08-11
impact: moderate
tags: [architecture, mqtt, serialization, documentation]
---

# ADR-056: Typed ha_discovery()/openhab() Producers and Open Passthrough for Consumer Overrides

## Status

Accepted **Date:** 2026-08-11

## Context

ADR-050 gave apps a typed, drift-guarded producer for the six-key `x-cosalette-consumer` block, but the two platform-specific override extensions it feeds into — `x-cosalette-ha-discovery` and `x-cosalette-openhab` — were left as untyped, hand-built dicts. The cosalette-apps consumer-integration proposal (`tmp/cosalette-consumer-integration-proposal.md`, Findings 13, 21, 22), raised while planning the wiz2mqtt migration (14 WiZ bulbs, each with state and command channels, booleans, bounded integers, an enum and a colour triple), surfaces two concrete gaps this ADR closes. Finding 13: `_CONSUMER_FIELD_MAP` in `_consumer_gen.py` whitelists four Home Assistant keys with no escape hatch, so an author who sets `component: light` (Finding 10) has no way to supply `schema: json`, `supported_color_modes`, `min_kelvin`, `effect_list` or any of the roughly one hundred platform keys Home Assistant MQTT discovery actually accepts — the curated set will always trail HA's own release cadence. Finding 21: `OpenHabOverrides` exposes `item_type`, `label`, `groups` and `tags`, but the Thing *channel* side — its type and its parameters (`on`/`off` for a switch, `min`/`max`/`step` for a dimmer, `colorMode` for a colour channel) — is entirely inferred and has no annotation path at all, which is the direct cause of three separate bugs (channel type not overridable, JSON booleans staying UNDEF, numeric constraints not reaching the channel). Finding 22: neither extension gets the typed, discoverable, drift-guarded treatment `consumer()` established — authors hand-write untyped `json_schema_extra` dicts for both, with no static check and no protection against the reader and producer key sets drifting apart as Findings 13 and 21 grow the surface. This ADR is upstream of the wiz2mqtt build: `cap-10u.6` (the app's tracking epic) names Findings 21 and (transitively, via the annotation surface it depends on) 13 among its closing conditions, and blocks `cap-10u.8` (app scaffold) and `cap-10u.14` (consumer integration) until the framework ships an open, typed way to reach platform keys the curated field set does not cover.

## Decision

Extend the ADR-050 typed-producer pattern to both override extensions: add `cosalette.schema.ha_discovery(**HaDiscoveryMeta)` and `cosalette.schema.openhab(**OpenHabMeta)`, each a TypedDict-backed producer whose keys mirror a reader-side frozen dataclass one-for-one, guarded by the same drift-guard test pattern as `ConsumerMeta`/`ConsumerMetadata`. `HaDiscoveryOverrides` and `OpenHabMeta` both gain one additional field beyond today's curated keys: `HaDiscoveryOverrides.extra: dict[str, Any]` (Finding 13) is merged into the generated HA discovery payload last, after every curated key, so it can both add new keys and override a curated one when HA's platform-specific requirements conflict with cosalette's generic default. `OpenHabOverrides` gains `channel_type: str | None` and `channel_params: dict[str, Any]` (Finding 21): `channel_type` overrides the inferred `.things` channel type descriptor the same way `item_type` already overrides the Item type, and `channel_params` is merged into the generated channel's parameter list last, after every computed parameter (`stateTopic`/`commandTopic`, the JSONPATH transform, the `on`/`off` pair), so an author can add `colorMode: HSB` or override a computed default. Both `extra` and `channel_params` are untyped `dict[str, Any]` by design — a curated key set cannot anticipate Home Assistant's or openHAB's next release, so the passthrough is deliberately open rather than a longer allowlist. Because a single field commonly needs `consumer()` plus one or both override producers simultaneously (every wiz2mqtt field does), a `cosalette.schema.merge(*blocks)` helper folds multiple producer outputs into the one dict `Field(json_schema_extra=...)` accepts, shallow-merging top-level extension keys and raising on a duplicate key. `X_COSALETTE_HA_DISCOVERY` and `X_COSALETTE_OPENHAB` constants are added alongside `X_COSALETTE_CONSUMER`, and the reader in `_loader_helpers.py` is refactored to read through the same three constants so producer and reader cannot drift on the key name either.

```python
from typing import Annotated
import pydantic
from cosalette.schema import consumer, ha_discovery, openhab, merge

class BulbState(pydantic.BaseModel):
    hsb: Annotated[
        list[int],
        pydantic.Field(json_schema_extra=merge(
            consumer(display_name="HSB"),
            openhab(
                item_type="Color",
                channel_type="color",
                channel_params={"colorMode": "HSB"},
            ),
        )),
    ]
    state: Annotated[
        bool,
        pydantic.Field(json_schema_extra=merge(
            consumer(display_name="Desk Lamp"),
            ha_discovery(extra={"schema": "json", "optimistic": False}),
        )),
    ]
```

## Decision Drivers

- Consistency: apps already know the consumer() shape from ADR-050; ha_discovery()/openhab() should look and behave the same way rather than introduce a second pattern.
- Open extensibility: Home Assistant's and openHAB's own key vocabularies evolve faster than a framework release cycle, so the two passthrough fields must accept arbitrary keys rather than grow into a longer allowlist that permanently trails upstream.
- Single source of truth: each producer's TypedDict must not drift from its reader-side dataclass, guarded by an automated test, exactly as ConsumerMeta/ConsumerMetadata already are.
- Composability: consumer(), ha_discovery() and openhab() commonly annotate the same field together (every wiz2mqtt property needs at least two), so the three producers must combine into the single dict pydantic's json_schema_extra accepts.
- Minimal surface now: ship the smallest useful producer pair and defer value-level validation of HA/openHAB enum vocabularies, matching the precedent ADR-050 already set for consumer().

## Considered Options

### Option 1: Two typed producers (ha_discovery, openhab) + extra/channel_params passthrough + merge() helper (chosen)

Add cosalette.schema.ha_discovery(**HaDiscoveryMeta) and cosalette.schema.openhab(**OpenHabMeta), TypedDict-parity guarded against HaDiscoveryOverrides/OpenHabOverrides exactly as consumer() is guarded against ConsumerMetadata. Add extra: dict[str, Any] to the HA overrides and channel_type/channel_params to the openHAB overrides as open passthrough fields merged last by the generators. Add a merge() helper so one field can carry consumer() + ha_discovery() + openhab() together.

- *Advantages:* Matches the ADR-050 pattern apps already know — same TypedDict-parity shape, same drift-guard test pattern, same import style.; Typo-checked curated keys stay the ergonomic front door; extra/channel_params stay a genuinely open back door that cannot itself drift out of date.; merge() solves composability once, centrally, instead of every app hand-rolling a dict union with silent last-write-wins on key collisions.; Two separate producers keep the HA and openHAB extensions independently evolvable — a new openHAB key does not touch the HA producer's type or tests.
- *Disadvantages:* Three producer calls plus a merge() call is more ceremony at the call site than a single combined call would be.; Two new public modules-level functions plus a merge() helper widen the API surface that must be kept stable, on top of the three ADR-050 already added.; extra/channel_params are keys-only escape hatches with zero validation — a typo inside them fails exactly as silently as the pre-ADR-050 status quo did for the curated keys.

### Option 2: No producers — apps hand-build ha-discovery/openhab dicts (status quo)

Leave x-cosalette-ha-discovery and x-cosalette-openhab exactly as they are: authors write literal dicts by hand and pass them through Field(json_schema_extra=...), with no typed producer and no passthrough field, working around Finding 13/21 downstream per-app if at all.

- *Advantages:* Zero new public surface to maintain.; No decision needed about passthrough shape or merge semantics.
- *Disadvantages:* Leaves Findings 13 and 21 completely unaddressed — component: light and channel_type overrides remain either impossible or untyped.; The inconsistency with consumer() actively confuses authors: one of three sibling extensions on the same field is typed and two are not.; Blocks cap-10u.8/cap-10u.14 indefinitely since wiz2mqtt cannot express its bulb entities without the passthrough this option omits entirely.

### Option 3: Single unified override() producer bundling both extensions

Instead of two producers, add one cosalette.schema.override(ha={...}, openhab={...}) call that internally builds both x-cosalette-ha-discovery and x-cosalette-openhab keys from nested TypedDicts, avoiding the need for a separate merge() helper since one call already returns a two-key dict (plus consumer() would need folding in as a third nested argument or a separate merge() step anyway).

- *Advantages:* One import, one call site for both override extensions when both are needed.; Removes the risk of forgetting to merge() ha_discovery() and openhab() together.
- *Disadvantages:* Still needs consumer() merged in separately (it is the far more common case, present on every annotated field), so the ceremony savings are partial at best.; Nested TypedDicts (override(ha=HaDiscoveryMeta, openhab=OpenHabMeta)) are a new authoring shape ADR-050 apps have not seen, unlike two producers that look exactly like consumer().; Couples the HA and openHAB extensions' evolution into one function signature, so a breaking change to one's key set forces a signature change touching both.; Loses the drift-guard test's clean one-producer-to-one-dataclass mapping; a combined TypedDict pairing would need its own bespoke guard logic.

## Decision Matrix

| Criterion | Two typed producers (ha_discovery, openhab) + extra/channel_params passthrough + merge() helper | No producers — apps hand-build ha-discovery/openhab dicts (status quo) | Single unified override() producer bundling both extensions |
| --- | --- | --- | --- |
| Consistency with ADR-050's established consumer() pattern | 5 | 1 | 3 |
| Open extensibility for unanticipated HA/openHAB keys | 5 | 3 | 5 |
| Single source of truth / drift-guard testability | 5 | 1 | 3 |
| Composability with consumer() on a shared field | 4 | 2 | 3 |
| Maintenance burden (lower is better) | 3 | 5 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Apps get a typed, discoverable, single-source way to attach both HA and openHAB override metadata, matching the consumer() authoring experience apps already know from ADR-050.
- The extra and channel_params passthrough fields give authors a permanent escape hatch that does not require a framework release every time Home Assistant or openHAB adds a platform key, resolving Findings 13 and 21 without committing cosalette to tracking either vocabulary.
- merge() gives apps one documented way to combine consumer() + ha_discovery() + openhab() on a single field instead of each app inventing its own dict-union convention.
- The shared X_COSALETTE_HA_DISCOVERY / X_COSALETTE_OPENHAB constants and drift-guard tests mean the two override readers and their producers cannot silently diverge, closing Finding 22.
- Unblocks cap-10u.8 and cap-10u.14 downstream in cosalette-apps, and unblocks cos-hh2j (component-aware HA payload builders) which consumes this annotation surface.

### Negative

- extra and channel_params are keys-only escape hatches with no value validation, so a typo inside either dict still produces silent no-op or rejected output — exactly the failure mode ADR-050 accepted for the curated keys and now accepts again for the passthrough.
- Two new public producer functions plus a merge() helper further widen the cosalette.schema surface that must be kept stable, on top of consumer()/temperature()/percent().
- Authors must remember to merge() multiple producer outputs rather than passing several json_schema_extra= dicts; forgetting it silently drops all but the last producer call at the pydantic Field level.
- channel_type/channel_params only add the annotation surface — the component-aware payload builders that fully exploit them for composite entities (Finding 20) are deferred to cos-hh2j, so this ADR alone does not make every wiz2mqtt entity expressible yet.

_2026-08-11_
