---
status: Accepted
date: 2026-09-10
impact: moderate
tags: [architecture, mqtt, serialization, telemetry, cli]
---

# ADR-076: Typed value aggregates on array-valued properties for cross-target consumer parity

## Status

Accepted **Date:** 2026-09-10

## Context

An array-valued property has no single value for a per-property discovery entity to carry. That gap — not the Home Assistant-only composite it pushes authors toward — is the root cause of the adopter's openHAB build failure. The early adopter (github.com/ff-fab/cosalette-apps, nine bridges) filed a proposal against cosalette 0.9.5 titled "openHAB generation ignores channel-level `ha_entities()` composites", reporting that `cosalette schema ha-discovery` succeeds and `cosalette schema openhab` exits 1 for the same calendar-bridge channel. The headline framing — that the two renderers have *diverged* and that this is a bug — is rejected: openHAB ignoring channel-level composites is explicit, documented and test-pinned (ADR-057 Decision: "OpenHAB generation is unchanged: composite mapping is HA-only in this ADR", plus `test_ha_entities_only_channel_is_silent_for_openhab`), and ADR-059's gate failing over an empty openHAB document is exactly what that gate exists to do.

The underlying need is nonetheless real, and tracing it back one step changes what the fix should be. The adopter's channel carries a single top-level property `events: list[Event]`. The discovery gate's real complaint is not about composites at all — it is that an **array-valued property has no single value** for a per-property entity to carry. That absence is what pushed the author toward a channel-level `ha_entities()` composite in the first place; composites are Home Assistant-only by ADR-057; and that is what broke their `schema openhab` build. Composites are the symptom. The missing single value is the root cause.

A further premise settles the option space. **openHAB is a first-class deployment target for cosalette-apps**, confirmed by the maintainer. Any answer that tiers openHAB down — an opt-out that lets an author declare the channel "Home Assistant only" — trades a capability gap for a silent deployment gap, which is the failure mode ADR-059 was created to eliminate.

The framework already has per-property machinery that *both* generators implement: `_derive_value_template` (`_consumer_gen.py`:449-465) builds `{{ value_json.<path> }}` for Home Assistant, and `_channel_entries` (`_consumer_gen.py`:1196) builds `transformationPattern="JSONPATH:$.<path>"` for openHAB from the same `PropertySchema.path`. Give an array-valued property a single value and it flows through that existing machinery on both sides, from one declaration.

What openHAB can actually execute was verified against the add-on sources rather than its documentation, because the JSONPATH add-on README is stale and wrong (it claims the transform returns the original JSON on no match and "will never return null"; the current code does neither — do not design against that text):

- **Aggregate functions exist and are executable.** The JSONPATH service uses Jayway `com.jayway.jsonpath` (2.5.0 on openHAB 4.0/4.1, 2.9.0 on 4.2/4.3/5.0, 2.10.0 on main). Jayway has provided `length()`, returning an Integer, since 2.1, alongside `min()`, `max()`, `avg()` and `sum()`. `size()` is **not** a Jayway function — only a filter operator — so the emitted form must be `length()`.
- **The transform passes function syntax through intact.** `JSonPathTransformationService` performs zero validation: it calls `JsonPath.read(source, expr)` and `.toString()`s any non-`List` result. Only a multi-element non-scalar `List` result is rejected.
- **The value lands cleanly as a number.** `NumberValue` parses `"2"` into a `DecimalType`. Leaving `unit` unset for a plain count keeps it `DecimalType` rather than a `QuantityType`.
- **No new install burden.** *Nothing* transformation-related ships by default in openHAB 3.x/4.x/5.x — `featuresBoot` carries no `openhab-transformation-*` feature and stock `addons.cfg` has every list commented out. But `openhab-transformation-jsonpath` is **already a hard prerequisite for every channel cosalette generates today**, so aggregates add zero incremental burden beyond cosalette's existing JSONPATH baseline.
- **Syntax form is version-constrained.** The emitted form must be the colon form `TYPE:FUNCTION` (`JSONPATH:$.events.length()`). The newer `TYPE(FUNCTION)` form only parses on openHAB 4.3+, where the parenthesis regex landed; on 4.2 and earlier `TransformationStep` does a naive `indexOf(":")` and throws. The colon form works on every supported version and stays safe even when the expression ends in `)`. Chaining, if ever needed, uses `∩` (U+2229 INTERSECTION), not the `List<String>` config form, which is likewise 4.3+.
- **Failure semantics differ from Home Assistant and must be documented.** A missing key raises Jayway `PathNotFoundException` → `TransformationException` → `ChannelState.processMessage` logs "discarding message" and returns **without updating the Item**. It does not set `UNDEF`, so the Item silently retains its previous value. An empty array is fine: `length()` returns 0.

