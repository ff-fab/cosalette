# T1: Cosalette Shared Topic Namespace for Telemetry and Commands

## Status

**Resolved** — Implemented in cosalette via scoped name uniqueness
([ADR-019](../adr/ADR-019-scoped-name-uniqueness.md)).

**Resolved Date:** 2026-03-04

## Resolution

**Option B (per-type uniqueness)** from the workarounds section was implemented in
cosalette as a three-phase change:

1. **Per-type name validation** — `_validate_name_unique()` now scopes checks within
   each registration type. Telemetry + command pairs can share the same name; device
   registrations remain globally unique.
2. **Shared `DeviceContext` dedup** — `_build_contexts()` creates a single
   `DeviceContext` when a telemetry and command registration share a name, so both
   handlers share availability and error publishing.
3. **Availability publish dedup** — `_publish_device_availability()` deduplicates
   shared names to avoid publishing duplicate availability messages.

The `cmd_` prefix workaround is no longer necessary. See
[ADR-019](../adr/ADR-019-scoped-name-uniqueness.md) for the full decision record.

## Summary (Original Problem)

Cosalette enforced a **globally unique name** across all registration types (devices,
telemetry, commands). This name was the sole determinant of the MQTT topic segment. When
a domain like `hot_water` needed both periodic telemetry (`/state`) and writable commands
(`/set`), the two registrations **could not share the same name**, forcing a prefix
workaround (`cmd_hot_water`) that violated the project's MQTT topic layout defined in
ADR-002.

This was a **framework-level limitation** that has now been resolved.

## The Dilemma

### ADR-002 Expects Shared Namespaces

ADR-002 specifies domain-grouped MQTT topics where each physical subsystem owns a single
namespace with both `/state` and `/set` suffixes:

```
vito2mqtt/{device_id}/hot_water/state   ← telemetry (read-only sensors)
vito2mqtt/{device_id}/hot_water/set     ← commands  (writable parameters)
```

This follows standard MQTT conventions and is how Home Assistant expects devices to
organize their topics. The **same domain name** (`hot_water`) appears in both the
telemetry and command topics.

### Cosalette Forbids It

Cosalette's `App` class maintains three separate registration lists:

| Registration type | Stored in          | Added via            |
| ----------------- | ------------------ | -------------------- |
| Device            | `self._devices`    | `app.add_device()`   |
| Telemetry         | `self._telemetry`  | `app.add_telemetry()`|
| Command           | `self._commands`   | `app.add_command()`  |

However, **all three lists feed into a single name validation pool**:

```python
# cosalette/_app.py — _all_registrations property
@property
def _all_registrations(self):
    return [*self._devices, *self._telemetry, *self._commands]
```

Every `add_*()` call passes through `_check_device_name()` →
`_registration_summary()` → `_validate_name_unique()`, which collects all names from
all three registries into one `set[str]` and rejects duplicates:

```python
@staticmethod
def _validate_name_unique(name: str, existing: set[str]) -> None:
    if name in existing:
        msg = f"Device name '{name}' is already registered"
        raise ValueError(msg)
```

The result: after `add_telemetry(name="hot_water", ...)` succeeds, calling
`add_command(name="hot_water", ...)` raises `ValueError: Device name 'hot_water' is
already registered`.

### The Name IS the Topic

There is no parameter to decouple the internal registration name from the MQTT topic
segment. The topic is derived directly from the name at multiple levels:

1. **`DeviceContext._topic_base`**: `f"{topic_prefix}/{name}"`
2. **`DeviceContext.publish_state()`**: publishes to `f"{self._topic_base}/state"`
3. **`TopicRouter` subscriptions**: subscribes to `f"{prefix}/{device}/set"`
4. **`TopicRouter._extract_device()`**: parses the incoming topic to look up the
   handler by name

There are **zero** topic override parameters anywhere in the `add_telemetry()`,
`add_command()`, or `DeviceContext` APIs.

## Affected Domains

Four of our seven domain groups appear in **both** `SIGNAL_GROUPS` (telemetry) and
`COMMAND_GROUPS` (commands):

| Domain              | In SIGNAL_GROUPS | In COMMAND_GROUPS | Conflict |
| ------------------- | ---------------- | ----------------- | -------- |
| `outdoor`           | ✅               | ❌                | —        |
| `hot_water`         | ✅               | ✅                | **Yes**  |
| `burner`            | ✅               | ❌                | —        |
| `heating_radiator`  | ✅               | ✅                | **Yes**  |
| `heating_floor`     | ✅               | ✅                | **Yes**  |
| `system`            | ✅               | ✅                | **Yes**  |
| `diagnosis`         | ✅               | ❌                | —        |

This is not an edge case — **4 out of 7 domains** (57%) are affected.

## Current Workaround

The composition root uses a `name_format` parameter to prefix command registrations:

