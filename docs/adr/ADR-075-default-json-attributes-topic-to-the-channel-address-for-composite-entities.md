---
status: Accepted
date: 2026-09-09
impact: moderate
tags: [architecture, telemetry, cli]
---

# ADR-075: Default json_attributes_topic to the channel address for composite entities

## Status

Accepted **Date:** 2026-09-09

## Context

A channel-level `ha_entities()` composite (ADR-057) builds one Home Assistant entity from a whole payload model, with an open `extra` passthrough merged last. The recommended shape for a list payload — the calendar bridge's `CalendarState.events: list[CalendarEvent]` — is a composite that surfaces a count via `value_template: "{{ value_json.events | length }}"`. But the composite cannot expose the *contents* of the list. Home Assistant reads extra attributes from a separate `json_attributes_topic`, and ignores a `json_attributes_template` when no topic is set. cosalette emits no `json_attributes_topic`, and a template passed through `extra` is emitted verbatim and therefore inert (confirmed by the adopter). There is also no placeholder for a channel's generated address, so a model-level spec shared by every calendar (channels generated from a callable `name=`) cannot name a per-calendar attributes topic — hard-coding one would be correct for `birthday` and wrong for `garbage`. So a list payload can expose a count but not its contents.

## Decision

Default `json_attributes_topic` to the composite entity's own state topic (the resolved `config["state_topic"]` after `extra` is merged) when the composite sets `json_attributes_template` and does not set `json_attributes_topic`, because the channel already publishes the full payload to that topic and the default resolves per channel — so a model-level template shared by callable-named channels names each channel's own topic — while an explicit `json_attributes_topic` in `extra` still wins and a composite with no state topic (command-only, or one whose builder drops it) is left untouched.

```python
ha_entity(
    component="sensor",
    name="birthday",
    extra={
        "value_template": "{{ value_json.events | length }}",
        # HA requires json_attributes_template to render a JSON OBJECT, not a
        # bare array, so the list is wrapped under a key:
        "json_attributes_template": "{{ {'events': value_json.events} | tojson }}",
    },
)
# Generated config gains, resolved from the channel address:
#   "state_topic": "caldates2mqtt/birthday/state",
#   "json_attributes_topic": "caldates2mqtt/birthday/state"
```

## Decision Drivers

- The list payload is already published to the channel's state topic, so that topic is the natural, correct source for the attributes template — no new wire topic is introduced.
- Channels generated from a callable `name=` share one model-level spec, so the default must resolve per channel; a hard-coded topic would be right for one channel and wrong for its siblings.
- It must be additive: an entity that sets no `json_attributes_template` must produce byte-identical output, and an author who sets `json_attributes_topic` (or redirects `state_topic`) via `extra` must keep the topics consistent.
- It should reuse the composite's existing state-topic resolution rather than introduce a placeholder/interpolation mini-language into `extra` values.

## Considered Options

### Option 1: Default json_attributes_topic to the resolved state topic (chosen)

In the composite payload builder, after `extra` is merged, set `json_attributes_topic` to `config["state_topic"]` when `json_attributes_template` is present and `json_attributes_topic` is absent and a state topic exists. Reading the post-`extra` value keeps it consistent with an author who redirects `state_topic`.

- *Advantages:* Smallest change that covers the reported case: the channel address is already computed, so the default is a short guard.; Resolves per channel automatically, so a model-level template shared by callable-named channels is correct for every channel with no placeholder syntax.; Additive and non-surprising: no template means no change; an explicit topic is preserved; a composite with no state topic is untouched.; Reads the post-`extra` state topic, so it honours the same extra-wins-last invariant as every other computed field, and runs before the enrichment hook so `app.discovery(enrich=...)` retains the final word.
- *Disadvantages:* Only covers the composite path; a scalar per-property entity that sets `json_attributes_template` via `ha_discovery(extra=...)` is not defaulted (scalar sensors rarely carry attribute payloads).; Couples the attributes topic to the state topic; an author who wants attributes from a different topic must set `json_attributes_topic` explicitly (which is supported).

### Option 2: Author-resolved placeholder in extra values

Introduce a placeholder token (e.g. `{channelAddress}`) that the generator substitutes inside `extra` string values, so an author writes `json_attributes_topic: "{channelAddress}"` (or any other topic) themselves.

- *Advantages:* General: lets `extra` reference the generated address for any key, not just `json_attributes_topic`.; Fully explicit — the author states the topic rather than relying on a default.
- *Disadvantages:* Adds a substitution mini-language to `extra`, a new surface to specify, escape, validate, and test against Jinja/HA templating that also lives in those strings.; More boilerplate for the common case: every composite that wants attributes must spell out the placeholder, versus getting the right topic for free.; Larger blast radius — placeholder substitution touches every `extra` value, not just the one key the reported case needs.

## Decision Matrix

| Criterion | Default json_attributes_topic to the resolved state topic | Author-resolved placeholder in extra values |
| --- | --- | --- |
| Covers the reported list-payload case | 5 | 5 |
| Per-channel correctness for callable name= | 5 | 4 |
| Implementation surface (higher = smaller) | 5 | 2 |
| Backward compatibility of default output | 5 | 5 |
| Author ergonomics for the common case | 5 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A list or object payload can finally expose its contents as Home Assistant attributes through a composite, not just a scalar count.
- A model-level `json_attributes_template` shared by callable-named (`name=`) channels names each channel's own topic automatically, so per-calendar attributes work with one spec.
- The change is additive: composites that set no template, and any explicit `json_attributes_topic`, produce identical output.

### Negative

- The default couples the attributes topic to the state topic; a different attributes topic requires an explicit `json_attributes_topic` in `extra`.
- The default applies only to the composite path, so a scalar per-property entity setting `json_attributes_template` via `ha_discovery(extra=...)` remains inert — a small asymmetry documented rather than closed.
- cosalette cannot validate that the author's `json_attributes_template` renders a JSON object (HA's requirement); a bad template is silently dropped by Home Assistant, so the constraint is documented in guidance rather than enforced.

_2026-09-09_
