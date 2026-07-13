---
icon: material/database
---

# Persistence

Cosalette's persistence system lets devices **save state across restarts** —
accumulated values, calibration offsets, last-known-good readings, or anything
that shouldn't be lost on power failure.

The system has three layers:

| Layer | What it does | Set where |
| --- | --- | --- |
| **Store backend** | Where bytes live (file, database, memory) | `App(store=...)` |
| **DeviceStore** | Per-device scoped dict-like interface | Injected into handlers |
| **PersistPolicy** | When to flush to disk | `persist=` decorator parameter |

## Store Backends

A `Store` is a key-value storage backend. The framework ships four:

| Backend | Use case |
| --- | --- |
| `JsonFileStore(path)` | Production — single JSON file, atomic writes |
| `SqliteStore(path)` | Production — single SQLite file, WAL mode |
| `MemoryStore()` | Testing — in-memory dict |
| `NullStore()` | Opt-out — all operations are no-ops |

The `Store` protocol is simple:

```python
class Store(Protocol):
    def load(self, key: str) -> dict[str, object] | None: ...
    def save(self, key: str, data: dict[str, object]) -> None: ...
```

You can implement your own backend (Redis, S3, etc.) by satisfying this protocol.

### JsonFileStore

Stores all keys as top-level entries in a single JSON file. Uses atomic
writes (write to temp file, then rename) to prevent corruption.

```python
store = JsonFileStore("./data/state.json")
# All device keys stored in one file: {"sensor": {...}, "counter": {...}}
```

### SqliteStore

Stores all keys in a single SQLite database with WAL mode enabled
for concurrent read access.

```python
store = SqliteStore("./data/state.db")
```

### Store Factories

When the store path depends on runtime settings, pass a **callable factory**
instead of a concrete instance:

```python
def make_store(settings: Gas2MqttSettings) -> Store:
    return JsonFileStore(settings.data_dir / "state.json")

app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
    store=make_store,
)
```

The factory is called during bootstrap — after settings and adapters are
resolved but before any device handlers run. Parameters are injected via
the DI system (every parameter must carry a type annotation), so the
factory can request settings, adapters, or both:

```python
def make_store(settings: Gas2MqttSettings) -> Store:
    return SqliteStore(settings.db_path)

app = cosalette.App(name="gas2mqtt", store=make_store)
```

!!! tip "When to use a factory"
    Use a concrete `Store` when the path is known at import time.
    Use a factory when the path comes from settings or environment variables
    that are resolved at startup.

## Default Store Resolution

When `store=` is **omitted** from `App(...)`, the framework automatically
creates a `JsonFileStore` at a path derived from the app name. Path
precedence:

1. `<NAME>_STORE_PATH` environment variable — name upper-cased, all
   non-alphanumeric characters replaced by underscores
   (e.g. `CALDATES2MQTT_STORE_PATH`, `SENSOR_HUB_STORE_PATH`).
2. `$XDG_STATE_HOME/<name>/store.json`.
3. `~/.local/state/<name>/store.json` (the XDG default).

This means **retained-topic cleanup (ADR-048) works with zero
configuration** — apps that remove configured entities will have orphaned
retained topics cleared on the next restart without any store wiring.

```python
import cosalette

# Zero-config: JsonFileStore auto-resolved from the app name
# Path: MYAPP_STORE_PATH → $XDG_STATE_HOME/myapp/store.json → ~/.local/state/myapp/store.json
app = cosalette.App(name="myapp", version="1.0.0")
```

To **opt out of persistence entirely**, pass `store=None`:

```python
# Explicit opt-out — no store, no retained-topic cleanup
app = cosalette.App(name="myapp", version="1.0.0", store=None)
```

Explicit `Store` instances and factory callables always take precedence
over the default resolution:

```python
# Explicit path — default resolution does not apply
app = cosalette.App(
    name="myapp",
    store=cosalette.JsonFileStore("/app/data/state.json"),
)
```

### Configurable default backend