```python
# packages/src/vito2mqtt/main.py
register_commands(app, name_format="cmd_{group}")
```

This produces:

| Registration          | Name                  | MQTT Topic                                     |
| --------------------- | --------------------- | ---------------------------------------------- |
| Telemetry: hot_water  | `hot_water`           | `vito2mqtt/.../hot_water/state` ✅              |
| Command: hot_water    | `cmd_hot_water`       | `vito2mqtt/.../cmd_hot_water/set` ❌            |
| Telemetry: system     | `system`              | `vito2mqtt/.../system/state` ✅                 |
| Command: system       | `cmd_system`          | `vito2mqtt/.../cmd_system/set` ❌               |

The `cmd_` prefix makes commands functional but **violates ADR-002**: command topics
no longer share the domain namespace with their telemetry counterparts.

## Workarounds Evaluated

### A. `cmd_` Prefix (Current)

- **ADR-002 compliant:** No
- **Framework features preserved:** Yes
- **Effort:** Trivial
- **Assessment:** Functional hack. Topics are ugly and non-standard. Home Assistant
  integration requires custom topic templates instead of convention-based discovery.

### B. Use `add_device()` for Overlapping Domains

The `@app.device` archetype can both publish state and handle commands under a single
name. However, it provides **none** of the telemetry archetype's conveniences:

- No automatic periodic polling (must implement own async loop)
- No coalescing groups (`group=` parameter)
- No `PublishStrategy` (e.g. `OnChange()`)
- No `PersistPolicy`

**Assessment:** Would solve naming but requires reimplementing the tick-aligned
coalescing scheduler that cosalette's telemetry archetype provides for free. This
defeats the purpose of using the framework.

### C. Two Separate App Instances

Run telemetry and commands on different `App` instances with separate MQTT connections.

**Assessment:** Not feasible — two event loops, two MQTT connections, no shared
adapter lifecycle management. Architectural dead end.

### D. Upstream Enhancement: Decouple Name from Topic (Recommended)

Add an optional `topic=` parameter to `add_telemetry()` and `add_command()`:

```python
app.add_telemetry(name="hot_water_telemetry", topic="hot_water", func=..., ...)
app.add_command(name="hot_water_command",     topic="hot_water", func=...)
```

The internal name remains unique for context keying, logging, and health reporting.
The topic parameter controls the MQTT segment independently.

| Aspect              | Status   |
| ------------------- | -------- |
| ADR-002 compliant   | ✅ Yes   |
| Framework features  | ✅ All preserved |
| Breaking change     | ❌ No (additive, optional parameter) |
| Complexity          | Moderate — needs changes in `DeviceContext._topic_base`, `TopicRouter`, and `_build_contexts()` |

## Proposed Framework Enhancement

### Requirements

1. **`topic` parameter** on `add_telemetry()` and `add_command()`
   Optional `str` that defaults to the registration `name`. When provided, this value
   is used as the MQTT topic segment instead of the name.

2. **Topic collision awareness**
   Two registrations with the same `topic` value should be permitted **only** when one
   is telemetry (`/state`) and the other is command (`/set`). Two telemetry
   registrations or two command registrations with the same topic should still be
   rejected, since they would conflict on the same MQTT topic.

3. **`DeviceContext` topic decoupling**
   `DeviceContext._topic_base` should use the `topic` value (if provided) instead of
   `name`. Logging and error reporting should continue to use `name`.

4. **`TopicRouter` multi-name support**
   The router's `_extract_device()` must map from topic segments back to handler names,
   which may differ from the topic. A reverse lookup (`topic → name`) is needed.

### Impact on cosalette Internals

| Component                 | Change Required                                          |
| ------------------------- | -------------------------------------------------------- |
| `_TelemetryRegistration`  | Add `topic: str \| None` field                           |
| `_CommandRegistration`    | Add `topic: str \| None` field                           |
| `_validate_name_unique()` | No change (validates `name`, not `topic`)                |
| New: `_validate_topic()`  | Validate topic uniqueness per suffix (`/state`, `/set`)  |
| `_build_contexts()`       | Pass `topic` (or fallback to `name`) to `DeviceContext`  |
| `DeviceContext.__init__()` | Accept `topic` parameter for `_topic_base`               |
| `TopicRouter`             | Build reverse map `topic → name` for command dispatch    |

### Suggested API

```python
# Telemetry with explicit topic
app.add_telemetry(
    name="hot_water_telemetry",
    topic="hot_water",           # ← new optional parameter
    func=read_hot_water,
    interval=30,
)

# Command sharing the same MQTT namespace
app.add_command(
    name="hot_water_command",
    topic="hot_water",           # ← same topic, different name
    func=handle_hot_water_set,
)

# Result:
#   vito2mqtt/.../hot_water/state  ← published by hot_water_telemetry
#   vito2mqtt/.../hot_water/set    ← subscribed by hot_water_command
```