The verbatim escape hatch already exists and needs no code: `OpenHabOverrides.channel_params` (`_schema/__init__.py`:164-172) is an open `dict[str, Any]` passthrough merged last in `_channel_entries` (`_consumer_gen.py`:1226-1228), keyed by parameter name, so `openhab(channel_params={"transformationPattern": "JSONPATH:$.events.length()"})` overrides the derived value in place today. `_escape_openhab_string` touches only `\`, `"` and newline, so `$ . [ ] ( )` all pass through unharmed. It is an escape hatch, not a mechanism: openHAB-only, unvalidatable, and the framework emits a string it cannot reason about.

## Decision

Use a **typed aggregate declaration on array-valued properties** — an author declares `consumer(aggregate="count")` (with `min`, `max`, `avg` and `sum` as the natural family from the same enum) and each generator renders it in its own target vocabulary from that single declaration — because the discovery gate's real complaint is that an array-valued property has no single value, and supplying that value routes the property through the *existing* per-property machinery both generators already implement. openHAB renders `transformationPattern="JSONPATH:$.<path>.length()"`; Home Assistant renders `value_template: {{ value_json.<path> | length }}`. Cross-target parity is then a property of the construction rather than a promise to be maintained, the framework keeps full control of escaping and validation because it emits both strings itself, and the root cause is dissolved instead of the symptom being routed around.

```python
# Author declaration — one aggregate, no target named
class CalendarPayload(BaseModel):
    events: list[Event] = Field(
        json_schema_extra=consumer(
            name="Upcoming events",
            aggregate="count",   # count | min | max | avg | sum
        )
    )

# Rendered by HaDiscoveryGenerator (per-property path, unchanged machinery):
#   value_template: "{{ value_json.events | length }}"
#
# Rendered by OpenHabGenerator (colon form — parses on 4.0 through 5.0):
#   Number upcoming_events { channel="..." }
#   transformationPattern="JSONPATH:$.events.length()"
#
# Jayway resolves length() natively; JSonPathTransformationService does not
# validate the expression, and NumberValue parses "2" -> DecimalType.
# `unit` is left unset so the Item stays DecimalType, not QuantityType.
```

## Decision Drivers

- openHAB is a first-class deployment target for cosalette-apps (maintainer premise), so any answer that tiers it down or lets a channel silently vanish from an openHAB deployment is disqualified — that is the failure mode ADR-059 exists to prevent.
- The root cause is that an array-valued property has no single value; composites are the symptom the author reached for. A fix that supplies the missing value dissolves the cause, one that projects composites across targets only moves the symptom.
- Both generators already implement per-property rendering from the same `PropertySchema.path` (`_derive_value_template` for Jinja, `_channel_entries` for JSONPATH). Reusing that path buys cross-target parity by construction, from one declaration, at minimal blast radius.
- The framework must be able to validate and escape what it emits. A closed enum of aggregate functions is checkable at declaration time; an author-supplied transform string is not.
- Documents for apps that do not use an aggregate must stay byte-identical to today's output — the same additive guarantee ADR-073 and ADR-074 made.
- The emitted openHAB syntax must execute on every supported openHAB version (4.0 through 5.0), which constrains the form to the colon `TYPE:FUNCTION` variant and the function name to Jayway's `length()`.
- ADR-056 established separate, target-native producer vocabularies rather than one target's dialect leaking into the other; whatever is chosen must not make openHAB inherit Home Assistant's template engine.

## Considered Options

### Option 1: Typed aggregate rendered per target (chosen)

An array-valued property carries a typed aggregate declaration from a closed enum (`count` primarily; `min`/`max`/`avg`/`sum` as the natural family). The declaration names no target. Each generator renders it in its own vocabulary from that single declaration: openHAB emits `transformationPattern="JSONPATH:$.<path>.length()"` in the colon form, Home Assistant emits `value_template: {{ value_json.<path> | length }}`. The property now has a single value, so it flows through the per-property machinery both generators already implement rather than needing a new channel-level path on either side.