By default the auto-resolved store uses `JsonFileStore`. For high-write
apps, call `cosalette.set_default_store_backend()` **once at startup**
(before any `App()` is constructed) to swap the backend:

```python
import cosalette
from cosalette import SqliteStore

# All App() instances that omit store= will now resolve a SqliteStore
cosalette.set_default_store_backend(SqliteStore)

app = cosalette.App(name="myapp", version="1.0.0")
```

Passing `None` resets to the `JsonFileStore` default. Explicit `store=`
arguments on `App()` are always unaffected.

!!! warning "Process-global, not thread-safe"
    Call `set_default_store_backend()` once during module initialisation or
    early startup. Do not call it concurrently or mid-run.

!!! warning "Switching backends on an existing store path"
    `SqliteStore` and `JsonFileStore` use different file formats. If
    `store.json` already exists at the default path and you switch the
    backend to `SqliteStore`, the open will fail with "file is not a
    database". When switching backends, point `<NAME>_STORE_PATH` at a
    new filename (e.g. `MYAPP_STORE_PATH=/data/store.sqlite3`) or
    delete/migrate the existing file first.

### Container deployments

Inside a container the default XDG path is ephemeral. When the framework
detects a container runtime (`/.dockerenv`, `/run/.containerenv`, or the
`container` env var) **and** no `<NAME>_STORE_PATH` is set **and** the
app's entity set may vary by config across restarts, it logs a `WARNING`
at startup:

```
WARNING  Using an auto-resolved default store at <path>, which is ephemeral
         inside a container - retained-topic cleanup (ADR-048) will not survive
         restarts. Set <NAME>_STORE_PATH to a path on a mounted volume for
         durable persistence.
```

For durable persistence across restarts, set `<NAME>_STORE_PATH` to a
path on a mounted volume (e.g. `MYAPP_STORE_PATH=/app/data/store.json`).
See the [Deployment guide](../guides/deployment.md#persistence) for
details.

!!! note "Why static apps are exempt"
    The warning exists to protect against ADR-048 ghost entities — retained
    topics for devices/telemetry that no longer exist after a config change.
    If the app's entity set is provably fixed (static `name=` strings, no
    callable `enabled=`, no `@app.on_configure` hooks), there can never be a
    config-driven entity removal across restarts, so ADR-048 cleanup has
    nothing to recover. The warning is suppressed for such apps.
    Apps that use `@app.on_configure` or callable `name=`/`enabled=` always
    receive the warning, as their entity set may shrink between restarts.

    Additionally, provably-static apps skip the ADR-048 snapshot write
    entirely — no `store.json` is created at the default XDG path unless
    `persist=` is also used.

See [ADR-049 — Default store path resolution](../adr/ADR-049-default-store-path-resolution.md)
for the design rationale and alternatives considered.

## DeviceStore

`DeviceStore` is a per-device scoped wrapper around a `Store` backend.
It provides a familiar dict-like interface:

```python
@app.telemetry("sensor", interval=60)
async def sensor(store: DeviceStore) -> dict[str, object]:
    # Dict-like access
    store["count"] = store.get("count", 0) + 1
    store.setdefault("offset", 0.0)

    # Check what's stored
    all_data = store.to_dict()

    return {"count": store["count"]}
```

The framework automatically:

1. Creates a `DeviceStore` scoped to the device name
2. Loads existing data before the first handler call
3. Injects it via the DI system (declare `store: DeviceStore`)
4. Saves on shutdown (safety net, regardless of policy)

### Dirty Tracking

`DeviceStore` tracks whether it has been modified since the last save.
This enables the `SaveOnChange` policy to avoid unnecessary I/O:

```python
store["value"] = 42      # store.dirty → True
store.save()             # store.dirty → False
store.mark_dirty()       # Force dirty (e.g., after mutating a nested object)
```

## Save Policies (PersistPolicy)

A `PersistPolicy` controls **when** the store is saved during the
telemetry loop. Three policies ship with the framework:

### SaveOnPublish

Save after each successful MQTT publish. The most common choice —
persisted state always matches what's been broadcast.

