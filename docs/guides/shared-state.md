---
icon: material/link-variant
---

# Share State Between Handlers

In a typical cosalette application, different handlers need access to
shared mutable state. For example, a `@app.command()` handler might
update a valve position, and a `@app.telemetry()` handler needs to
report that position alongside sensor readings.

cosalette offers two first-class patterns for shared state:

| Pattern | Best for |
|---------|----------|
| [`@app.state`](#app-state-factory) | Objects constructed from settings at bootstrap; no port protocol needed |
| [Adapter-as-state](#adapter-as-state) | State that must satisfy a port Protocol shared across modules |

## `@app.state` Factory

`@app.state` is the simplest way to create shared state. Decorate a factory
function — cosalette calls it once at bootstrap, registers the return value
in the DI container by its return type, and injects it into every handler
that declares that type.

```python title="app.py"
import cosalette
from cosalette.testing import MockMqttClient


class ValveState:
    """Shared valve state — single instance per application."""

    def __init__(self) -> None:
        self.last_command: str | None = None

    def record(self, command: str) -> None:
        self.last_command = command


app = cosalette.App(name="mybridge", version="1.0.0")


@app.state
def valve_state() -> ValveState:  # (1)!
    return ValveState()


@app.telemetry("sensor", interval=5.0)
async def read_sensor(state: ValveState) -> dict[str, object]:  # (2)!
    return {"temperature": 22.5, "last_valve": state.last_command}


@app.command("valve")
async def handle_valve(payload: str, state: ValveState) -> dict[str, object]:
    state.record(payload)
    return {"valve_state": payload}
```

1. The return annotation `-> ValveState` is the DI key. No protocol or adapter
   registration needed.
2. Any handler declaring `ValveState` receives the same instance created by
   the factory.

### Settings injection

If the factory's first parameter is annotated with `Settings` (or a subclass),
the framework passes the resolved settings instance automatically:

```python
class AppSettings(cosalette.Settings):
    default_position: str = "closed"


@app.state
def valve_state(settings: AppSettings) -> ValveState:
    return ValveState(default_position=settings.default_position)
```

### Async factories and teardown

For state that holds resources (database connections, thread pools), use an
async generator or async context manager:

```python
from collections.abc import AsyncIterator


@app.state
async def database(settings: AppSettings) -> AsyncIterator[Database]:
    db = await Database.connect(settings.db_url)
    try:
        yield db
    finally:
        await db.close()  # runs on shutdown
```

Four factory forms are supported:

| Form | Teardown |
|------|----------|
| `def f() -> T` | None |
| `def f() -> ContextManager[T]` | `__exit__` |
| `async def f() -> AsyncIterator[T]` | generator finalized |
| `async def f() -> AsyncContextManager[T]` | `__aexit__` |

Teardown runs in **reverse registration order** (LIFO) on shutdown.

### Testing with `@app.state`

Use `AppHarness.override_state()` to bypass the factory in tests:

```python
import asyncio

async def test_command_handler(harness: AppHarness) -> None:
    fake_state = ValveState()
    harness.override_state(ValveState, fake_state)

    async def trigger():
        # ... invoke command handler or trigger action ...
        harness.trigger_shutdown()

    asyncio.create_task(trigger())
    await harness.run()

    assert fake_state.last_command == "open"
```

---

## `@app.react` — Domain-Event Reactors

`@app.react` completes the `@app.state` story: the state object collects domain
events; the reactor function handles the I/O side-effects. The framework calls the
reactor automatically at execution boundaries — no manual flush calls in your handlers.

### The problem `@app.react` solves

Without reactors, state objects that need to publish or persist must accept `ctx`
and `store` as method parameters. That couples the domain model to I/O and makes
unit-testing the state class require mocking infrastructure.

`@app.react` inverts this: the reactor lives at the composition root (`main.py`),
receives full DI injection, and the state class stays a pure domain object.

### Core pattern

```python title="app.py"
from __future__ import annotations
from dataclasses import dataclass, field

import cosalette
from cosalette import DeviceStore


class RegistryEvent:
    def __init__(self, name: str, sensor_id: int) -> None:
        self.name = name
        self.sensor_id = sensor_id


@dataclass
class Registry:
    _events: list[RegistryEvent] = field(default_factory=list, repr=False)

    def assign(self, name: str, sensor_id: int) -> None:
        self._events.append(RegistryEvent(name, sensor_id))

    def drain_events(self) -> list[RegistryEvent]:  # (1)!
        evts, self._events = self._events, []
        return evts


@dataclass
class SharedState:
    registry: Registry = field(default_factory=Registry)


app = cosalette.App(name="mybridge", version="1.0.0")


@app.state
def shared_state() -> SharedState:
    return SharedState()


@app.react(SharedState, drain=lambda s: s.registry.drain_events())  # (2)!
async def on_registry_events(
    events: list[RegistryEvent],   # (3)!
    ctx: cosalette.DeviceContext,
    store: DeviceStore,
    state: SharedState,
) -> None:
    for event in events:
        await ctx.publish("registry/event", {"name": event.name, "id": event.sensor_id})
    store["registry"] = [e.__dict__ for e in state.registry._events]


@app.device("receiver")  # (4)!
async def receiver(ctx: cosalette.DeviceContext, state: SharedState):
    while not ctx.shutdown_requested:
        reading = await read_sensor()
        state.registry.assign(reading.name, reading.id)
        yield          # (5)!
        await ctx.sleep(1.0)
```

1. `drain_events()` is the structural drain method. When `drain=None`, the framework
   calls `state_instance.drain_events()` automatically.
2. `drain=lambda s: s.registry.drain_events()` points to a specific attribute's drain
   method. Use this when the events live on a sub-object rather than on the top-level
   state.
3. `events` is a **reserved parameter name** — the framework injects the drained event
   list directly and skips normal type-based DI for it. Annotate as `list[YourEvent]`
   for type checking.
4. `@app.device` handlers must be async generators (`async def` that `yield`s).
5. `yield` is the reaction boundary. The framework drains events and calls all
   matching reactors before the next `await ctx.sleep(...)`.

### When reactors fire

| Handler type | Reaction boundary |
|---|---|
| `@app.device` | After each `yield` and once at normal completion |
| `@app.stream` | After each item processed and once at handler exit |
| `@app.telemetry` | After each successful handler return |
| `@app.command` | After each successful handler return |

**Command handler ordering:** When a command handler returns state, the framework
publishes the state to MQTT _before_ dispatching reactors. If a reactor fails, the
error is routed through the command error path (error topic + health reporting), but
the already-published command result is _not_ rolled back

Reactors do **not** fire on cancellation or unhandled exceptions.

### `drain=` forms

| Form | When to use |
|---|---|
| `drain=None` | State object has a `drain_events()` method directly |
| `drain=lambda s: s.sub.drain_events()` | Events live on a sub-object |
| `drain=lambda s: s.pop_events()` | Custom drain method name |

### Testing reactors

Reactor functions are plain async functions — call them directly in unit tests:

```python
async def test_on_registry_events_publishes() -> None:
    state = SharedState()
    state.registry.assign("living-room", 42)
    events = state.registry.drain_events()

    mock_ctx = FakeDeviceContext()
    store = MemoryStore().device_store("test")

    await on_registry_events(events=events, ctx=mock_ctx, store=store, state=state)

    assert any(t == "registry/event" for t, _ in mock_ctx.published)
```

### Registration error conditions

| Condition | Error |
|---|---|
| `StateType` not registered via `@app.state` | `ValueError` at decoration time |
| Reactor function is not `async def` | `TypeError` at decoration time |
| `drain=None` and state has no `drain_events()` | `AttributeError` at runtime |

See [ADR-043](../adr/ADR-043-domain-event-reactors-for-state-objects.md) for design
rationale and the rejected alternatives.

---

## Adapter-as-State

The adapter pattern is the right choice when shared state must satisfy a **port
Protocol** — for example, when the state interface is imported by multiple
modules that shouldn't depend on the concrete implementation.

!!! note "Prerequisites"

    This section assumes you've read the
    [Hardware Adapters](hardware-adapters.md) guide and understand the
    ports-and-adapters pattern.

### Step 1: Define a State Port

Unlike hardware ports, a state port doesn't wrap external hardware — it
defines an interface for application-internal state. Keep it focused on
what consumers need to _read_:

```python title="ports.py"
from typing import Protocol, runtime_checkable


@runtime_checkable
class AppStatePort(Protocol):
    """Shared application state between handlers."""

    @property
    def last_valve_command(self) -> str | None: ...

    @property
    def last_command_time(self) -> float | None: ...
```

!!! tip "Use properties on state ports"

    Exposing **read-only properties** on the port keeps the interface narrow.
    Write access happens through methods on the concrete class. This way, only
    the handler that writes to state needs to know about the implementation —
    readers depend only on the protocol.

### Step 2: Implement the State Class

The concrete implementation holds the mutable fields:

```python title="state.py"
import time


class AppState:
    """Concrete shared state — single instance per application."""

    def __init__(self) -> None:
        self._last_valve_command: str | None = None
        self._last_command_time: float | None = None

    @property
    def last_valve_command(self) -> str | None:
        return self._last_valve_command

    @property
    def last_command_time(self) -> float | None:
        return self._last_command_time

    def record_command(self, command: str) -> None:  # (1)!
        """Record a valve command with timestamp."""
        self._last_valve_command = command
        self._last_command_time = time.monotonic()
```

1. The `record_command` method is on the concrete class, not on the protocol. This
   means only the command handler (which needs the concrete type or a wider protocol)
   can mutate state. Telemetry handlers only read through the narrow port.

### Step 3: Register and Use

```python title="app.py"
import cosalette
from cosalette.testing import MockMqttClient

from ports import AppStatePort
from state import AppState

app = cosalette.App(name="mybridge", version="1.0.0")
app.adapter(AppStatePort, AppState)  # (1)!


@app.telemetry("sensor", interval=5.0)
async def read_sensor(state: AppStatePort) -> dict[str, object]:  # (2)!
    """Report sensor data alongside the last valve command."""
    return {
        "temperature": 22.5,
        "last_valve": state.last_valve_command,
    }


@app.command("valve")
async def handle_valve(  # (3)!
    payload: str, state: AppState
) -> dict[str, object]:
    """Handle valve commands and record the action."""
    state.record_command(payload)
    return {"valve_state": payload}


app.run(mqtt=MockMqttClient())
```

1. One instance of `AppState` is created at startup and shared across all handlers.
2. The telemetry handler declares `AppStatePort` (the protocol) — it only sees the
   read-only properties. The framework injects the same `AppState` instance because
   `AppState` satisfies the `AppStatePort` protocol structurally.
3. The command handler declares the concrete `AppState` type to access the
   `record_command()` method. Both annotations resolve to the same singleton — the
   framework uses `issubclass` matching.

!!! info "Protocol vs concrete type"

    Both `AppStatePort` and `AppState` resolve to the **same instance**. The
    framework's injection resolver checks:

    1. Exact type match
    2. `issubclass` match for Settings subclasses
    3. `issubclass` match for adapter port types

    Since `AppState` satisfies `AppStatePort` (structural subtyping), either
    annotation works. Using the protocol in readers and the concrete type in
    writers is a common pattern that maximises flexibility.

## When Not to Use the Adapter Pattern

This adapter-as-state pattern works well for **protocol-typed shared state**. Consider
alternatives when:

| Scenario | Better approach |
|----------|----------------|
| No port Protocol needed (simple settings-derived object) | Use [`@app.state`](#app-state-factory) — simpler and no protocol required |
| State needs to survive restarts | Persist to disk/database in the lifespan teardown |
| State needs thread safety | Use `asyncio.Lock` inside the state class |
| Per-device scoped state (separate instance per device name) | Manages state internally with a `dict[str, ...]` keyed by device name |
| State depends on Settings to construct | Use [`@app.state`](#app-state-factory) with a settings parameter |

## Complete Example

Here's a self-contained application that demonstrates the full pattern:

```python title="shared_state_example.py"
"""Shared state between handlers — complete example."""

import random
import time
from typing import Protocol, runtime_checkable

import cosalette
from cosalette.testing import MockMqttClient


# --- Port ---
@runtime_checkable
class AppStatePort(Protocol):
    """Read-only view of shared application state."""

    @property
    def last_valve_command(self) -> str | None: ...

    @property
    def last_command_time(self) -> float | None: ...


# --- Implementation ---
class AppState:
    """Mutable shared state — created once at startup."""

    def __init__(self) -> None:
        self._last_valve_command: str | None = None
        self._last_command_time: float | None = None

    @property
    def last_valve_command(self) -> str | None:
        return self._last_valve_command

    @property
    def last_command_time(self) -> float | None:
        return self._last_command_time

    def record_command(self, command: str) -> None:
        self._last_valve_command = command
        self._last_command_time = time.monotonic()


# --- App ---
app = cosalette.App(name="stateapp", version="1.0.0")
app.adapter(AppStatePort, AppState)


@app.telemetry("sensor", interval=3.0)
async def read_sensor(state: AppStatePort) -> dict[str, object]:
    """Reads sensor + reports last valve command."""
    return {
        "temperature": round(20.0 + random.uniform(-2, 2), 1),
        "last_valve": state.last_valve_command,
    }


@app.command("valve")
async def handle_valve(payload: str, state: AppState) -> dict[str, object]:
    """Receives valve commands and records them in shared state."""
    state.record_command(payload)
    return {"valve_state": payload}


app.run(mqtt=MockMqttClient())
```

---

## See Also

- [Hardware Adapters](hardware-adapters.md) — registration forms, dry-run swapping,
  factory callables
- [Hexagonal Architecture](../concepts/hexagonal.md) — conceptual foundation
  for ports and adapters
- [Build a Telemetry Device](telemetry-device.md#initialisation-callbacks-init) —
  the `init=` callback provides a lighter alternative for simple per-handler
  state (no adapter needed)
- [Build a Full App](../getting-started/full-app.md) — capstone guide combining all patterns
