---
status: Accepted
date: 2026-08-11
impact: moderate
tags: [architecture, mqtt, serialization, devices, documentation]
---

# ADR-057: Component-Aware HA Payload Builders via Channel-Level Composite Entities

## Status

Accepted **Date:** 2026-08-11

## Context

ADR-056 opened a typed, drift-guarded producer surface for both HA/openHAB override extensions but explicitly deferred the payload-generation half: 'the component-aware payload builders that fully exploit them for composite entities (Finding 20) are deferred to cos-hh2j, so this ADR alone does not make every wiz2mqtt entity expressible yet.' Two findings from the cosalette-apps consumer-integration proposal (tmp/cosalette-consumer-integration-proposal.md) remain open as a result. Finding 10: `_infer_component()` lets `x-cosalette-ha-discovery.component` redirect the discovery topic (e.g. to `homeassistant/light/...`) but `_build_payload()` still emits the same six scalar keys (`state_topic`, `value_template`, `command_topic`, `command_template`, ...) regardless — verified against `component: light`, HA rejects the config outright because `value_template` is not a light key and no `command_topic` was present. Finding 20: `_payloads_for_channel()` emits exactly one HA entity per annotated property, with no way to say 'these N JSON fields are one entity' — the proposal's own words: 'This is what blocks every rich consumer entity, not just wiz2mqtt's ... All of them are currently expressible only as a scatter of sensors.' The worked example is concrete: one WiZ bulb needs a single HA `light` entity (schema: json) spanning `state`, `brightness`, `color_temp_kelvin`, plus a native openHAB `Color` item over a `hsb` field the HA side ignores — 14 bulbs make this the literal blocker for wiz2mqtt's migration (`cap-10u.6`, which gates `cap-10u.8`/`cap-10u.14`). A second architectural fact shapes the fix: ADR-055 established that a `device` archetype channel with `payload_model=` emits TWO channels sharing one model type — a send `/state` channel and a receive `/set` channel — so any per-channel composite-entity mechanism must merge contributions from both channels into one HA config rather than emitting two incomplete ones (one with only `state_topic`, one with only `command_topic`).

## Decision

