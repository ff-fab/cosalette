---
icon: material/magnify-scan
---

# Registry Introspection

Cosalette's introspection system lets you **inspect all registered devices,
telemetry, commands, and adapters** at any point after registration — before
the app even starts running.

## Why Introspect?

A cosalette app assembles its registrations declaratively via decorators:

```python
@app.telemetry("temp", interval=30, publish=Every(seconds=60) | OnChange())
async def temp() -> dict[str, object]:
    return {"celsius": read_sensor()}
```

But once an app has dozens of registrations across multiple files, it becomes
hard to answer simple questions:

- What devices are registered?
- What interval does `temp` use? Is it deferred?
- Which telemetry uses a `SaveOnPublish` policy?
- What adapters are wired up?

`build_registry_snapshot()` answers all of these programmatically.

## The Snapshot

`build_registry_snapshot(app)` returns a plain Python dict that is
**fully JSON-serializable** — no custom encoders needed:

```python
import cosalette
from cosalette import build_registry_snapshot

app = cosalette.App(name="mybridge", version="1.0.0")

# ... register devices, telemetry, commands, adapters ...

snapshot = build_registry_snapshot(app)
```

The returned dict has this structure:

```python
{
    "app": {
        "name": "mybridge",
        "version": "1.0.0",
        "description": "IoT-to-MQTT bridge",
    },
    "devices": [ ... ],
    "telemetry": [ ... ],
    "commands": [ ... ],
    "streams": [ ... ],
    "periodic": [ ... ],
    "adapters": [ ... ],
}
```

### Telemetry Entries

Each telemetry entry captures the full configuration:

```python
{
    "name": "temp",
    "type": "telemetry",
    "func": "sensors.temp",
    "interval": 30.0,                                    # (1)!
    "strategy": "AnyStrategy(Every(seconds=60.0), OnChange())",  # (2)!
    "persist": "SaveOnPublish()",                         # (3)!
    "group": "sensors",                                   # (4)!
    "is_root": False,
    "has_init": False,
    "dependencies": [["store", "DeviceStore"]],           # (5)!
    "triggerable": True,                                  # (6)!
    "trigger_source": "local",                            # (7)!
    "min_interval": 2.5,                                  # (8)!
}
```

1. Concrete float, or `"<deferred>"` if the interval is a settings-derived callable
2. Strategy `repr()` — composites are shown recursively
3. Persist policy `repr()`, or `null` if not set
4. Coalescing group name, or `null`
5. Injected parameters as `[param_name, type_name]` pairs
6. `True` when the entity accepts a wake (`triggerable=`), else `False`
7. Trigger source string (`"local"`) or `null` when not triggerable (ADR-065)
8. Storm-throttle floor in seconds, or `null` when unthrottled (ADR-066)

