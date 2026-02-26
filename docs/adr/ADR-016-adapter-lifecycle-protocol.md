# ADR-016: Adapter Lifecycle Protocol

## Status

Accepted **Date:** 2026-02-26

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

_2026-02-26_
