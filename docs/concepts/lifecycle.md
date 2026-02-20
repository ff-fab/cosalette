---
icon: material/refresh
---

# Application Lifecycle

The cosalette lifecycle is a **four-phase orchestration** that transforms a
collection of decorated functions into a running MQTT daemon. Each phase has
clear responsibilities and well-defined boundaries.

## Phase Overview

```mermaid
sequenceDiagram
    participant CLI as CLI (Typer)
    participant App
    participant MQTT as MQTT Client
    participant Devices
    participant Health as HealthReporter

    Note over CLI,Health: Phase 1 — Bootstrap
    CLI->>App: parse args, load Settings
    App->>App: configure_logging()
    App->>App: resolve_adapters()
    App->>MQTT: MqttClient(settings, will=LWT)
    App->>Health: HealthReporter(mqtt, clock)
    App->>MQTT: mqtt.start()

    Note over CLI,Health: Phase 2 — Registration
    App->>App: install signal handlers (SIGTERM, SIGINT)
    App->>Health: publish_device_available() × N
    App->>App: build DeviceContexts
    App->>App: wire TopicRouter
    App->>MQTT: subscribe to {prefix}/{device}/set × N
    App->>MQTT: on_message(router.route)

    Note over CLI,Health: Phase 3 — Run
    App->>App: enter lifespan (startup)
    App->>Devices: create_task() × N
    App->>App: await shutdown_event.wait()

    Note over CLI,Health: Phase 4 — Teardown
    App->>Devices: cancel tasks
    App->>App: exit lifespan (shutdown)
    App->>Health: shutdown() → publish offline × N
    App->>MQTT: mqtt.stop()
```

## Phase 1 — Bootstrap

Bootstrap prepares all infrastructure before any device code runs:

1. **Settings** — `Settings` instance is created from environment variables,
   `.env` file, and CLI overrides (see [Configuration](configuration.md))
2. **Logging** — `configure_logging()` clears existing handlers, installs
   JSON or text formatter on stderr (+ optional rotating file handler)
3. **Adapters** — `_resolve_adapters()` instantiates all registered adapters,
   choosing dry-run variants when `--dry-run` is active
4. **Clock** — `SystemClock()` (or injected `FakeClock` in tests)
5. **MQTT client** — `MqttClient(settings.mqtt, will=build_will_config(prefix))`
   with the LWT pre-configured for crash detection
6. **Services** — `HealthReporter` and `ErrorPublisher` are created with
   references to the MQTT port and clock
7. **Connect** — `mqtt.start()` begins the background connection loop

```python
# Simplified bootstrap (framework internals)
resolved_settings = settings or self._settings_class()
configure_logging(resolved_settings.logging, service=self._name, version=self._version)

resolved_adapters = self._resolve_adapters()
resolved_clock = clock or SystemClock()

mqtt = self._create_mqtt(mqtt, resolved_settings)
health_reporter, error_publisher = self._create_services(mqtt, resolved_clock)

if isinstance(mqtt, MqttLifecycle):
    await mqtt.start()
```

!!! info "LWT registration"
    The Last Will and Testament is set *during* MQTT connection, not after.
    `build_will_config()` creates a `WillConfig` that the `MqttClient` passes
    to aiomqtt's `Client` constructor. If the client crashes, the broker
    publishes `"offline"` to `{prefix}/status` automatically.

## Phase 2 — Registration

Registration wires the device graph into the running infrastructure:

1. **Signal handlers** — `SIGTERM` and `SIGINT` both call `shutdown_event.set()`.
   This handles both `Ctrl+C` during development and `docker stop` in production.
2. **Device availability** — `publish_device_available()` sends `"online"` to
   `{prefix}/{device}/availability` for every registered device
3. **DeviceContexts** — one `DeviceContext` per device, pre-configured with
   the device name, MQTT port, settings, adapters, clock, and shutdown event
4. **TopicRouter** — command handler proxies are registered for each
   `@app.device` function; the router maps `{prefix}/{device}/set` to handlers
5. **Subscriptions** — MQTT subscriptions for all command topics
6. **Message wiring** — `mqtt.on_message(router.route)` connects inbound
   messages to the router

## Phase 3 — Run

The run phase is where device code executes:

1. **AppContext** — created with settings and resolved adapters
2. **Enter lifespan** — the lifespan context manager's startup code runs
   (everything before `yield`), receiving the `AppContext`
3. **Device tasks** — each device becomes an `asyncio.Task`:
   - `@app.device` → `_run_device()` (runs the coroutine directly)
   - `@app.telemetry` → `_run_telemetry()` (polling loop with `ctx.sleep`)
