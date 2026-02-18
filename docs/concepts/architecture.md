---
icon: material/city-variant-outline
---

# Architecture

Cosalette follows the **composition root** pattern: a single `App` object acts as
the wiring point where all infrastructure, devices, and lifecycle hooks come
together. The framework calls *your* code — not the other way around.

## The FastAPI Analogy

If you have used FastAPI, the programming model will feel familiar:

```python
import cosalette

app = cosalette.App(name="velux2mqtt", version="0.3.0")  # (1)!

@app.device("blind")  # (2)!
async def blind(ctx: cosalette.DeviceContext) -> None:
    ...

@app.telemetry("temp", interval=60)  # (3)!
async def temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"celsius": 21.5}

@app.on_startup  # (4)!
async def setup(ctx: cosalette.AppContext) -> None:
    ...

app.adapter(GpioPort, RpiGpioAdapter, dry_run=MockGpio)  # (5)!

app.run()  # (6)!
```

1. **Composition root** — the `App` is constructed once at module level.
2. **Decorator registration** — `@app.device` registers a command & control coroutine.
3. **Telemetry decorator** — `@app.telemetry` registers a periodic polling function.
4. **Lifecycle hook** — `@app.on_startup` runs after MQTT connects, before devices start.
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
| `@app.device(name)`       | Register a command & control device           |
| `@app.telemetry(name, interval=N)` | Register a periodic telemetry device |
| `@app.on_startup`         | Register a pre-device startup hook            |
| `@app.on_shutdown`        | Register a post-device shutdown hook          |
| `app.adapter(Port, Impl)` | Bind a Protocol port to a concrete adapter   |

!!! tip "No base classes"
    Device functions are plain `async def` coroutines. There is no
    `BaseDevice` to inherit from — just accept a `DeviceContext` parameter
    and the framework handles the rest.

## Context Injection

The framework injects two context types depending on the registration point:

=== "DeviceContext"

    Injected into `@app.device` and `@app.telemetry` functions. Provides
    device-scoped MQTT publishing, command registration, shutdown-aware
    sleep, and adapter resolution.

    ```python
    @app.device("relay")
    async def relay(ctx: cosalette.DeviceContext) -> None:
        gpio = ctx.adapter(GpioPort)  # resolve adapter
        while not ctx.shutdown_requested:
            await ctx.publish_state({"on": gpio.read()})
            await ctx.sleep(5)
        ```

=== "AppContext"

    Injected into `@app.on_startup` and `@app.on_shutdown` hooks. Provides
    settings and adapter resolution but *not* device-scoped features (no
    publish, no sleep, no on_command).

    ```python
    @app.on_startup
    async def seed_cache(ctx: cosalette.AppContext) -> None:
        db = ctx.adapter(DatabasePort)
        await db.warm_cache()
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
| **Run**        | Execute startup hooks, launch device tasks, `await shutdown_event.wait()` |
| **Teardown**   | Execute shutdown hooks, cancel tasks, publish offline, disconnect MQTT  |

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

1. **Single wiring point** — all adapters, devices, and hooks are assembled in
   one place, making the dependency graph explicit.
2. **Testability** — inject doubles at the root without modifying device code.
3. **Dry-run mode** — swap adapter implementations globally by setting one flag.
4. **Discoverability** — reading `app.py` reveals the full application topology.

---

## See Also

- [Application Lifecycle](lifecycle.md) — detailed phase-by-phase walkthrough
- [Hexagonal Architecture](hexagonal.md) — ports, adapters, and the dependency rule
- [Device Archetypes](device-archetypes.md) — the two first-class device types
- [Testing](testing.md) — test seams and the `AppHarness`
- [ADR-001 — Framework Architecture Style](../adr/ADR-001-framework-architecture-style.md)
- [ADR-005 — CLI Framework](../adr/ADR-005-cli-framework.md)
- [ADR-006 — Hexagonal Architecture](../adr/ADR-006-hexagonal-architecture.md)
