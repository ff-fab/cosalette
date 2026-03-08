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
}
```

1. Concrete float, or `"<deferred>"` if the interval is a settings-derived callable
2. Strategy `repr()` — composites are shown recursively
3. Persist policy `repr()`, or `null` if not set
4. Coalescing group name, or `null`
5. Injected parameters as `[param_name, type_name]` pairs

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
 "dependencies": [["ctx", "DeviceContext"]]}

# Command entry
{"name": "valve", "type": "command", "func": "handlers.valve",
 "mqtt_params": ["payload", "topic"], "is_root": False,
 "has_init": False, "dependencies": []}
```

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
| **CLI diagnostics** | `--show-devices` flag renders the snapshot as a table |
| **Machine-readable output** | `--show-devices-json` dumps as JSON |
| **Agent consumption** | AI agents parse the JSON to understand app structure |
| **Test assertions** | Verify registration correctness in integration tests |

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