4. **Block** — `await shutdown_event.wait()` suspends the orchestrator until
   a shutdown signal arrives

```python
# Phase 3 internals
app_context = AppContext(settings=resolved_settings, adapters=resolved_adapters)
async with self._lifespan(app_context):
    device_tasks = self._start_device_tasks(contexts, error_publisher)
    await shutdown_event.wait()
```

### Lifespan Execution Windows

| Phase           | Runs after                | Runs before              |
|-----------------|---------------------------|--------------------------|
| Lifespan enter  | MQTT connected + subscribed | Device tasks started   |
| Lifespan exit   | Device tasks cancelled   | MQTT disconnected        |

This ordering is intentional: the lifespan startup code can warm caches or
initialise resources *before* devices begin, and the shutdown code can
flush state *after* devices have stopped.

### Error Handling in Lifespan

If the lifespan's startup code (before `yield`) raises an exception, the
application aborts — no device tasks are started. If the shutdown code
(after `yield`) raises, the exception is logged and shutdown continues.

```python
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    # Startup — critical failure aborts the app
    meter = ctx.adapter(GasMeterPort)
    meter.connect(ctx.settings.serial_port)
    yield
    # Shutdown — errors are logged but don’t prevent MQTT disconnect
    meter.close()
```

!!! tip "try/finally for guaranteed cleanup"
    Wrap `yield` in `try/finally` when managing multiple resources to ensure
    all cleanup runs, even if device shutdown raises.

## Phase 4 — Teardown

Teardown runs in reverse order to bootstrap:

1. **Cancel device tasks** — all device `asyncio.Task`s are cancelled;
   `asyncio.gather` waits for graceful completion
2. **Exit lifespan** — the lifespan context manager's shutdown code runs
   (everything after `yield`)
3. **Health offline** — `HealthReporter.shutdown()` publishes `"offline"` to
   each device's availability topic and to `{prefix}/status`
4. **MQTT disconnect** — `mqtt.stop()` cancels the connection loop

```python
# Phase 4 internals (simplified)
# The lifespan context manager wraps the device phase:
#   async with self._lifespan(app_context):
#       ... devices run ...
# When the `async with` block exits, shutdown code runs.

await self._cancel_tasks(device_tasks)
# lifespan __aexit__ runs here (code after yield)
await health_reporter.shutdown()

if isinstance(mqtt, MqttLifecycle):
    await mqtt.stop()
```

## Signal Handling

The framework installs handlers for both `SIGTERM` and `SIGINT`:

```python
loop = asyncio.get_running_loop()
for sig in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(sig, event.set)
```

| Signal    | Source                | Effect                      |
|-----------|----------------------|------------------------------|
| `SIGINT`  | `Ctrl+C` in terminal | Sets `shutdown_event`        |
| `SIGTERM` | `docker stop`, systemd | Sets `shutdown_event`      |

Both signals trigger the same graceful shutdown path. No special handling
is needed for different deployment environments.

## Graceful Shutdown Pattern

Device code cooperates with shutdown via the `ctx.shutdown_requested` +
`ctx.sleep()` pattern:

```python
@app.device("sensor")
async def sensor(ctx: cosalette.DeviceContext) -> None:
    while not ctx.shutdown_requested:
        data = await read_sensor()
        await ctx.publish_state(data)
        await ctx.sleep(30)  # returns early on shutdown
    # cleanup code runs here
```

`ctx.sleep()` internally races an `asyncio.sleep` against the shutdown event.
When shutdown is signalled, sleep returns immediately (without raising), and
the `while` loop exits naturally.

## CLI Orchestration

The full path from command line to async lifecycle:

```
$ myapp --log-level DEBUG
    │
    ├── Typer parses CLI flags
    ├── loads Settings from env + .env
    ├── applies CLI overrides (--log-level, --log-format)
    └── asyncio.run(app._run_async(settings=...))
            │
            ├── Phase 1: Bootstrap
            ├── Phase 2: Registration
            ├── Phase 3: Run (enter lifespan → devices → exit lifespan)
            └── Phase 4: Teardown
```

---

## See Also

- [Architecture](architecture.md) — composition root and test seams
- [Device Archetypes](device-archetypes.md) — how devices execute in Phase 3
- [Health & Availability](health-reporting.md) — availability publishing in Phases 2 and 4
- [Error Handling](error-handling.md) — error isolation during device execution
- [ADR-001 — Framework Architecture Style](../adr/ADR-001-framework-architecture-style.md)
- [ADR-005 — CLI Framework](../adr/ADR-005-cli-framework.md)
