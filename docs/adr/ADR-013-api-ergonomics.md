# ADR-013: API Ergonomics — Lifespan, Runner, and Injection

## Status

Accepted **Date:** 2026-02-20

## Context

The cosalette framework's original API required significant boilerplate for common
operations. Three pain points were identified:

1. **Manual asyncio orchestration** — every application needed `asyncio.run(main())`,
   signal handling (`SIGTERM`, `SIGINT`), and a custom async `main()` function.
   This is infrastructure, not domain logic.

2. **Separate startup/shutdown hooks** — `@app.on_startup` and `@app.on_shutdown`
   decorators split paired resource lifecycle code across two unrelated functions.
   Developers had to manually ensure that every resource opened in startup was closed
   in shutdown — a structural problem that context managers solve naturally.

3. **Mandatory `DeviceContext` parameter** — every handler had to accept
   `ctx: DeviceContext` even when the handler didn't use it (e.g., simple telemetry
   returning a dict). This violated YAGNI and added visual noise.

The cosalette motto is "FastAPI for MQTT" — the API should be as concise as FastAPI's
decorator-based routing. The original API fell short of that goal.

## Decision

Introduce three complementary API improvements:

### 1. Public `app.run()` sync entrypoint

Use a single `app.run()` call to replace manual asyncio orchestration:

```python
app.run(mqtt=MqttClient(), settings=my_settings)
```

`app.run()` wraps `asyncio.run()`, installs signal handlers for `SIGTERM`/`SIGINT`,
and suppresses `KeyboardInterrupt`. A companion `app.cli()` method returns the Typer
CLI application for direct CLI integration.

**Rationale:** Encapsulates lifecycle wiring that every application repeats identically.
Follows the FastAPI pattern where `uvicorn.run(app)` is one line.

### 2. Lifespan context manager

Replace `@app.on_startup` / `@app.on_shutdown` with a single lifespan function
passed to the `App` constructor:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    meter = ctx.adapter(GasMeterPort)
    meter.connect(ctx.settings.serial_port)
    yield  # devices run here
    meter.close()

app = cosalette.App(name="gas2mqtt", version="1.0.0", lifespan=lifespan)
```

**Rationale:** The `@asynccontextmanager` pattern pairs resource acquisition with
cleanup in a single function — structurally preventing the "forgot to close in
shutdown" class of bugs. This is the same pattern adopted by Starlette/FastAPI
(superseding their earlier `on_startup`/`on_shutdown` events). The `AppContext`
parameter provides access to settings and adapters but deliberately excludes
device-specific operations (publish, sleep, on_command).

### 3. Signature-based handler injection

Handlers declare only the parameters they need via type annotations. The framework
inspects signatures at **registration time** (fail-fast validation) and resolves
injectable types at **call time**:

```python
# Zero-arg — simplest telemetry
@app.telemetry("temp", interval=60)
async def temp() -> dict[str, object]:
    return {"celsius": 22.5}

# Cherry-pick specific dependencies
@app.device("valve")
async def valve(logger: logging.Logger, settings: Settings) -> None:
    ...

# Full context — backwards compatible
@app.device("relay")
async def relay(ctx: DeviceContext) -> None:
    ...
```

**Injectable types:**

| Type annotation    | Source                         |
| ------------------ | ------------------------------ |
| `DeviceContext`    | Full per-device context        |
| `Settings`         | `ctx.settings` (+ subclasses)  |
| `logging.Logger`   | `cosalette.<device_name>`      |
| `ClockPort`        | `ctx.clock`                    |
| `asyncio.Event`    | Shutdown event                 |
| Adapter port types | `ctx.adapter(PortType)`        |

**Rationale:** Follows the Dependency Injection (DI) principle — handlers declare
dependencies, the framework provides them. This is the same approach used by
FastAPI's `Depends()` and pytest's fixture injection, adapted for type-annotation-based
resolution. Missing annotations raise `TypeError` at registration time (fail-fast),
and unknown types are deferred to call time (supporting adapters registered in any
order).

## Decision Drivers

- **"FastAPI for MQTT" motto** — API conciseness is a first-class goal
- **Paired resource safety** — startup-shutdown pairs should be structural, not
  disciplinary
- **YAGNI** — handlers shouldn't declare unused parameters
- **Fail-fast** — invalid configurations should error at registration, not at runtime
- **Backwards compatibility** — existing `handler(ctx: DeviceContext)` code must
  continue working without changes

## Considered Options

### Option A: Runner only (`app.run()`)

Add only the sync entrypoint, leave startup/shutdown hooks and handler signatures
unchanged.

- *Advantages:* Minimal change, lowest risk
- *Disadvantages:* Doesn't address the paired resource or YAGNI problems

### Option B: Runner + Lifespan

Add `app.run()` and the lifespan context manager, keep mandatory `ctx` parameter.

- *Advantages:* Solves the two most impactful problems (asyncio boilerplate +
  paired resources)
- *Disadvantages:* Handlers still require `ctx` even when unused

### Option C: Runner + Lifespan + Injection (chosen)

All three improvements together.

- *Advantages:* Complete solution — addresses all three pain points. Each feature
  is orthogonal and independently useful.
- *Disadvantages:* Larger change surface. Injection adds a reflection-based system
  (`inspect.signature`, `get_type_hints`) that must be maintained.

### Why not `Depends()` style?

FastAPI uses explicit `Depends(get_db)` for dependency injection. This requires
the caller to name a provider function for each dependency. Type-annotation-based
injection (as implemented) is simpler because the framework already knows what
types are available — there's no user-defined provider chain. The set of injectable
types is fixed by the framework, making explicit `Depends()` unnecessary overhead.

## Consequences

### Positive

- Minimal application (`main.py`) reduces to 5-10 lines including imports
- Resource safety is structural — the context manager pattern prevents forgotten
  cleanup
- Simple telemetry handlers become one-liners:
  `async def temp() -> dict[str, object]: return {"celsius": 22.5}`
- Existing code (`handler(ctx: DeviceContext)`) works unchanged — injection is
  backwards compatible
- Fail-fast validation catches annotation errors at import time, not at midnight
  during device polling
- The injection system is extensible — new injectable types can be added to the
  framework without changing handler code

### Negative

- The `@app.on_startup` / `@app.on_shutdown` API is removed (breaking change) —
  but the framework has no external users yet, so migration cost is zero
- The injection module uses `inspect.signature` and `typing.get_type_hints` which
  add ~50μs per handler registration — negligible for the 2-10 handlers a typical
  application registers
- Developers must understand Python type annotations to use injection — but this
  is already a project requirement (mypy strict mode)

### Migration

| Old API | New API |
| ------- | ------- |
| `asyncio.run(main())` + signal handling | `app.run()` |
| `@app.on_startup` / `@app.on_shutdown` | `App(lifespan=my_lifespan)` |
| `async def handler(ctx: DeviceContext)` | `async def handler()` (or keep `ctx`) |

_2026-02-20_