- *Advantages:* Dissolves the root cause: the array-valued property gains a single value, which is precisely what the discovery gate was missing.; Cross-target parity is a property of the construction — one declaration, two renderings — not a portability promise that has to be maintained against two evolving template engines.; The framework emits both strings itself, so escaping and validation stay entirely under its control; the enum is closed and checkable at declaration time.; Reuses the existing per-property pipeline on both sides; the openHAB `JSONPATH:` prefix is hardcoded at exactly one site (`_consumer_gen.py`:1218) and Home Assistant's derivation at one more (`_derive_value_template`:449-465).; Executable on every supported openHAB version: Jayway has shipped `length()` since 2.1, `JSonPathTransformationService` does not validate the expression, and the colon form parses on 4.0 through 5.0.; Adds no install burden — `openhab-transformation-jsonpath` is already a hard prerequisite for every channel cosalette generates.; Purely additive: documents with no aggregate render byte-identically to today.; The author writes one short, target-neutral declaration instead of learning either target's expression language.
- *Disadvantages:* Covers aggregates only. A property needing an arbitrary derived value still has no first-class answer, and the escape hatch remains `channel_params`.; Adds a new key to the `consumer()` producer surface and its reader-side dataclass, with a drift-guard test to keep them aligned.; openHAB's failure semantics for a missing key differ from Home Assistant's — the Item silently retains its previous value rather than going `UNDEF` — so authors must be warned in the docs; the framework cannot fix this from its side.; `min`/`max`/`avg`/`sum` are only meaningful over arrays of numbers, so the enum admits declarations that are type-invalid for the property they sit on unless validation catches them.

### Option 2: Structural JSONPATH only

Change nothing about the declaration surface: let the framework keep deriving the whole accessor structurally from the property path, and accept that array-valued properties simply cannot be rendered as per-property entities on either target.

- *Advantages:* Zero new API surface and zero implementation cost.; The framework retains complete control over every string it emits, since nothing is author-supplied.; Existing generated output is trivially unchanged.
- *Disadvantages:* Cannot express an aggregate at all — this is the disqualifying defect. `_path_segments_to_accessor` (`_consumer_gen.py`:63-82) projects a path tuple to `$.a.b` or `$['a.b']` and has no vocabulary for a trailing function call; a segment named `length()` fails `_IDENTIFIER_RE` and is emitted as `$['length()']`, which Jayway resolves as a literal key and not a function.; Leaves the adopter's channel exactly where it started: unrenderable for openHAB, and therefore still failing the ADR-059 gate.; Keeps pushing authors toward channel-level composites, which are Home Assistant-only, which is the loop that produced this ADR.

### Option 3: Verbatim openHAB transform via channel_params

Treat `OpenHabOverrides.channel_params` as the answer: the author writes the openHAB transformation string themselves and the framework passes it through untouched. This works today with no code change — `channel_params` (`_schema/__init__.py`:164-172) is an open dict merged last in `_channel_entries` (`_consumer_gen.py`:1226-1228), keyed by parameter name, so `openhab(channel_params={"transformationPattern": "JSONPATH:$.events.length()"})` overrides the derived value in place, and `_escape_openhab_string` touches only backslash, double quote and newline, leaving `$ . [ ] ( )` intact.

- *Advantages:* Already exists and already works — zero code change, available to any author today.; Maximally expressive on the openHAB side: any transformation the target can execute can be written.; Fully consistent with the ADR-056 'curated front door plus an open back door' principle, which is what `channel_params` was added for.
- *Disadvantages:* openHAB-only. It gives Home Assistant nothing, so a single declaration cannot serve both targets and the parity problem is untouched.; Unvalidatable: the framework emits a string it cannot reason about, cannot check against the property's type, and cannot keep correct across openHAB versions.; Pushes target-specific expression syntax into application code, which is exactly the coupling ADR-056's typed producers were introduced to avoid.; As a primary mechanism it makes the common case (a count) as expensive as the rare case.

### Option 4: Project composites into openHAB

Teach `OpenHabGenerator` to read channel-level `ha_entities()` composites and render them as openHAB Items, translating the composite's Jinja `value_template` into an openHAB-executable transformation (the adopter's Option A).

- *Advantages:* One author declaration nominally serves both targets, with no new surface to learn.; Directly addresses the adopter's filed complaint in the terms they filed it.
- *Disadvantages:* Its premise is false: the composite does not 'already carry everything openHAB needs'. openHAB's MQTT binding has no Jinja engine, so a `value_template` is not executable there.; Guaranteeing semantic equivalence across two template engines is a portability promise that breaks on the first Home Assistant-specific filter, and every subsequent HA release can break it again.; openHAB does ship a JINJA transformation add-on whose `JinjaTransformationService` binds a parsed `value_json` deliberately mirroring Home Assistant — but it is HubSpot Jinjava, not Python Jinja2, it is a separate add-on beyond cosalette's JSONPATH baseline, and its own documentation states that not all features of the Home Assistant templating engine are supported. It narrows the gap; it does not close it.; Contradicts ADR-057, which makes a composite *replace* per-property generation for its channel: projecting it into openHAB would delete the per-property Items that existing composite users get today.; Largest blast radius of any option, across the loader, both generators and the shared gate.

### Option 5: Tier openHAB down with per-target opt-out