Attach composite HA entity specs at the pydantic MODEL level (not the field level) via `cosalette.schema.ha_entities(ha_entity(component=..., name=..., extra={...}), ...)` passed to `pydantic.ConfigDict(json_schema_extra=...)`. Pydantic embeds model-level `json_schema_extra` as a sibling key of `properties`/`type` in the generated JSON Schema, which becomes the channel's `payload_schema` verbatim — so this is 'channel-level' in cosalette's one-model-per-channel design without touching the registration/decorator pipeline (`_app.py`, `_wiring`, `_asyncapi.py` channel-dict assembly) at all. Add a reader-side `HaEntitySpec` frozen dataclass (`component: str`, `name: str | None`, `extra: dict[str, Any]`) and a `ChannelSchema.ha_entities: tuple[HaEntitySpec, ...]` field, populated in `_loader_helpers.py` by reading `payload_schema[x-cosalette-ha-discovery]['entities']` — structurally distinct from the existing FIELD-level `x-cosalette-ha-discovery` scalar overrides (component/value_template/command_template/expire_after/extra) added by ADR-056, which keep working unchanged for single-property entities. In `HaDiscoveryGenerator`, a channel carrying `ha_entities` is excluded entirely from scalar per-property generation (a composite entity replaces the per-field scatter for that channel — Finding 20's whole point) and instead routed through a new composite path: channels are grouped by `(app, resolved_device)` exactly as `OpenHabGenerator._channels_by_device` already groups Things, then for each distinct `(component, name)` entity spec found across the group's channels, `state_topic` is taken from whichever channel has `direction in (send, both)` and `command_topic` from whichever has `direction in (receive, both)` — merging the ADR-055 state/set pair into one config. `unique_id`/`object_id`/topic segment are derived the same way scalar entities already are (device name + slug), and the discovery topic's component segment comes from `spec.component`, fixing Finding 10 structurally: the component now selects a real builder, not just a topic string. Three named component builders apply curated defaults before `extra` is merged last (mirroring `HaDiscoveryOverrides.extra`'s override-last semantics from ADR-056): `light` defaults `schema: "json"` (HA's MQTT JSON light schema reads/writes the retained JSON body directly, matching how cosalette already publishes the full model — no per-field `value_template` needed, unlike scalar entities); `climate` drops the bare `state_topic`/`command_topic` keys entirely, since HA's MQTT climate has no single state/command topic — every capability (mode, target temperature, ...) needs its own `<x>_state_topic`/`<x>_command_topic` pair that only the author can name via `extra`; `cover` keeps the inherited `state_topic`/`command_topic` unchanged, since a plain open/close/stop cover accepts them natively. Any component not in this table (including unknown/future ones) gets the base topics with no extra defaults. `extra` stays a fully open, untyped passthrough exactly like ADR-056's `HaDiscoveryOverrides.extra` and `OpenHabOverrides.channel_params` — the proposal's explicit request for 'a back door beside the curated allowlist' applies here too, since `brightness: true` / `supported_color_modes` / `state_value_template` are themselves native HA vocabulary keys with no cosalette-side semantics to curate. OpenHAB generation is unchanged: composite mapping is HA-only in this ADR; the worked example's native openHAB `Color` item is already fully expressible via ADR-056's per-property `channel_type`/`channel_params`.

```python
from typing import Annotated
import pydantic
from cosalette.schema import consumer, ha_entities, ha_entity, openhab, merge

class BulbState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra=ha_entities(
            ha_entity(
                component="light",
                name="Desk Lamp",
                extra={
                    "schema": "json",
                    "brightness": True,
                    "supported_color_modes": ["color_temp", "hs"],
                },
            ),
        )
    )

    state: Annotated[bool, pydantic.Field(json_schema_extra=consumer(display_name="Desk Lamp"))]
    brightness: Annotated[int, pydantic.Field(ge=0, le=255, json_schema_extra=consumer())]
    color_temp_kelvin: Annotated[int | None, pydantic.Field(ge=2200, le=6500, json_schema_extra=consumer())]
    hsb: Annotated[
        list[int],
        pydantic.Field(json_schema_extra=merge(
            consumer(display_name="HSB"),
            openhab(item_type="Color", channel_type="color", channel_params={"colorMode": "HSB"}),
        )),
    ]

```

## Decision Drivers

- Finding 10 and Finding 20 are one root cause: scalar, per-property generation cannot produce a valid non-scalar HA entity no matter how the component is overridden — the fix has to change what gets built, not just what topic it lands on.
- ADR-055's state/set two-channel device pattern means any per-channel entity mechanism must merge contributions from two ChannelSchema instances into one config, or every composite entity ships half-built.
- Consistency with the existing extension architecture: every x-cosalette-* block so far rides on pydantic's generated JSON Schema (field-level via Field(json_schema_extra=...)) with zero touch to the registration/decorator pipeline; a channel-level mechanism should preserve that property if at all possible.
- Open extensibility: HA's per-component key vocabulary (light schemas, climate capability topics, cover position semantics) evolves faster than a framework release cycle, so curated defaults must stay minimal and overridable rather than trying to model each platform exhaustively — the same rationale ADR-056 already established for extra/channel_params.
- Typo/drift safety: a mechanism built on property-name string references (e.g. a list of sibling field names) reintroduces the exact silent-drift failure mode ADR-050 eliminated for consumer() by making the producer and reader share one typed vocabulary.

## Considered Options

### Option 1: Model-level ha_entities()/ha_entity() + channel-grouping merge + light/climate/cover builders (chosen)

Composite entity specs ride on the pydantic model's own json_schema_extra (ConfigDict), landing as a sibling key in the generated payload schema. The loader reads it into ChannelSchema.ha_entities; the generator groups channels by (app, device), merges state_topic/command_topic across the ADR-055 send/receive channel pair per (component, name) key, and applies one of three named component builders before merging the entity's open extra dict last.

- *Advantages:* Zero changes to _app.py, _wiring, or _asyncapi.py channel-dict assembly — the mechanism is purely schema-side, exactly like every other x-cosalette-* extension to date.; extra stays a fully open, untyped passthrough consistent with ADR-056's precedent, so HA's evolving component vocabularies never require a cosalette release to reach.; No property-name string references — nothing to typo, nothing to drift silently when a field is renamed.; The channel-grouping merge that fixes the ADR-055 two-channel case is a bounded, testable addition to HaDiscoveryGenerator, mirroring OpenHabGenerator._channels_by_device's existing grouping pattern.
- *Disadvantages:* Two structurally distinct ways to write x-cosalette-ha-discovery now exist (field-level scalar overrides vs model-level composite entities) that authors must learn apart, even though the reader disambiguates them automatically by where they were found.; A channel that declares ha_entities forfeits its own per-property scalar HA generation entirely — a channel cannot be partially composite and partially scalar without splitting into two payload models.; climate and cover get only minimal component-aware defaults (drop-invalid-keys / keep-as-is); full per-capability topic/template generation is deferred, so climate entities remain mostly author-authored via extra.

### Option 2: Decorator-level ha_entities kwarg threaded through registration

Add an ha_entities= kwarg to @app.device/@app.telemetry/@app.command and thread it through _wiring and _asyncapi.py's channel-dict builders, emitting the entity spec directly onto the channel dict (parallel to how x-cosalette-archetype/x-cosalette-app are already emitted from ChannelSchema fields).

- *Advantages:* The registration call site already knows which channels belong to one device (state + set), so the state/command merge could happen at emission time with no separate grouping pass in the generator.; Channel-level really means channel-level — no reliance on pydantic's model-level json_schema_extra behavior as an implementation detail.
- *Disadvantages:* Touches _app.py, _wiring registration, and _asyncapi.py channel-dict assembly — a much larger surface than every prior x-cosalette-* extension, all of which stayed schema-side.; Couples the entity definition to the registration call site rather than the model, so reusing one payload model across two registrations could silently produce two different entity specs for the same fields.; Breaks the established pattern that consumer()/ha_discovery()/openhab() metadata is fully recoverable from TypeAdapter(model).json_schema() alone — a composite entity would need the live App registry to inspect, which the existing offline `cosalette schema ha-discovery` CLI path does not have.

### Option 3: Property-level 'primary field' marker referencing sibling field names

Annotate one property per entity with json_schema_extra={'entity': {'component': ..., 'fields': ['brightness', 'color_temp_kelvin', ...]}}, listing the other property names that belong to the same entity, reusing the existing field-level extension mechanism instead of introducing a model-level one.

- *Advantages:* Reuses the existing Field(json_schema_extra=...) mechanism with no new pydantic ConfigDict pattern to teach.; Roughly the same implementation footprint as the chosen option for the loader and the channel-grouping merge, which is needed either way.
- *Disadvantages:* Reintroduces the exact string-reference drift ADR-050 eliminated for consumer(): a typo'd or stale field name in the 'fields' list fails silently, producing an incomplete entity with no signal.; No natural home for entity-level keys that are not property references at all — schema: json, supported_color_modes, and state_value_template have no corresponding property, so a second untyped passthrough would still be needed alongside the field-list mechanism.; Which property is 'primary' is an arbitrary, undiscoverable authoring choice — nothing in the schema signals it, unlike a model-level block that is unambiguously the whole model's concern.

## Decision Matrix

| Criterion | Model-level ha_entities()/ha_entity() + channel-grouping merge + light/climate/cover builders | Decorator-level ha_entities kwarg threaded through registration | Property-level 'primary field' marker referencing sibling field names |
| --- | --- | --- | --- |
| Consistency with existing schema-only extension architecture (no registration/decorator changes) | 5 | 2 | 4 |
| Correctly merges the ADR-055 state/set two-channel device pattern into one entity | 5 | 5 | 3 |
| Typo/drift safety of the entity-to-field association | 5 | 5 | 2 |
| Implementation blast radius (lower touch = higher score) | 4 | 2 | 4 |
| Open extensibility for HA component keys the curated builders do not anticipate | 5 | 4 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- wiz2mqtt's light entity becomes expressible in one model-level block plus per-field consumer()/openhab() annotations, instead of five scattered, misshapen scalar entities that HA rejects outright (Finding 10's reproduction).
- Finding 10 is fixed structurally rather than cosmetically: a component override now selects a real builder (schema: json default for light, invalid generic topics dropped for climate) instead of reusing the scalar six-key set under a different topic.
- extra stays an open, untyped passthrough consistent with ADR-056's 'back door beside the curated allowlist' precedent, so HA's per-component vocabulary keeps evolving without requiring a cosalette release.
- Closes Finding 20, the last of the three closing conditions cosalette-apps' cap-10u.6 gate names (alongside the already-shipped Findings 21/22 from ADR-056) other than runtime discovery (Finding 23, tracked separately as cos-0p16).

### Negative

- Two structurally distinct ways to write x-cosalette-ha-discovery now exist (field-level scalar overrides from ADR-056 vs model-level composite entities from this ADR); nothing stops an author writing conflicting or redundant metadata in both places for the same channel, and the reader disambiguates purely by where the block was found, not by any explicit marker.
- climate and cover receive only minimal component-aware treatment — drop-invalid-keys for climate, keep-as-is for cover — not full per-capability topic/template synthesis, so a working climate entity still requires the author to hand-supply every <x>_state_topic/<x>_state_template pair via extra.
- A channel that declares ha_entities forfeits its own per-property scalar HA generation entirely for that channel; a device that wants one composite entity plus one independent scalar sensor from the same payload model must split them into two models.
- OpenHAB gets no equivalent composite mechanism in this ADR — it remains per-property only, relying on ADR-056's channel_type/channel_params for native item types.

_2026-08-11_
