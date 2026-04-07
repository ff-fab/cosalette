---
status: Accepted
date: 2026-04-06
impact: moderate
tags: [devices, lifecycle]
---

# ADR-031: Sub-Entity Context Manager

## Status

Accepted **Date:** 2026-04-06

## Context

cosalette devices currently publish telemetry and availability at the device level.
Some devices need temporary sub-components that appear and disappear at runtime.
Examples:

- a `calibrate` sub-entity that is online only during a calibration procedure, publishes
  step progress, and accepts calibration commands.
- a special function sub-entity active only during specific device lifecycle stages.
- per-sensor staleness tracking where individual sensors may go offline while the device
  remains healthy.

Today there is no framework support for sub-entity lifecycle. Developers must
manually publish availability and state to sub-entity topics, manage cleanup on exit,
and wire up command handlers — duplicating the patterns the framework already provides
at the device level.

### Sub-topic routing is available

ADR-025 (Command Channel and Sub-Topic Routing) introduced `ctx.on_command("sub")`
and a `TopicRouter` that subscribes to `{device}/+/set` wildcards. Sub-entity command
support can delegate directly to this existing infrastructure — no new routing code is
needed.

### Topic conventions are established

ADR-002 (MQTT Topic Conventions) defines the device topic layout:

```text
{app}/{device}/state
{app}/{device}/set
{app}/{device}/availability
```

The natural extension for sub-entities adds one level, following ADR-025's precedent:

```text
{app}/{device}/{sub}/state
{app}/{device}/{sub}/set
{app}/{device}/{sub}/availability
```

## Decision

Add a `sub_entity(name)` async context manager to `DeviceContext` that manages
sub-entity lifecycle — availability publishing, scoped state publishing, and command
handler registration.

### SubEntityContext

A lightweight context object yielded by the context manager:

```python
class SubEntityContext:
    name: str
    parent: DeviceContext

    async def publish_state(self, payload: dict, *, retain: bool = True) -> None:
        """Publish to {parent_topic_base}/{name}/state."""

    def on_command(self, handler: CommandHandler) -> CommandHandler:
        """Register a command handler on {parent_topic_base}/{name}/set.

        Delegates to parent.on_command(self.name).
        """
```

`SubEntityContext` mirrors a subset of `DeviceContext`'s API — `publish_state()` and
`on_command()` — scoped to the sub-entity's topic level.

### Context manager lifecycle

```python
@asynccontextmanager
async def sub_entity(self, name: str) -> AsyncIterator[SubEntityContext]:
    self._validate_sub_entity_name(name)
    self._active_sub_entities.add(name)
    sub = SubEntityContext(name=name, parent=self)
    avail_topic = f"{self._topic_base}/{name}/availability"
    await self._mqtt.publish(avail_topic, "online", retain=True, qos=1)
    try:
        yield sub
    finally:
        # Clear retained state
        state_topic = f"{self._topic_base}/{name}/state"
        await self._mqtt.publish(state_topic, "", retain=True, qos=1)
        # Publish offline
        await self._mqtt.publish(avail_topic, "offline", retain=True, qos=1)
        self._active_sub_entities.discard(name)
```

On enter:

1. Validate the sub-entity name (see naming restrictions below).
2. Track the name in `_active_sub_entities: set[str]` on `DeviceContext`.
3. Publish `"online"` to `{device}/{sub}/availability` (retained, QoS 1).
4. Yield a `SubEntityContext`.

On exit (`__aexit__`):

1. Publish empty payload to `{device}/{sub}/state` to clear retained state.
2. Publish `"offline"` to `{device}/{sub}/availability` (retained, QoS 1).
3. Remove the name from `_active_sub_entities`.

### Command support via delegation

`SubEntityContext.on_command(handler)` delegates to
`self.parent.on_command(self.name)(handler)`, reusing the existing sub-topic
routing from ADR-025. The `TopicRouter` already subscribes to `{device}/+/set`
and dispatches by sub-topic — no additional router changes or subscriptions are
needed.

### Lifecycle scope: context manager only

Only the `async with` context manager API is provided. Persistent sub-entities
(always-online sub-components) are supported via a long-running `async with` block
inside `@app.device`:

```python
@app.device("cover")
async def cover(ctx):
    async with ctx.sub_entity("temperature") as temp:
        # temp is online for the entire device lifetime
        while not ctx.shutdown_requested:
            reading = await read_sensor()
            await temp.publish_state({"value": reading})
            await ctx.sleep(60)
```

This avoids a second API (`add_sub_entity()`) and its attendant shutdown-ordering
complexity. If demand for a dedicated persistent sub-entity API grows, it can be
added backward-compatibly in a future release.

### State retention: retain during lifecycle, clear on exit

Sub-entity state is published with `retain=True` by default (same as device state),
so Home Assistant sees the latest state even after reconnection. On `__aexit__`, the
retained state topic is cleared by publishing an empty payload — the MQTT-standard
deletion mechanism. This prevents stale state data from lingering in the broker after
the sub-entity goes offline.

### Naming restrictions

