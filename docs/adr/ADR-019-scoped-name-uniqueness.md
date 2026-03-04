# ADR-019: Scoped Name Uniqueness

## Status

Accepted **Date:** 2026-03-04

## Context

Cosalette enforced **globally unique names** across all registration types — devices,
telemetry, and commands. A single `_validate_name_unique()` check collected names from
all three registries into one `set[str]` and rejected duplicates regardless of
registration type.

This prevented telemetry and command registrations from sharing the same name, even
though ADR-002 explicitly defines a topic layout where both concerns share a single
device segment:

```text
{app}/{device}/state   ← telemetry (published)
{app}/{device}/set     ← commands  (subscribed)
```

The `@app.device` archetype already used a single name for both state publishing and
command handling, proving that the MQTT topic structure was designed for shared
namespaces. But using `@app.telemetry` and `@app.command` separately — which is
necessary for accessing telemetry-specific features like `PublishStrategy`,
`PersistPolicy`, and coalescing groups — forced workarounds like `cmd_` prefixes
that violated ADR-002's topic conventions.

In the vito2mqtt reference project, 4 out of 7 domain groups (57%) required both
telemetry and commands. The `cmd_` prefix workaround produced topics like
`vito2mqtt/.../cmd_hot_water/set` instead of the expected
`vito2mqtt/.../hot_water/set`.

## Decision

Scope name uniqueness **per registration type** instead of globally. The collision
matrix:

| Pair | Result | Rationale |
| ---- | ------ | --------- |
| device ↔ device | **REJECT** | Same MQTT topics would conflict |
| device ↔ telemetry | **REJECT** | Device archetype already publishes state |
| device ↔ command | **REJECT** | Device archetype already handles commands |
| telemetry ↔ telemetry | **REJECT** | Both would publish to the same `/state` topic |
| command ↔ command | **REJECT** | Both would subscribe to the same `/set` topic |
| telemetry ↔ command | **ALLOW** | Different MQTT suffixes (`/state` vs `/set`) — this is the ADR-002 design intent |

When a telemetry and command registration share the same name, the framework:

1. Creates a **shared `DeviceContext`** so both handlers publish availability and errors
   under the same device identity.
2. Publishes **one availability message** per shared name instead of duplicates.
3. Maintains separate handler execution — the telemetry handler runs in its polling loop
   and the command handler responds to incoming MQTT messages independently.

Device registrations remain globally unique — the `@app.device` archetype already
manages both state and commands under a single name, so collisions with any other
registration type are always rejected.

## Decision Drivers

- **ADR-002 compliance** — the MQTT topic layout was designed for telemetry and commands
  to share a device segment. The global uniqueness constraint contradicted this.
- **57% domain overlap** — more than half of vito2mqtt's domains needed both telemetry
  and commands, making this a mainstream use case, not an edge case.
- **Principle of least surprise** — `@app.device` already uses a single name for both
  concerns. Users would expect `@app.telemetry("x")` + `@app.command("x")` to work
  the same way when the archetype features require separate registrations.
- **Zero API surface change** — the solution required no new parameters, no new
  decorators, and no changes to existing user code.

## Considered Options

### Option A: `topic=` Parameter to Decouple Name from Topic

Add an optional `topic=` parameter to `add_telemetry()` and `add_command()` so that
the internal name (used for logging, keying, health) and the MQTT topic segment can
differ:

```python
app.add_telemetry(name="hot_water_telemetry", topic="hot_water", ...)
app.add_command(name="hot_water_command", topic="hot_water", ...)
```

- *Advantages:* Preserves global name uniqueness. Introduces an explicit topic override.
  Flexible for advanced use cases.
- *Disadvantages:* Adds API surface (new parameter). Creates a name/topic duality
  that complicates logging and debugging. Requires changes to `DeviceContext`,
  `TopicRouter`, and validation logic. Users must remember two identifiers per
  registration.

### Option B: Per-Type Uniqueness (Chosen)

Validate name uniqueness within each registration type rather than globally. Telemetry
and command registrations can share the same name; same-type duplicates and device
collisions are still rejected.

- *Advantages:* Zero API surface change. ADR-002 compliant. Name and topic remain
  identical — no duality. Shared `DeviceContext` enables clean availability and error
  semantics.
- *Disadvantages:* Shared `DeviceContext` semantics must be understood (mitigated:
  consistent with `@app.device` behavior). Root device constraints remain global
  (acceptable: root devices are a special case).

### Option C: `@app.domain()` Composite Registration

Introduce a new decorator that registers both telemetry and command handlers under a
single name with a unified configuration:

```python
@app.domain("hot_water", interval=30)
async def hot_water(ctx):
    # handles both polling and commands
    ...
```

- *Advantages:* Expressive API for the common case. Single registration point.
- *Disadvantages:* New API surface and new archetype concept. Unclear how to combine
  telemetry-specific features (publish strategy, coalescing groups) with command
  semantics. Significant design and implementation effort. Does not help with the
  majority of existing registrations that use the current three-archetype model.

## Decision Matrix

| Criterion | A: `topic=` Parameter | B: Per-Type Uniqueness | C: `@app.domain()` |
| --------- | :-------------------: | :--------------------: | :-----------------: |
| ADR-002 compliance | 5 | 5 | 5 |
| Zero API surface change | 2 | 5 | 1 |
| Implementation complexity | 3 | 4 | 2 |
| Name/topic clarity | 3 | 5 | 4 |
| Backward compatibility | 4 | 5 | 3 |
| Shared DeviceContext semantics | 3 | 4 | 5 |
| **Total** | **20** | **28** | **20** |

*Scale: 1 (poor) to 5 (excellent)*

Option B scores highest because it achieves ADR-002 compliance with zero API surface
change and minimal implementation complexity. The name remains the topic segment, so
there is no duality to manage.

## Consequences

### Positive

- Telemetry and command registrations can share the same name, enabling ADR-002
  compliant MQTT topic namespaces without workarounds
- Zero API surface change — no new parameters, decorators, or concepts
- Shared `DeviceContext` means availability and error topics are unified per device,
  matching the natural MQTT device model
- Existing applications with globally unique names continue to work identically
- The `cmd_` prefix workaround in vito2mqtt can be removed

### Negative

- Shared `DeviceContext` semantics require understanding — developers must know that
  a telemetry and command handler sharing the same name also share device availability
  and error context (mitigated: consistent with `@app.device` behavior)
- Root device (unnamed) constraints remain global — only one unnamed registration of
  any type is permitted (acceptable: root devices are a distinct, rare use case)
- Device archetype collisions are still globally rejected — `@app.device("x")` cannot
  coexist with `@app.telemetry("x")` or `@app.command("x")` (by design: the device
  archetype already handles both concerns)

_2026-03-04_
