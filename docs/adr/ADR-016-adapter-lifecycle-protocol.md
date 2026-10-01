---
status: Accepted
date: 2026-02-26
impact: moderate
tags: [lifecycle]
---

# ADR-016: Adapter Lifecycle Protocol

## Status

Accepted **Date:** 2026-02-26 | Amended **Date:** 2026-10-01

## Context

cosalette adapters often need initialisation and cleanup — opening serial ports,
connecting to databases, warming up hardware. The existing `lifespan=` hook handles
this, but for the common case of "enter adapter on startup, exit on shutdown" the
user must write boilerplate:

```python
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    meter = ctx.adapter(GasMeterPort)
    meter.connect(ctx.settings.serial_port)
    yield
    meter.close()
```

This is ceremony. The adapter already knows how to manage its own lifecycle — it
just needs the framework to call `__aenter__` and `__aexit__` at the right time.

Python's async context manager protocol (`__aenter__`/`__aexit__`) is the standard
mechanism for paired resource management. Many libraries already implement it (e.g.
`aiosqlite`, `aiohttp.ClientSession`, `serial_asyncio`).

## Decision

**Auto-manage adapters implementing `__aenter__`/`__aexit__`** using
`contextlib.AsyncExitStack`, because this eliminates boilerplate for the common case
while preserving the `lifespan=` hook for advanced orchestration.

### Detection

The framework uses duck-typing to detect lifecycle adapters:

```python
def _is_async_context_manager(obj: object) -> bool:
    return hasattr(obj, "__aenter__") and hasattr(obj, "__aexit__")
```

This is intentionally `hasattr`-based rather than `isinstance(..., AbstractAsyncContextManager)` — the ABC requires explicit registration, while duck-typing
is more inclusive and Pythonic.

### Execution order

```text
MQTT Connect
    ↓
Enter lifecycle adapters (AsyncExitStack)   ← NEW
    ↓
Enter lifespan (user code before yield)
    ↓
Device tasks run
    ↓
Exit lifespan (user code after yield)
    ↓
Exit lifecycle adapters (LIFO via AsyncExitStack)   ← NEW
    ↓
MQTT Disconnect
```

Adapters are entered **before** the lifespan and exited **after** it. This means:

- Lifespan code can safely use entered adapters (e.g. run queries on an
  already-connected database adapter)
- Adapter cleanup runs after lifespan teardown, so lifespan shutdown code can still
  use adapter resources

### Only async context manager protocol

The framework detects only `__aenter__`/`__aexit__`. It does not look for named
methods like `connect()`/`close()` or `start()`/`stop()`. This keeps detection
simple and aligns with Python's standard protocol.

## Decision Drivers

- Reducing boilerplate for the most common adapter lifecycle pattern
- Aligning with Python's standard resource management protocol (PEP 343)
- Preserving backward compatibility — existing apps with `lifespan=` continue
  working unchanged
- Exception safety via `AsyncExitStack` (LIFO ordering, guaranteed cleanup)

## Considered Options

### Option 1: Named lifecycle methods (`connect`/`close`)

Detect `connect()`/`close()` or `start()`/`stop()` methods on adapters and call
them automatically.

- *Advantages:* Works with existing synchronous adapters. No protocol changes needed.
- *Disadvantages:* Ambiguous — many classes have `close()` methods that shouldn't be
  called by the framework. No standard for which method names to detect. Synchronous
  methods block the event loop.

### Option 2: Marker base class or decorator

Require adapters to inherit from a `LifecycleAdapter` base or apply a `@managed`
decorator.

- *Advantages:* Explicit opt-in. No false positives.
- *Disadvantages:* Inheritance conflicts with protocol-based architecture
  ([ADR-006](ADR-006-hexagonal-architecture.md)). Adds framework coupling to adapter
  implementations.

### Option 3: Async context manager protocol (chosen)

Detect `__aenter__`/`__aexit__` and manage via `AsyncExitStack`.

- *Advantages:* Standard Python protocol. Many libraries already implement it.
  `AsyncExitStack` provides LIFO ordering and exception safety. No framework coupling.
  Duck-typing detection aligns with the protocol-based architecture.
- *Disadvantages:* Sync-only adapters need wrapping. Implicit — adding `__aenter__`
  to an adapter changes its startup behavior.

## Consequences

### Positive

- The common case (adapter with paired init/cleanup) needs no `lifespan=` hook at all
- Adapters that already implement `__aenter__`/`__aexit__` (e.g. `aiosqlite`,
  `aiohttp.ClientSession`) work automatically
- `AsyncExitStack` guarantees LIFO cleanup ordering and handles exceptions in
  individual adapter teardowns without blocking others
- Fully backward compatible — existing `lifespan=` hooks work identically

### Negative

- Two lifecycle mechanisms to document and understand (adapter protocol vs. lifespan
  hook)
- Implicit behavior — adding `__aenter__`/`__aexit__` to an adapter silently changes
  when it is entered/exited
- No control over adapter entry order (dict iteration order, which is insertion order)

## Amendment (2026-10-01) — Additive

**Rationale:** The documented execution order starts with 'MQTT Connect', but MqttClient.start() only schedules the connection loop and run_lifespan_and_devices launched entity tasks immediately. I/O-free telemetry handlers (computed values, timers) therefore published before CONNACK on every start, and each failed publish surfaced as an ERROR log, a WARNING with traceback from the error publisher, an error-topic publish attempt and an 'error' health status (reported by early adopter wiz2mqtt). This amendment restores the documented order with a bounded barrier.

### Additional Sub-Decision: Bounded First-Connect Barrier

Entity startup in the run phase waits for the first MQTT connect before launching device, telemetry, periodic and stream tasks together. The barrier is an `asyncio.Event` set by a connect callback registered through the existing `MqttConnectAware.add_connect_callback` immediately after the ADR-012 reannounce callback; callbacks run sequentially in registration order, so the gate opens only after availability, registry and heartbeat have been announced. No new port or protocol is introduced (ADR-006).

The wait is bounded by `App(startup_connect_timeout: float | None = 10.0)`, measured with the injected `ClockPort`. On timeout one `WARNING` is logged and tasks start anyway, so handlers with local side effects keep working during a broker outage; shutdown ends the wait early without a warning. `None` disables the barrier, and values <= 0 are rejected like the other interval kwargs. Adapters that are not connect-aware (`MockMqttClient`, `AppHarness`, `NullMqttClient`) are always considered connected and skip the barrier.

```text
MQTT start (connection scheduled)
    ↓
Enter lifecycle adapters → enter lifespan → startup health checks
    ↓
Wait for first connect + reannounce (≤ startup_connect_timeout)
    ↓
Device / telemetry / periodic / stream tasks run
```

### Additional Positive Consequences

- I/O-free handlers no longer race the broker connection on every start; the first state publish follows the availability announce.
- Existing unit tests are unaffected because the in-memory doubles are not connect-aware.

### Additional Negative Consequences

- Startup with an unreachable broker is delayed by up to startup_connect_timeout (10 s by default) before handlers run.
- A telemetry tick missed during a later reconnect is still caught up only on the next interval; waking sleepers on reconnect is tracked separately.