Sub-entity names are validated with the following rules:

1. **MQTT-safe:** no `/`, `+`, `#` characters (same validation as
   `_validate_sub_topic` in ADR-025).
2. **Not empty:** empty string raises `ValueError`.
3. **No concurrent duplicates:** a sub-entity name that is already active on the
   same `DeviceContext` raises `ValueError`. Tracked via `_active_sub_entities`.
4. **Reserved names rejected:** the following names are reserved because they would
   collide with existing or planned device-level topic suffixes:

   | Reserved name   | Reason                                    |
   | --------------- | ----------------------------------------- |
   | `state`         | Device state topic (ADR-002)              |
   | `set`           | Command topic suffix (ADR-002)            |
   | `availability`  | Device availability topic (ADR-012)       |
   | `status`        | App-level health topic (ADR-012)          |
   | `error`         | Error topic (ADR-011)                     |
   | `config`        | Future: HA MQTT discovery config topic    |
   | `attributes`    | Future: HA extra attributes topic         |
   | `json_attributes` | Future: HA JSON attributes topic        |
   | `diagnostic`    | Future: diagnostic data channel           |
   | `firmware`      | Future: OTA firmware update channel       |

5. **Same name as device:** allowed (topic path `{device}/{device}/state` is
   unambiguous), but logs a `WARNING` since it is likely a mistake.

### Sub-entity availability and HealthReporter

Sub-entity availability is published directly by the context manager via `MqttPort`,
**not** through `HealthReporter`. This is intentional:

- `HealthReporter` tracks device-level availability and app-level status. Sub-entity
  lifecycle is scoped and transient — adding tracking to `HealthReporter` would
  complicate its shutdown logic for marginal benefit.
- The context manager's `__aexit__` guarantees cleanup. If the process crashes, the
  device-level LWT (`availability = "offline"`) signals Home Assistant that the
  entire device (and by implication its sub-entities) is unavailable.

## Decision Drivers

- **Consistency:** sub-entity topics follow the same conventions as device topics,
  extended by one level.
- **Minimal API surface:** one context manager method, one yielded context type.
- **Leverage existing infrastructure:** command routing, topic conventions, and MQTT
  publishing are reused without modification.
- **Clean broker state:** retained messages are cleared on sub-entity exit.
- **Safety:** naming validation prevents topic collisions and concurrent duplicates.

## Considered Options

### Option A: Context manager only (chosen)

`async with ctx.sub_entity("name") as sub:` — scoped lifecycle, automatic cleanup.

**Pros:** simple, composable, guaranteed cleanup, small API surface.
**Cons:** permanent sub-entities require a long-running `async with` (a workaround,
not a first-class API).

### Option B: Context manager + persistent API

Add `ctx.add_sub_entity("name")` that returns a `SubEntityContext` without scoping.

**Pros:** first-class persistent sub-entities.
**Cons:** shutdown ordering (what if device exits before cleanup?), larger API, two
code paths to test and maintain.

### Option C: Independent command implementation

Sub-entities subscribe to their own MQTT topics manually, bypassing `TopicRouter`.

**Pros:** no dependency on ADR-025 infrastructure.
**Cons:** duplicates routing logic, inconsistent with established command patterns,
more code to maintain.

### Option D: Extend HealthReporter for sub-entities

Route sub-entity availability through `HealthReporter` for centralized tracking.

**Pros:** single source of truth for all availability state.
**Cons:** complicates `HealthReporter` shutdown logic, adds tracking overhead for
transient entities, marginal benefit since `__aexit__` guarantees cleanup.

## Decision Matrix

| Criterion              | A: ctx mgr only | B: + persistent | C: independent cmd | D: via HealthReporter |
| ---------------------- | ---------------- | --------------- | ------------------ | --------------------- |
| API simplicity         | 5                | 3               | 4                  | 4                     |
| Guaranteed cleanup     | 5                | 3               | 4                  | 5                     |
| Infrastructure reuse   | 5                | 5               | 2                  | 3                     |
| Permanent sub-entities | 3                | 5               | 3                  | 3                     |
| Maintenance cost       | 5                | 3               | 2                  | 3                     |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Devices with transient sub-components get framework-managed lifecycle — no more
  manual availability publishing and cleanup.
- Home Assistant sees sub-entities appear and disappear cleanly, with no stale
  retained state.
- Sub-entity commands work immediately via existing ADR-025 infrastructure.
- The API is small and consistent with existing `DeviceContext` patterns.

### Negative

- Persistent sub-entities use a workaround (long-running `async with`) rather than
  a dedicated API. Acceptable for 0.3.0; can be addressed in a future ADR if demand
  materialises.
- Sub-entity availability is not tracked by `HealthReporter`, so framework-level
  health queries don't include sub-entity status. This is intentional for simplicity
  but may need revisiting if observability requirements grow.

### References

- ADR-002: MQTT Topic Conventions
- ADR-011: Error Handling and Publishing
- ADR-012: Health and Availability Reporting
- ADR-025: Command Channel and Sub-Topic Routing

_2026-04-06_
