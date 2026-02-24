# ADR-015: Persistence

## Status

Accepted  **Date:** 2025-06-25

## Context

IoT bridge applications often maintain state between restarts — accumulated counter
values, calibration offsets, last-known-good readings, or user-configured thresholds.
Without persistence, every restart loses this state and devices must re-derive it from
scratch (if possible at all).

### Evidence from gas2mqtt

The reference application gas2mqtt demonstrates the pattern clearly:

| State item | Why it's needed |
| --- | --- |
| `total_m3` | Accumulated gas consumption; the impulse counter resets on power loss |
| `last_impulse_ts` | Debounce and flow-rate calculation need the previous timestamp |
| `offset` | User-configured calibration offset persisted across restarts |

gas2mqtt uses a hand-rolled `JsonFileStorage` class with a `Port` abstraction,
a `FakeStorage` for tests, and a `NullStorage` fallback. This pattern recurs
in every non-trivial IoT bridge.

### The Pattern is Pervasive

Every IoT bridge that tracks cumulative values, calibration data, or user settings
needs the same three things:

1. **A storage backend** — where bytes live (file, database, memory)
2. **A per-device scoped store** — so each device's state is isolated
3. **A save-timing policy** — when to flush to disk (every publish? every change? only on shutdown?)

## Decision

Provide a three-layer persistence system as a first-class framework feature.

### Layer 1 — Store Protocol

A `@runtime_checkable` protocol defining the storage contract:

```python
class Store(Protocol):
    def load(self, key: str) -> dict[str, object] | None: ...
    def save(self, key: str, data: dict[str, object]) -> None: ...
```

Four backends ship with the framework:

| Backend | Use case |
| --- | --- |
| `NullStore` | Opt-out; all operations are no-ops |
| `MemoryStore` | Testing; stores data in a dict |
| `JsonFileStore` | Production default; atomic writes, single JSON file |
| `SqliteStore` | Production alternative; WAL mode, single-file database |

### Layer 2 — DeviceStore

A `MutableMapping`-like wrapper scoped to a single device key.
Provides dict-style access (`store["key"]`), dirty tracking, and
`to_dict()` / `mark_dirty()` methods. Injected into handlers via
the DI system — handlers declare `store: DeviceStore` in their
signature.

### Layer 3 — PersistPolicy

A strategy protocol controlling *when* the store is saved during operation:

```python
class PersistPolicy(Protocol):
    def should_save(self, store: DeviceStore, published: bool) -> bool: ...
```

Three policies ship with the framework:

| Policy | Saves when |
| --- | --- |
| `SaveOnPublish()` | After each MQTT publish |
| `SaveOnChange()` | Whenever the store is dirty |
| `SaveOnShutdown()` | Only on graceful shutdown |

Policies compose with `|` (OR) and `&` (AND), mirroring `PublishStrategy`.

The framework **always** saves on shutdown regardless of policy (safety net).

### Wiring

```python
app = cosalette.App("myapp", "1.0.0", store=JsonFileStore("./data"))

@app.telemetry("sensor", interval=60, persist=SaveOnPublish())
async def sensor(store: DeviceStore) -> dict[str, object]:
    store["count"] = store.get("count", 0) + 1
    return {"count": store["count"]}
```

### Separation of Concerns

| Concern | Responsibility |
| --- | --- |
| Where to store | `Store` backend (app-level) |
| What to store | Handler code (device-level) |
| When to save | `PersistPolicy` (decorator parameter) |
| Key scoping | `DeviceStore` (framework-managed) |

## Considered Options

### Option A: Utility Library (filter-style)

Provide `Store` and `DeviceStore` as importable classes. Users wire
everything manually via `init=` callbacks.

- *Advantages:* Simple to implement, no framework coupling
- *Disadvantages:* Boilerplate in every handler, no save-timing control,
  easy to forget shutdown saves

### Option B: `persist=` Decorator Parameter (strategy-style) ✅

Provide `Store` backends, `DeviceStore` injection, and composable
`PersistPolicy` strategies via a `persist=` decorator parameter.

- *Advantages:* Declarative, composable, mirrors `publish=` pattern,
  framework handles lifecycle and error recovery
- *Disadvantages:* More framework surface area, `persist=` only on
  `@app.telemetry` (device handlers own their loop)

### Option C: Explicit Store with Manual `.save()`

Inject `DeviceStore` but require users to call `.save()` explicitly.

- *Advantages:* Full user control, simple protocol
- *Disadvantages:* Easy to forget saves, no composition, no shutdown safety net

### Option D: Database-centric ORM

Provide a full ORM layer with models, migrations, and query builders.

- *Advantages:* Powerful for complex state
- *Disadvantages:* Massive complexity, wrong abstraction level for IoT bridges

## Decision Matrix

| Criterion | A: Library | B: persist= | C: Manual | D: ORM |
| --- | --- | --- | --- | --- |
| Ease of use | 3 | 5 | 3 | 2 |
| Composability | 1 | 5 | 1 | 3 |
| Framework coherence | 2 | 5 | 3 | 2 |
| Testability | 4 | 5 | 4 | 3 |
| Implementation cost | 5 | 3 | 4 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Persistence becomes declarative — one line in the decorator
- Save-timing is composable and testable, just like publish strategies
- `MemoryStore` backend makes testing trivial — no filesystem needed
- Shutdown safety net prevents data loss from forgotten saves
- `DeviceStore` injection follows established DI patterns

### Negative

- `persist=` only works on `@app.telemetry` — device handlers must
  call `store.save()` manually (matches `publish=` scope)
- Four backends to maintain (though NullStore and MemoryStore are trivial)
- Users must understand the three-layer architecture

## Related Decisions

- [ADR-006: Hexagonal Architecture](ADR-006-hexagonal-architecture.md) — Store is a port
- [ADR-013: Publish Strategies](ADR-013-telemetry-publish-strategies.md) — `persist=` mirrors `publish=`
- [ADR-014: Signal Filters](ADR-014-signal-filters.md) — filters are utility-library, persistence is framework-integrated

_2025-06-25_