Accept that channel-level entity expression is Home Assistant-only, and add a per-target `discoverable` axis so an author can declare a channel visible to Home Assistant and not to openHAB, silencing the ADR-059 gate for that channel (the adopter's Option C in spirit, and the `discoverable` extension tracked as follow-on work).

- *Advantages:* Smallest capability surface — expresses intent rather than adding a rendering mechanism.; Composes with the existing ADR-073/ADR-074 `discoverable` vocabulary the author already knows.; Would still be useful on its own terms if a genuinely Home-Assistant-only channel need ever appeared.
- *Disadvantages:* Contradicts the maintainer's premise that openHAB is a first-class deployment target: it makes a capability gap permanent by making it declarable.; An opt-out lets authors silently drop channels out of an openHAB deployment — the exact silent-failure mode ADR-059 was created to eliminate, re-entering through the front door.; Satisfies the gate by removing entities rather than by producing them; the openHAB user still has nothing.; Does not help the adopter, whose calendar bridge genuinely wants the event count in openHAB.

## Decision Matrix

| Criterion | Typed aggregate rendered per target | Structural JSONPATH only | Verbatim openHAB transform via channel_params | Project composites into openHAB | Tier openHAB down with per-target opt-out |
| --- | --- | --- | --- | --- | --- |
| openHAB executability (the target can actually run what is emitted) | 5 | 1 | 5 | 1 | 2 |
| Cross-target parity from a single declaration | 5 | 2 | 1 | 3 | 1 |
| Framework can validate and escape what it emits | 5 | 4 | 1 | 2 | 4 |
| Backward compatibility of existing generated output | 5 | 5 | 5 | 2 | 4 |
| Consistency with ADR-056/ADR-057 target-native vocabularies | 4 | 3 | 4 | 1 | 2 |
| Implementation blast radius (5 = smallest) | 4 | 5 | 5 | 1 | 3 |
| Author ergonomics | 5 | 1 | 2 | 4 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The adopter's calendar bridge becomes expressible on both targets from one declaration: `events: list[Event]` with `aggregate="count"` yields a Home Assistant sensor and an openHAB `Number` Item, and the channel stops failing the ADR-059 gate under `schema openhab` — without any channel being hidden from a target.
- Cross-target parity holds by construction. Both renderings are derived by the framework from the same `PropertySchema.path` and the same aggregate token, so the two generators cannot drift on this feature without the shared derivation changing.
- The framework never emits a string it cannot reason about: the aggregate vocabulary is a closed enum, so escaping, version-safe syntax selection and type checking all stay on cosalette's side of the boundary.
- No new openHAB add-on is required. `openhab-transformation-jsonpath` is already a hard prerequisite for every channel cosalette generates, so aggregates cost nothing beyond that existing baseline — and the emitted colon form `JSONPATH:$.<path>.length()` executes unchanged on openHAB 4.0 through 5.0.
- Purely additive. Documents for apps that declare no aggregate are byte-identical to today's output, preserving the guarantee ADR-073 and ADR-074 also made.
- The pressure that drove authors toward Home-Assistant-only composites for the array-of-objects case is relieved at its source, so ADR-057's negative consequence (composites being HA-only) stops being a cross-target trap for the most common shape that hit it.
- openHAB's first-class status is preserved without a per-target opt-out, so ADR-059's guarantee — that a target's empty output is never silently accepted — remains intact and un-eroded.

### Negative

- openHAB's failure semantics for a missing key differ from Home Assistant's and cannot be fixed from cosalette's side: Jayway raises `PathNotFoundException`, `ChannelState.processMessage` logs "discarding message" and returns without updating the Item, so the Item silently retains its previous value rather than going `UNDEF`. An empty array is fine (`length()` returns 0), but the stale-value behaviour must be documented for authors as a target-level caveat.
- The aggregate vocabulary is deliberately narrow. Anything beyond `count`/`min`/`max`/`avg`/`sum` still has no first-class expression, and such authors fall back to the openHAB-only `channel_params` escape hatch or a Home-Assistant-only composite — the very split this ADR narrows but does not eliminate.
- `min`/`max`/`avg`/`sum` are meaningful only over arrays of numbers, so the enum permits declarations that are type-invalid for the property carrying them; validation must reject those or authors will meet the error at deployment time rather than at generation time.
- The emitted openHAB syntax is version-constrained in a way that is invisible in the declaration: only the colon form `TYPE:FUNCTION` parses on openHAB 4.2 and earlier, because `TransformationStep` there does a naive `indexOf(":")`. The generator must never migrate to the newer `TYPE(FUNCTION)` form (or the `List<String>` chaining config) without dropping support for those versions.
- The `consumer()` producer surface and its reader-side dataclass both grow a key, adding one more pair to keep aligned under the existing drift-guard test convention.
- PR #447's new gate diagnostic will become incomplete once aggregates land: it currently advises per-property `consumer()` annotations and the all-or-nothing `discoverable=False`, and will need to name an aggregate as the right answer for array-of-objects properties.

_2026-09-10_
