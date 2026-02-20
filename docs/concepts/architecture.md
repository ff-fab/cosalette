---
icon: material/city-variant-outline
---

# Architecture

Cosalette follows the **composition root** pattern: a single `App` object acts as
the wiring point where all infrastructure, devices, and lifespan logic come
together. The framework calls *your* code — not the other way around.

## The FastAPI Analogy

If you have used FastAPI, the programming model will feel familiar:

```python
import cosalette

app = cosalette.App(name="velux2mqtt", version="0.3.0")  # (1)!

@app.device("blind")  # (2)!
async def blind(ctx: cosalette.DeviceContext) -> None:
    ...

@app.command("valve")  # (3)!
async def handle_valve(
    topic: str, payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    return {"state": payload}

@app.telemetry("temp", interval=60)  # (4)!
async def temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"celsius": 21.5}

app.adapter(GpioPort, RpiGpioAdapter, dry_run=MockGpio)  # (5)!

app.run()  # (6)!
```

1. **Composition root** — the `App` is constructed once at module level.
2. **Device decorator** — `@app.device` registers a long-running command & control coroutine (escape hatch).
3. **Command decorator** — `@app.command` registers a per-message command handler (recommended for most command devices).
4. **Telemetry decorator** — `@app.telemetry` registers a periodic polling function.
5. **Adapter binding** — maps a Protocol port to a concrete implementation (with optional dry-run variant).
6. **Entry point** — builds the CLI, parses arguments, and runs the async lifecycle.

This is **Inversion of Control (IoC)**: the framework owns the `asyncio` event
loop, signal handling, MQTT connection management, and task supervision. Your
device functions and hooks are *called back* by the framework at the appropriate
point in the [lifecycle](lifecycle.md).

## Decorator-Based Registration API

All registration happens at import time through decorators and method calls on
the `App` instance:

| API                       | Purpose                                      |
|---------------------------|----------------------------------------------|
| `@app.command(name)`       | Register a per-message command handler (recommended) |
| `@app.device(name)`       | Register a long-running command & control coroutine  |
| `@app.telemetry(name, interval=N)` | Register a periodic telemetry device |
| `App(lifespan=fn)`        | Register a lifespan context manager           |
| `app.adapter(Port, Impl)` | Bind a Protocol port to a concrete adapter   |

!!! tip "No base classes"
    Device functions are plain `async def` coroutines. There is no
    `BaseDevice` to inherit from — handlers declare only the parameters
    they need via type annotations, and the framework injects them
    automatically.

## Context Injection

The framework uses **signature-based injection** — device handlers declare only
the parameters they need via type annotations, and the framework provides them
automatically.

=== "DeviceContext"

    Injected into `@app.command`, `@app.device`, and `@app.telemetry` functions
    when they declare a `ctx: DeviceContext` parameter. Provides device-scoped
    MQTT publishing, command registration, shutdown-aware sleep, and adapter
    resolution.

    For `@app.command` handlers, dependencies are injected by type annotation:

    ```python
    @app.command("relay")
    async def handle_relay(
        topic: str, payload: str, ctx: cosalette.DeviceContext
    ) -> dict[str, object]:
        gpio = ctx.adapter(GpioPort)  # resolve adapter
        return {"on": gpio.read()}
    ```

    For `@app.device` functions, the context is the sole parameter:

    ```python
    @app.device("relay")
    async def relay(ctx: cosalette.DeviceContext) -> None:
        gpio = ctx.adapter(GpioPort)  # resolve adapter
        while not ctx.shutdown_requested:
            await ctx.publish_state({"on": gpio.read()})
            await ctx.sleep(5)
    ```

=== "Zero-arg handler"

    Handlers that don't need context can omit all parameters. This is common
    for simple telemetry devices:

    ```python
    @app.telemetry("temp", interval=60)
    async def temp() -> dict[str, object]:
        return {"celsius": await read_sensor()}
    ```

=== "Selective injection"

    Handlers can request specific injectable types — `Settings`, `logging.Logger`,
    `ClockPort`, `asyncio.Event`, or adapter port types:

    ```python
    @app.telemetry("temp", interval=60)
    async def temp(settings: Settings) -> dict[str, object]:
        # Only settings injected — no DeviceContext needed
        return {"celsius": 21.5, "unit": settings.unit}
    ```

=== "AppContext (lifespan)"

    Injected into the lifespan function. Provides settings and adapter
    resolution but *not* device-scoped features (no publish, no sleep,
    no on_command).

    ```python
    @asynccontextmanager
    async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
        db = ctx.adapter(DatabasePort)
        await db.warm_cache()
        yield
        await db.close()
    ```

## Four-Phase Orchestration

The `App._run_async()` method orchestrates the full application lifecycle in
four sequential phases:

```mermaid
graph LR
    A["① Bootstrap"] --> B["② Registration"]
    B --> C["③ Run"]
    C --> D["④ Teardown"]
```

| Phase          | What happens                                                           |
|----------------|------------------------------------------------------------------------|
| **Bootstrap**  | Load settings, configure logging, resolve adapters, connect MQTT       |
| **Registration** | Install signal handlers, publish availability, build contexts, wire router |
| **Run**        | Execute lifespan startup, launch device tasks, `await shutdown_event.wait()` |
| **Teardown**   | Execute lifespan teardown, cancel tasks, publish offline, disconnect MQTT  |

Each phase is detailed in the [Application Lifecycle](lifecycle.md) concept page.

## Test Seams

`_run_async()` accepts four optional keyword arguments specifically designed
as **test seams** — injection points that let tests bypass real infrastructure:

```python
await app._run_async(
    settings=make_settings(),        # skip env/dotenv loading
    shutdown_event=asyncio.Event(),  # manual shutdown control
    mqtt=MockMqttClient(),           # in-memory MQTT double
    clock=FakeClock(),               # deterministic time
)
```

This design means integration tests run entirely in-process with no broker,
no real clock, and no signal handlers. See [Testing](testing.md) for the
full testing strategy.

!!! info "Design principle — Dependency Injection over Service Locator"
    The test-seam parameters follow constructor/method injection rather than
    a global service locator. Each dependency is explicit and visible at the
    call site, making tests self-documenting.

## Why a Composition Root?

The composition root pattern (as described in
[ADR-001](../adr/ADR-001-framework-architecture-style.md)) solves several
problems specific to IoT bridge daemons:

1. **Single wiring point** — all adapters, devices, and lifespan are assembled in
   one place, making the dependency graph explicit.
2. **Testability** — inject doubles at the root without modifying device code.
3. **Dry-run mode** — swap adapter implementations globally by setting one flag.
4. **Discoverability** — reading `app.py` reveals the full application topology.

---

## See Also

- [Application Lifecycle](lifecycle.md) — detailed phase-by-phase walkthrough
- [Hexagonal Architecture](hexagonal.md) — ports, adapters, and the dependency rule
- [Device Archetypes](device-archetypes.md) — the three first-class device types
- [Testing](testing.md) — test seams and the `AppHarness`
- [ADR-001 — Framework Architecture Style](../adr/ADR-001-framework-architecture-style.md)
- [ADR-005 — CLI Framework](../adr/ADR-005-cli-framework.md)
- [ADR-006 — Hexagonal Architecture](../adr/ADR-006-hexagonal-architecture.md)