```python
@app.telemetry("sensor", interval=60, persist=SaveOnPublish())
async def sensor(store: DeviceStore) -> dict[str, object]:
    store["count"] = store.get("count", 0) + 1
    return {"count": store["count"]}
```

### SaveOnChange

Save whenever the store has been modified, regardless of whether
MQTT publishing occurred. Most aggressive — minimises data loss.

```python
@app.telemetry("sensor", interval=60, persist=SaveOnChange())
async def sensor(store: DeviceStore) -> dict[str, object]:
    store["count"] = store.get("count", 0) + 1
    return {"count": store["count"]}
```

### SaveOnShutdown

Save only on graceful shutdown. Lightest I/O — no saves during normal
operation. Risk: data loss on hard crash or power loss.

```python
@app.telemetry("sensor", interval=60, persist=SaveOnShutdown())
```

!!! warning "Crash risk"
    `SaveOnShutdown` means **all data since the last startup is lost**
    if the process crashes or loses power. Use only when the data
    can be re-derived.

### Default Behaviour

If you set `store=` on the App but don't specify `persist=` on a device,
the framework saves **only on shutdown** (equivalent to `SaveOnShutdown()`).

The framework **always** saves on shutdown regardless of policy — the
`persist=` parameter only controls *additional* saves during operation.

## Composing Policies

Policies compose with `|` (OR) and `&` (AND), just like publish strategies:

```python
# Save on publish OR when dirty (maximum safety)
persist = SaveOnPublish() | SaveOnChange()

# Save only when BOTH conditions are true
persist = SaveOnPublish() & SaveOnChange()
```

`|` creates an `AnySavePolicy` (save if any child says yes).
`&` creates an `AllSavePolicy` (save only if all children agree).

### When to Use Which Policy

| Policy | I/O frequency | Data safety | Best for |
| --- | --- | --- | --- |
| `SaveOnPublish()` | Medium | Good | Most telemetry devices |
| `SaveOnChange()` | High | Best | Critical counters, calibration |
| `SaveOnShutdown()` | Minimal | Low | Derived/re-calculable data |
| `SaveOnPublish() \| SaveOnChange()` | High | Best | Belt-and-suspenders |

## Testing with MemoryStore

Use `MemoryStore` in tests to avoid filesystem access:

```python
from cosalette import MemoryStore, DeviceStore
from cosalette.testing import AppHarness

async def test_sensor_persists_count():
    backend = MemoryStore()
    harness = AppHarness.create(store=backend)

    @harness.app.telemetry("sensor", interval=10)
    async def sensor(store: DeviceStore) -> dict[str, object]:
        store["count"] = store.get("count", 0) + 1
        return {"count": store["count"]}

    await harness.run()
    assert backend.load("sensor") == {"count": 1}
```

You can also pre-seed the store to test load behaviour:

```python
backend = MemoryStore()
backend.save("sensor", {"count": 99})

# Handler will see store["count"] == 99 on first call
```

## Persistence and Device Handlers

The `persist=` parameter is only available on `@app.telemetry`, because
the framework controls the telemetry loop and knows when publishes occur.

For `@app.device` handlers (which own their loop), inject `DeviceStore`
and call `store.save()` manually when appropriate:

```python
@app.device("controller")
async def controller(ctx: DeviceContext, store: DeviceStore):
    while not ctx.shutdown_requested:
        # ... do work ...
        store["last_run"] = ctx.clock.now()
        store.save()  # Manual save
        yield  # reaction boundary
        await ctx.sleep(60)
```

The framework still saves on shutdown via the `finally` block.

## See Also

- [Publish Strategies](publish-strategies.md) — the `publish=` parameter that `persist=` mirrors
- [Signal Filters](signal-filters.md) — another composable utility
- [Testing Guide](../guides/testing.md) — testing with `MemoryStore`
- [ADR-015: Persistence](../adr/ADR-015-persistence.md) — architectural decision record
- [ADR-037: Lazy Store Resolution](../adr/ADR-037-lazy-store-resolution.md) — callable store factories