The `triggerable` / `trigger_source` / `min_interval` fields appear on both
telemetry and device entries. `format_registry_table()` renders
`trigger_source` and `min_interval` as **Trigger** and **Min interval**
columns (em-dash when absent) — surfaced from the terminal via
`cosalette manifest --registry --table` (see
[CLI Reference](../reference/cli.md#registry-snapshot)).

### Deferred Intervals

Intervals can be a concrete float or a callable that resolves from settings
at runtime (see [ADR-020](../adr/ADR-020-deferred-interval-resolution.md)):

```python
# Concrete — shows as 30.0
@app.telemetry("temp", interval=30.0)

# Deferred — shows as "<deferred>"
@app.telemetry("temp", interval=lambda s: s.sensor_interval)
```

Before the app runs, deferred intervals cannot be resolved because settings
haven't been validated yet. The snapshot shows `"<deferred>"` as a clear
indicator.

### Device and Command Entries

```python
# Device entry
{"name": "motor", "type": "device", "func": "devices.motor",
 "is_root": False, "has_init": True,
 "triggerable": True, "trigger_source": "local", "min_interval": None,
 "dependencies": [["ctx", "DeviceContext"]]}

# Command entry
{"name": "valve", "type": "command", "func": "handlers.valve",
 "mqtt_params": ["payload", "topic"], "is_root": False,
 "has_init": False, "dependencies": []}
```

### Stream and Periodic Entries

```python
# Stream entry
{"name": "receiver", "type": "stream", "func": "streams.receiver",
 "enabled": True, "is_root": False, "maxsize": 0,
 "backpressure": "drop_newest",
 "summary": "Read sensor frames from the serial port",
 "state_model": "FrameState",                          # (1)!
 "behavior": ["decodes LaCrosse frames"],
 "effects": ["publishes per-sensor state"],
 "dependencies": [["ctx", "DeviceContext"]]}

# Periodic entry
{"name": "cache-refresh", "type": "periodic", "func": "tasks.refresh_cache",
 "interval": 60.0, "enabled": True, "has_init": False,
 "summary": "Refresh the upstream cache",
 "behavior": ["evicts stale entries"],                 # (2)!
 "dependencies": [["cache", "CachePort"]]}
```

1. Class name of the declared `state_model`, or `null`. On streams this is
   runtime load-bearing — it validates every `ctx.publish_state()` payload (see
   [Validated Published State](../guides/contract-first-route-design.md#validated-published-state)).
2. Periodic tasks carry no `state_model`, `payload_model`, or `effects`: they have
   no MQTT presence by design ([ADR-041](../adr/ADR-041-periodic-background-tasks.md)).

Periodic tasks never appear in the generated AsyncAPI document — they have no MQTT
presence by design ([ADR-041](../adr/ADR-041-periodic-background-tasks.md)), so the
snapshot is the only place their contract metadata surfaces. Streams now emit an
AsyncAPI state channel (`x-cosalette-archetype: stream`) as of
[ADR-054](../adr/ADR-054-asyncapi-emission-for-the-stream-archetype.md), reversing
[ADR-045](../adr/ADR-045-stateful-stream-receiver-semantics.md)'s original exclusion;
the registry snapshot additionally carries stream-only fields that AsyncAPI does not
(maxsize, backpressure, dependencies).

### Adapter Entries

```python
{"port": "MqttPort", "impl": "PahoMqttAdapter",
 "dry_run": "NullMqttClient"}
```

Adapter `impl` and `dry_run` fields show:

- **Class name** for type-based registration
- **Import string** for lazy registration (e.g., `"mypackage.adapters:MyAdapter"`)
- **Qualified name** for callable factories

## Use Cases

| Use case | How |
| --- | --- |
| **Agent consumption** | The `cosalette_inspect_app` MCP tool returns the snapshot as JSON via `format_registry_json()` (see [`_mcp/_introspect_tools.py`](https://github.com/ff-fab/cosalette/blob/main/packages/src/cosalette/_mcp/_introspect_tools.py)) |
| **Programmatic/scripted use** | Call `build_registry_snapshot(app)` directly, then `format_registry_table()`/`format_registry_json()` (see [Formatting](#formatting) below) |
| **Test assertions** | Verify registration correctness in integration tests |

!!! note "Which CLI surface renders the registry snapshot"
    The app-runtime `--show-devices` and `--show-devices-json` flags (see
    [CLI Reference](../reference/cli.md#introspection-flags)) render the
    **AsyncAPI document** (`app.asyncapi()`), not the registry snapshot
    described on this page — despite the name, they don't call
    `build_registry_snapshot()`. To print the registry snapshot from a
    terminal, use the `cosalette` package CLI:
    `cosalette manifest myapp.main:app --registry` (add `--table` for the
    human-readable form). The `cosalette_inspect_app` MCP tool and calling
    `build_registry_snapshot()` yourself remain available for programmatic use.

## Formatting

Two convenience functions turn a snapshot into display-ready output:

```python
from cosalette import build_registry_snapshot, format_registry_table, format_registry_json

snapshot = build_registry_snapshot(app)

# Human-readable table
print(format_registry_table(snapshot))

# Indented JSON via orjson (same formatter the cosalette_inspect_app MCP tool uses)
print(format_registry_json(snapshot))
```

`format_registry_table` groups registrations by type (devices, telemetry,
commands, streams, periodic, adapters), omitting empty sections. Booleans are
rendered as ✓/— and missing values as —.

`format_registry_json` delegates to `orjson` with two-space indentation,
consistent with [ADR-021](../adr/ADR-021-json-serialization.md).

## Introspection Accessors

`build_registry_snapshot()` returns a flattened, serialised view. When you need the
**live registration objects** — with their full typed metadata — rather than a
serialised snapshot, the `App` exposes read-only accessor properties. Most are shared
with `Router` through a common mixin, so the same code works against either.

### Registration collections

The sequence accessors return immutable point-in-time `tuple` snapshots, so
registry internals cannot be mutated through them. `.adapters` returns a live,
immutable `MappingProxyType` view (entries added later remain visible through it):

| Accessor                     | Return type                       | App | Router |
| ---------------------------- | --------------------------------- | :-: | :----: |
| `.devices`                   | `Sequence[DeviceRegistration]`    |  ✓  |   ✓    |
| `.telemetry_registrations`   | `Sequence[TelemetryRegistration]` |  ✓  |   ✓    |
| `.commands`                  | `Sequence[CommandRegistration]`   |  ✓  |   ✓    |
| `.periodic_registrations`    | `Sequence[PeriodicRegistration]`  |  ✓  |   ✓    |
| `.stream_registrations`      | `Sequence[StreamRegistration]`    |  ✓  |   ✓    |
| `.state_factories`           | `tuple[StateRegistration, ...]`   |  ✓  |   —    |
| `.adapters`                  | `Mapping[type, ...]`              |  ✓  |   ✓    |

The `_registrations` suffix on `telemetry_registrations` and `periodic_registrations`
avoids shadowing the `@app.telemetry` / `@app.periodic` decorators.

`.root_names` returns a `frozenset[str]` of the names of root-level registrations
(`is_root`) across the device, telemetry, command, and stream archetypes. Root entities
occupy the app namespace with no device segment, so by the ADR-058 contract they never
appear in a schema's `device_names`; tooling that compares registrations against schema
device names (e.g. `cosalette schema check`) excludes them to avoid a spurious `EXTRA`.

```python
# Assert registration metadata directly, without a snapshot
reg = next(r for r in app.commands if r.name == "valve")
assert reg.payload_model is ValveCommand
assert reg.state_model is ValveState
```

### Name and configuration accessors

| Accessor                | Return type      | App | Router | Description                                                    |
| ----------------------- | ---------------- | :-: | :----: | -------------------------------------------------------------- |
| `.registered_names`     | `frozenset[str]` |  ✓  |   ✓    | Every registered device/telemetry/command/periodic/stream name |
| `.settings_class`       | `type[Settings]` |  ✓  |   —    | The concrete `Settings` subclass, available before startup      |
| `.store`                | `Store \| None`  |  ✓  |   —    | Configured store backend (or `None` when explicitly opted out)  |
| `.store_is_default`     | `bool`           |  ✓  |   —    | `True` when the store was auto-resolved by the framework        |
| `.has_dynamic_entities` | `bool`           |  ✓  |   —    | `True` when the app's entity set can vary between runs          |
| `.retained_cleanup`     | `bool \| None`   |  ✓  |   —    | Explicit ADR-048 cleanup override (`True`/`False`), or `None` for auto |

`registered_names` answers "is this name taken?"; the collection accessors expose the
metadata behind each name. All of these are stable public API — useful for structural
wiring tests, code generators, and diagnostics that reason about an app **before it
starts**.

## Design Notes

The introspection module reads the `App`'s internal registries directly.
It produces a read-only snapshot — no mutations, no side effects. The
output uses `repr()` on strategies and persist policies, which means
adding a custom strategy only requires implementing `__repr__` for it
to appear correctly in snapshots.

!!! info "Open/Closed Principle"
    New strategy or policy classes automatically work with introspection
    as long as they implement `__repr__`. No changes to the introspection
    module are needed — the system is open for extension, closed for
    modification.
