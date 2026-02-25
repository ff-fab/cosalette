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
async def controller(ctx: DeviceContext, store: DeviceStore) -> None:
    while True:
        # ... do work ...
        store["last_run"] = time.time()
        store.save()  # Manual save
        await asyncio.sleep(60)
```

The framework still saves on shutdown via the `finally` block.

## See Also

- [Publish Strategies](publish-strategies.md) — the `publish=` parameter that `persist=` mirrors
- [Signal Filters](signal-filters.md) — another composable utility
- [Testing Guide](../guides/testing.md) — testing with `MemoryStore`
- [ADR-015: Persistence](../adr/ADR-015-persistence.md) — architectural decision record
