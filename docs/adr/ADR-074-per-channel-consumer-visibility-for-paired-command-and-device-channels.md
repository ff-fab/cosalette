---
status: Accepted
date: 2026-09-09
impact: moderate
tags: [architecture, telemetry, devices, cli]
---

# ADR-074: Per-channel consumer visibility for paired command and device channels

## Status

Accepted **Date:** 2026-09-09

## Context

ADR-073 gave authors `discoverable=False` on `@app.telemetry`/`@app.command`/`@app.device` to declare a channel intentionally not a Home Assistant / openHAB entity. The flag is per *registration*, but one registration can create two *channels*: a `@app.command` that declares both `payload_model=` and `state_model=` emits a `/set` command channel and a `/state` channel, and a `@app.device` that declares `payload_model=` emits a `/state` send channel and a `/set` receive channel. `discoverable=False` covers both alike.

The early adopter (cosalette-apps, nine bridges) hit the gap in wallpanel-control. Its `display` command declares `state_model=DisplayState` whose fields carry `consumer(read_only=True)`, so the state is deliberately surfaced as two read-only sensors while the command channel is intentionally not an entity — exactly the intent ADR-073 exists to express. But it cannot be expressed: setting `discoverable=False` on that registration removes all three entities (measured: 3 discovery topics before, 0 after, exit 0). The gate is satisfied by deleting the working entities. There is no `state_discoverable`, `command_discoverable`, or per-channel escape hatch anywhere in the package, so the app was left failing the gate rather than delete working entities or add unintended writable ones.

## Decision

Use `discoverable: bool | Literal["command", "state"]` on `@app.command`/`@app.device` (and their Router forms) so the opt-out can target a single channel by role, because a registration that emits paired `/set` and `/state` channels needs to keep one an entity while the other is not — and the literal resolves to a per-channel boolean at generation time, so the emitted `x-cosalette-discoverable` extension stays a plain boolean and the loader/round-trip contract from ADR-073 is unchanged.

```python
@app.command(
    "display",
    payload_model=DisplayCommand,
    state_model=DisplayState,   # fields carry consumer(read_only=True)
    discoverable="state",       # keep the /state entity, hide the /set command
)
async def display(payload: DisplayCommand) -> DisplayState: ...

# Generated channels (emitted only for the opt-out, so default docs are unchanged):
#   displayCommand:
#     x-cosalette-archetype: command
#     x-cosalette-discoverable: false   # /set command channel hidden
#   displayState:
#     x-cosalette-archetype: command    # /state channel stays discoverable
```

## Decision Drivers

- The case to support is a command channel that is not an entity whose paired state channel is (and the symmetric device case) — a single per-registration boolean cannot express it.
- The emitted schema contract from ADR-073 must not change: `x-cosalette-discoverable` must stay a plain per-channel boolean so existing loaders, round-trips, and the per-channel gate keep working unchanged.
- It must be additive: documents for the default `discoverable=True` case must stay byte-identical to pre-ADR-073 output.
- It must be author-controlled at the point of registration, discoverable via the decorator signature and IDE completion, consistent with how `discoverable=False` already reads.
- `@app.telemetry` emits a single channel, so it must stay a plain `bool` and not grow a meaningless per-channel literal.

## Considered Options

### Option 1: Literal on discoverable → per-channel boolean at generation (chosen) (chosen)

Widen `discoverable` to `bool | Literal["command", "state"]` on command and device (App and Router). Thread the spec through the registration record unchanged, and resolve it to a per-channel boolean in the AsyncAPI channel builder against the channel's role (`/set` = command, `/state` = state). The emitted extension and the loader stay boolean.

- *Advantages:* Names the channel by role, so it reads the same whether the primary channel is the command (command archetype) or the state (device archetype).; Zero schema-contract change: the document still carries only booleans, so the ADR-073 loader, round-trip, and per-channel gate need no changes.; Additive at the decorator: default and `discoverable=False` behaviour is untouched; only the two new string values are new surface.; Resolution lives in one place (`_resolve_channel_discoverable` in the channel builder); every call site passes the spec through unchanged.
- *Disadvantages:* Overloads one parameter's type with a union rather than a dedicated name, so the two channel roles must be documented on the decorator.; A literal on a command that never emits a `/state` channel silently hides the sole command channel; the footgun is documented rather than validated, since whether a `/state` channel is emitted depends on a return annotation resolved later.

### Option 2: Separate state_discoverable= keyword

Keep `discoverable: bool` for the primary channel and add a second `state_discoverable: bool | None = None` keyword (None inherits) covering the paired channel.

- *Advantages:* Each parameter stays a plain boolean — no union type to document.; Explicit about which channel each flag governs for the command case.
- *Disadvantages:* Adds a second parameter that is meaningless for telemetry and for single-channel commands, widening the signature further than the one-value literal.; "primary" vs "secondary" is archetype-dependent (command's primary is the /set channel, device's primary is the /state channel), so `state_discoverable` reads naturally for commands but awkwardly for devices.; Two booleans encode four states that one `bool | Literal` already expresses, inviting contradictory combinations to validate.

## Decision Matrix

| Criterion | Literal on discoverable → per-channel boolean at generation (chosen) | Separate state_discoverable= keyword |
| --- | --- | --- |
| Expresses the paired-channel case | 5 | 5 |
| Schema-contract stability (ADR-073 boolean) | 5 | 5 |
| API surface minimalism | 4 | 2 |
| Reads consistently across command and device | 5 | 2 |
| Backward compatibility of default output | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A command whose paired read-only state is an entity while the command is not — the wallpanel-control case — now has a correct, supported registration (`discoverable="state"`), and the symmetric device case is covered too.
- The generated document remains byte-identical for the default case and carries only booleans for opt-outs, so the ADR-073 loader, round-trip, and per-channel discovery gate are unchanged.
- The resolution is centralised in the channel builder, so the App and Router decorators only widen a type and pass it through.

### Negative

- `discoverable` now carries a union type on command and device that authors must understand (telemetry stays `bool`), a small asymmetry across the archetypes.
- A `"command"`/`"state"` literal on a registration that emits only one channel silently opts that channel out; the behaviour is documented rather than validated, because whether the second channel is emitted can depend on a return annotation resolved after registration.

_2026-09-09_
