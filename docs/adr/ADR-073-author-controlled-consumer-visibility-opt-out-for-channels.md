---
status: Accepted
date: 2026-09-09
impact: moderate
tags: [architecture, telemetry, cli, devices]
---

# ADR-073: Author-controlled consumer-visibility opt-out for channels

## Status

Accepted **Date:** 2026-09-09

## Context

`schema ha-discovery` / `schema openhab` generate Home Assistant and openHAB entities from every *consumer-visible* channel. `_is_consumer_visible` (`_schema/_consumer_gen.py`) had exactly two exclusions: `scope == "all_apps"` (framework-internal cross-app channels) and `archetype == "stream"` (ADR-054). Neither is author-controllable for ordinary telemetry, command, or device channels.

ADR-059 introduced a discovery gate that fails `ha-discovery`/`openhab` when a registry has consumer-visible channels but produces no entities, and its decision drivers promised the feature stays "strictly opt-in — apps that don't target Home Assistant … must see zero behaviour change on upgrade." That guarantee held for the *runtime* discovery path but not for the CLI gate. Once that gate is evaluated per channel (the companion bug fix), a legitimately non-HA telemetry channel — a diagnostic counter, an internal event stream published as telemetry — fails the gate with no supported way to say "this channel is intentionally not a consumer entity."

The early adopter (cosalette-apps, nine bridges) hit this with a calendar bridge whose `events[]` payload is deliberately surfaced via a channel-level `ha_entities()` composite rather than per-property entities, leaving sibling channels with nothing to emit. Today their only escapes are: add a meaningless annotation, restructure the app to `@app.stream` (relocating the topic and requiring a `StreamablePort`, ADR-042/045), or hand-edit `x-cosalette-archetype: stream` into the generated document — which is erased on the next `schema dump` by design, because generated fields are sourced from the App registry, not hand-authored.

## Decision

Use a `discoverable: bool = True` keyword argument on `@app.telemetry`/`@app.command`/`@app.device` (and their Router forms) that flows into a generated `x-cosalette-discoverable` channel extension, because it keeps the opt-out author-controlled *and* durable across regeneration — consistent with how `x-cosalette-archetype`/`x-cosalette-app` are already sourced from the registry rather than hand-added.

```python
@app.telemetry("diagnostics", interval=60, discoverable=False)
async def diagnostics() -> dict:
    return {"loop_lag_ms": measure()}

# Generated channel dict (emitted only when False, so default docs are unchanged):
#   diagnosticsState:
#     x-cosalette-archetype: telemetry
#     x-cosalette-discoverable: false
#
# `schema ha-discovery` / `schema openhab` skip the channel and the per-channel
# discovery gate no longer reports it.
```

## Decision Drivers

- The opt-out must survive `schema dump`/`schema init` regeneration — a hand-added field on a generated document is stripped on the next round-trip (the `x-cosalette-archetype: stream` escape hatch fails exactly here).
- It must be author-controlled at the point of registration, where the author already knows the channel is not a Home Assistant / openHAB entity.
- It must be additive: documents for the default `discoverable=True` case must stay byte-identical to pre-ADR-073 output so existing deployments regenerate unchanged.
- It must couple cleanly with the per-channel discovery gate so intentional omissions do not become CI failures.
- It should reuse the existing consumer-visibility predicate rather than add a parallel exclusion path.

## Considered Options

### Option 1: Registration flag → generated channel extension (chosen)

Add a keyword-only `discoverable: bool = True` to the telemetry/command/device decorators. Thread it onto the registration record and emit `x-cosalette-discoverable: false` on the generated channel dict when False; the loader parses it back into `ChannelSchema.discoverable`, and `_is_consumer_visible` returns False for it.

- *Advantages:* Durable: the flag is sourced from the App registry, so it survives regeneration like every other generated field — the exact property the archetype escape hatch lacked.; Author-controlled at the natural point (the registration) and discoverable via decorator signature + IDE completion.; Additive emission (only when False) keeps default documents byte-identical, honouring ADR-059's zero-behaviour-change promise.; Reuses the single `_is_consumer_visible` predicate; no parallel gate logic.
- *Disadvantages:* Widest code surface: the flag threads through both App and Router decorators, their registration builders, and the AsyncAPI channel-dict assembly.; Adds one more field to the channel-extension vocabulary the loader must validate.

### Option 2: Hand-authored per-channel schema extension key

Document a model-level or channel-level extension (e.g. `x-cosalette-consumer: {exclude: true}`) that authors add directly to the generated AsyncAPI document, without touching the registration API or the archetype enum.

- *Advantages:* Small framework surface — only the loader and `_is_consumer_visible` change.; No change to the public decorator signatures.
- *Disadvantages:* Not durable: the field lives on a *generated* document and is stripped on the next `schema dump`/`schema init`, reproducing the exact fragility of the `x-cosalette-archetype: stream` workaround.; Splits the source of truth — a channel's consumer visibility would be hand-authored while its archetype/app/scope are generated.; Invites drift between the registration and the document.

### Option 3: CLI-level allowlist flag

Add a `--exclude-channel` (repeatable) option to `schema ha-discovery`/`openhab` naming channels to skip, kept in the CI invocation rather than the app.

- *Advantages:* Cheapest to implement — no schema, loader, or decorator changes.; Entirely inert until used.
- *Disadvantages:* Pushes channel knowledge out of the app and into every CI invocation and every consumer of the document.; Not self-describing: a reader of the schema cannot tell the channel is intentionally non-consumer.; Duplicated across the two generators and any future consumer target; easy to forget for one command.

## Decision Matrix

| Criterion | Registration flag → generated channel extension | Hand-authored per-channel schema extension key | CLI-level allowlist flag |
| --- | --- | --- | --- |
| Durability across regeneration | 5 | 1 | 3 |
| Author ergonomics / discoverability | 5 | 2 | 2 |
| Single source of truth | 5 | 2 | 2 |
| Implementation surface (higher = smaller) | 2 | 4 | 5 |
| Backward compatibility of generated output | 5 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Authors have a supported, durable way to declare a channel intentionally non-consumer and get exit 0 from the discovery gate.
- The per-channel discovery gate can be tightened (reporting each silent channel) without turning intentional omissions into CI failures.
- Generated documents for the default case are byte-identical to pre-ADR-073 output; existing deployments regenerate unchanged.
- Consumer visibility stays a single generated field, consistent with `x-cosalette-archetype`/`x-cosalette-app`.

### Negative

- `discoverable=` threads through both App and Router decorators and the AsyncAPI channel-dict builders, widening the registration API surface that must be maintained and tested.
- The channel-extension vocabulary and loader validation grow by one key (`x-cosalette-discoverable`).
- `@app.stream` deliberately does not accept the flag (streams are already non-consumer-visible), a small asymmetry across the archetypes.

_2026-09-09_
