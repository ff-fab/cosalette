---
description: "cosalette framework API reference"
applyTo: "**"
---

# cosalette Framework Reference

> **Version:** 0.3.0
> **PyPI:** `pip install cosalette` / `uv add cosalette`
> **Docs:** <https://ff-fab.github.io/cosalette/>
> **Source:** <https://github.com/ff-fab/cosalette>

## Public API

Everything is importable from `cosalette` directly — no private module imports.

### App & Context

| Export          | Type       | Description                                                    |
| --------------- | ---------- | -------------------------------------------------------------- |
| `App`           | class      | Composition root and application orchestrator                  |
| `AppContext`    | class      | Context for lifespan hooks (settings + adapter resolution)     |
| `DeviceContext` | class      | Per-device runtime context injected into device handlers       |
| `IntervalSpec`  | type alias | `float \| Callable[[Settings], float]` — deferred interval     |
| `LifespanFunc`  | type alias | `Callable[[AppContext], AbstractAsyncContextManager[None]]`    |
| `Command`       | dataclass  | Inbound MQTT command: topic, payload, sub_topic, timestamp     |
| `CronSchedule`  | class      | Quartz-compatible cron expression parser                       |
| `SubEntityContext`| class    | Scoped context for sub-entity lifecycle within a device        |
| `SettingRef`    | class      | Inspectable settings reference: field name + callable access   |
| `setting_ref`   | function   | Factory for `SettingRef` instances: `setting_ref("field")`     |

### Clock

| Export        | Type     | Description                        |
| ------------- | -------- | ---------------------------------- |
| `ClockPort`   | Protocol | `now() -> float` (monotonic)       |
| `SystemClock` | class    | Production adapter (`time.monotonic()`) |

### MQTT

| Export              | Type       | Description                                          |
| ------------------- | ---------- | ---------------------------------------------------- |
| `MqttPort`          | Protocol   | `publish()`, `subscribe()` contract                  |
| `MqttClient`        | class      | Production adapter (aiomqtt, auto-reconnect)         |
| `MockMqttClient`    | class      | Test double — records publishes, simulates inbound   |
| `NullMqttClient`    | class      | Silent no-op adapter                                 |
| `MqttLifecycle`     | Protocol   | `start()` / `stop()` lifecycle                       |
| `MqttMessageHandler`| Protocol   | `on_message(callback)` dispatch                      |
| `MessageCallback`   | type alias | `Callable[[str, str], Awaitable[None]]`              |
| `WillConfig`        | dataclass  | LWT: topic, payload, qos, retain                     |

### Errors

| Export              | Type       | Description                                          |
| ------------------- | ---------- | ---------------------------------------------------- |
| `ErrorPayload`      | dataclass  | Structured error: type, message, device, timestamp   |
| `ErrorPublisher`    | service    | Fire-and-forget error publication to MQTT            |
| `build_error_payload`| function  | Exception → `ErrorPayload` using error type map      |

### Health

| Export              | Type       | Description                                          |
| ------------------- | ---------- | ---------------------------------------------------- |
| `DeviceStatus`      | dataclass  | Per-device status snapshot                           |
| `HeartbeatPayload`  | dataclass  | App heartbeat: status, uptime, version, devices      |
| `HealthReporter`    | service    | Heartbeats + per-device availability + LWT           |
| `build_will_config` | function   | Create LWT `WillConfig` for `{prefix}/status`        |
| `HealthCheckable`   | Protocol   | `async def health_check() -> bool` — adapter health check contract |
| `AdapterHealthStatus`| dataclass | Per-adapter health state: healthy, failures, restarts |

### Settings

| Export            | Type          | Description                                        |
| ----------------- | ------------- | -------------------------------------------------- |
| `Settings`        | BaseSettings  | Root settings (mqtt + logging sub-models)          |
| `MqttSettings`    | BaseModel     | host, port, username, password (`SecretStr`), TLS, client_id, prefix |
| `LoggingSettings` | BaseModel     | level, format, file, rotation                      |

> **Credentials:** `MqttSettings.password` uses `SecretStr` — never log or
> serialize it directly. Use `.get_secret_value()` only where needed.

### Publish Strategies

| Export            | Type       | Description                                        |
| ----------------- | ---------- | -------------------------------------------------- |
| `PublishStrategy` | Protocol   | `should_publish()` + `on_published()` contract     |
| `Every`           | class      | Throttle: `Every(seconds=30)` or `Every(n=5)`      |
| `OnChange`        | class      | Dead-band: `OnChange()`, `OnChange(threshold=0.1)` |

| `AllStrategy`   | class      | AND-composite: publishes only if **all** children agree |
| `AnyStrategy`   | class      | OR-composite: publishes if **any** child says yes       |

Strategies compose: `OnChange() | Every(seconds=60)` (any), `OnChange() & Every(n=3)`
(all). Without a strategy, every poll cycle publishes.

### Signal Filters

| Export            | Type       | Description                                        |
| ----------------- | ---------- | -------------------------------------------------- |
| `Filter`          | Protocol   | `update(raw) -> float`, `.value`, `.reset()`       |
| `Pt1Filter`       | class      | First-order low-pass. `Pt1Filter(tau, dt)`         |
| `MedianFilter`    | class      | Sliding-window median. `MedianFilter(window)`      |
| `OneEuroFilter`   | class      | Adaptive 1€ filter. `OneEuroFilter(min_cutoff=…)`  |

Filters follow the `update → value` pattern. First `update()` seeds the filter and
returns input unchanged.

### Retry / Backoff

| Export              | Type       | Description                                        |
| ------------------- | ---------- | -------------------------------------------------- |
| `BackoffStrategy`   | Protocol   | `delay(attempt: int) -> float` — retry delay contract |
| `ExponentialBackoff` | class     | `min(base * 2^(attempt-1), max_delay)` with ±20% jitter |
| `LinearBackoff`     | class      | `min(step * attempt, max_delay)` with ±20% jitter  |
| `FixedBackoff`      | class      | Constant delay with ±20% jitter                    |
| `CircuitBreaker`    | class      | Consecutive-failure threshold → open/half-open/closed |

### Introspection

| Export                   | Type     | Description                                        |
| ------------------------ | -------- | -------------------------------------------------- |
| `build_registry_snapshot`| function | JSON-serializable snapshot of all app registrations |
| `format_registry_json`   | function | Pretty-print snapshot as JSON (orjson)              |
| `format_registry_table`  | function | Human-readable plain-text table of registrations    |

### `cosalette manifest` CLI

Prints the resolved registration surface of an app. Imports the module at
CLI time — module-level code runs (same behaviour as `cosalette_inspect_app`).

```bash
cosalette manifest myapp.main:app           # JSON output
cosalette manifest myapp.main:app --table   # human-readable table
```

**JSON output fields per device entry:**

| Field           | Description                                               |
| --------------- | --------------------------------------------------------- |
| `name`          | Device name (`null` for root devices)                     |
| `type`          | `"telemetry"`, `"command"`, or `"device"`                 |
| `interval`      | Seconds, field name (when `setting_ref()` used), or `"<deferred>"` |
| `triggerable`   | `true` when `triggerable=True`                            |
| `summary`       | Human-readable description (if declared)                  |
| `state_model`   | Class name of the state model type (if declared)          |
| `payload_model` | Class name of the accepted payload type (if declared)     |
| `behavior`      | List of operational step strings (if declared)            |
| `effects`       | List of side-effect strings (if declared)                 |

**MCP equivalent:** `cosalette_manifest("myapp.main:app")`

For authoring contract metadata (`summary`, `state_model`, `payload_model`,
`behavior`, `effects`) see the
[Contract-First Route Design](../guides/contract-first-route-design.md) guide.

### Persistence

| Export            | Type       | Description                                        |
| ----------------- | ---------- | -------------------------------------------------- |
| `Store`           | Protocol   | `load(key)` / `save(key, data)` contract           |
| `DeviceStore`     | class      | Per-device scoped `MutableMapping` with dirty tracking |
| `JsonFileStore`   | class      | Atomic JSON file backend                           |
| `SqliteStore`     | class      | SQLite WAL-mode backend                            |
| `MemoryStore`     | class      | In-memory (tests). Deep-copy isolation             |
| `NullStore`       | class      | No-op backend                                      |
| `PersistPolicy`   | Protocol   | `should_save(store, published)` contract           |
| `SaveOnPublish`   | class      | Save after each successful publish                 |
| `SaveOnChange`    | class      | Save when store is dirty                           |
| `SaveOnShutdown`  | class      | Save only on shutdown                              |

| `AllSavePolicy` | class      | AND-composite: save only if **all** children agree     |
| `AnySavePolicy` | class      | OR-composite: save if **any** child says yes           |

Policies compose: `SaveOnChange() | SaveOnPublish()` (any). Framework always saves on
shutdown regardless of policy (safety net).

### Logging

| Export             | Type     | Description                                       |
| ------------------ | -------- | ------------------------------------------------- |
| `JsonFormatter`    | class    | Structured JSON log formatter                     |
| `configure_logging`| function | Set up logging from `LoggingSettings`             |

---

## App Constructor

```python
App(
    name: str,                                  # Topic prefix + client ID + log name
    version: str = "0.0.0",                     # --version flag + heartbeats
    *,
    description: str = "IoT-to-MQTT bridge",    # CLI help text
    settings_class: type[Settings] = Settings,  # Custom settings subclass
    dry_run: bool = False,                      # Resolve dry-run adapters
    heartbeat_interval: float | None = 60.0,    # Seconds (None to disable)
    lifespan: LifespanFunc | None = None,       # Startup/shutdown hook
    store: Store | Callable[..., Store] | None = None,  # Persistence backend or factory
    adapters: dict[type, ...] | None = None,    # Port→impl mapping
    health_check_interval: float | None = 30.0, # Seconds between adapter health checks (None to disable)
    restart_after_failures: int = 5,             # Consecutive failures before adapter restart (0 to disable)
    max_restarts: int = 3,                       # Lifetime restart limit per adapter
    restart_cooldown: float = 5.0,               # Seconds between __aexit__ and __aenter__ during restart
    sustained_health_reset: float = 300.0,       # Seconds of sustained health to reset restart counter
)
```

### `adapters=` dict (since 0.1.5)

Inline adapter registration, alternative to calling `app.adapter()` imperatively:

```python
app = App(
    name="myapp",
    adapters={
        GasMeterPort: SerialGasMeter,                       # impl only
        DisplayPort: (OledDisplay, FakeDisplay),            # (impl, dry_run)
        SensorPort: "myapp.drivers:I2cSensor",              # lazy string
        ControlPort: create_controller,                     # factory callable
    },
)
```

Each value is `impl` or `(impl, dry_run)` tuple.

### `store=` persistence (since 0.1.5)

Pass a `Store` backend or a callable factory `Callable[..., Store]`. When a factory
is passed, it is called during bootstrap with DI-resolved settings and adapters.
The framework creates a scoped `DeviceStore` per device, injectable via the DI system.

```python
# Concrete store — resolved immediately
app = App(name="myapp", store=JsonFileStore("./data/state.json"))

# Factory — resolved at bootstrap with injected settings
def make_store(settings: MySettings) -> Store:
    return JsonFileStore(settings.data_dir / "state.json")

app = App(name="myapp", settings_class=MySettings, store=make_store)
```

### `app.settings` property

Eagerly constructed in `__init__` (since 0.1.4). Raises `RuntimeError` if settings
construction failed (e.g. missing required env vars when not running `--help`).

---

## Device Decorators

### `@app.telemetry(name, *, interval, ...)`

Periodic polling device. Framework loops, calls function, publishes returned dict.

```python
@app.telemetry("sensor", interval=5.0)
async def sensor() -> dict[str, object]:
    return {"temperature": 22.5}
```

Full signature:

```python
@app.telemetry(
    name: str | None = None,     # Device name (None = root device)
    *,
    interval: IntervalSpec | None = None,     # Seconds > 0, or callable (optional when schedule= provided)
    schedule: str | CronSchedule | CronSpec | None = None,  # Quartz cron expression, CronSchedule, or per-device callable
    publish: PublishStrategy | None = None,   # OnChange(), Every(seconds=30), etc.
    persist: PersistPolicy | None = None,     # SaveOnChange(), SaveOnPublish(), etc.
    init: Callable[..., Any] | None = None,   # Per-device state factory
    enabled: bool = True,        # False to skip registration entirely
    group: str | None = None,    # Coalescing group name
    retry: int = 0,              # Number of retry attempts per cycle
    retry_on: tuple[type[BaseException], ...] | None = None,  # Exception types to retry (default: (OSError,))
    backoff: BackoffStrategy | None = None,   # Retry delay strategy (default: ExponentialBackoff())
    circuit_breaker: CircuitBreaker | None = None,  # Circuit breaker instance
)
```

- **`name`**: device name. `None` → root device (publishes to `{prefix}/state`)
- **`interval`**: seconds between polls (> 0 for float). Can be a callable
  `lambda s: s.my_interval` for deferred resolution from settings (see ADR-020).
  Callable intervals are resolved once after settings are available in `_run_async()`.
  Validation deferred until resolution. Optional when `schedule=` is provided.
- **`schedule`**: Quartz cron expression (6-7 fields), a pre-parsed `CronSchedule`
  instance, or a `CronSpec` callable `(per_device_config) -> str | CronSchedule`.
  The callable form is only valid when `name=` is also a callable (dict-name
  multi-device registration) — static names have no per-device config to pass in.
  `interval=` and `schedule=` are **mutually exclusive** — providing both raises
  `ValueError`. At least one of `interval=` or `schedule=` is required. `schedule=`
  cannot combine with `group=` (including when using the callable form). First
  execution runs immediately, then waits for the next scheduled time.
- **`retry`**: number of retry attempts per cycle. `retry > 0` with `retry_on=()`
  raises `ValueError`. Defaults when `retry > 0`: `retry_on=(OSError,)`,
  `backoff=ExponentialBackoff()`.
- **`backoff`**: retry delay strategy. Only meaningful when `retry > 0`.
- **`circuit_breaker`**: circuit breaker instance. Opens after consecutive failures.
- **`publish`**: publish strategy. `None` → publish every cycle.
  `OnChange()` → only on value change. `Every(seconds=30)` → time-throttle.
- **`persist`**: save policy. Requires `store=` on App. Auto-saves on shutdown.
- **`init`**: callable invoked once at device startup. Return value injected as the
  init type into handler parameters. DI-enabled (receives Settings, adapters, etc.).
- **`enabled`**: `False` silently skips registration. Useful for conditional features.
- **`group`**: coalescing group (since 0.1.6). Devices in the same group share a single
  scheduler loop and publish atomically. All grouped devices share the same interval.
- **Returns**: `dict[str, object]` → auto-published as JSON. `None` → suppress publish
  for this cycle.
- **Error isolation**: exceptions logged + published to error topic, loop continues
- **Error deduplication**: consecutive identical errors logged once; recovery logged

### `@app.command(name, ...)`

Declarative command handler. Dispatched on inbound MQTT to `{prefix}/{name}/set`.

```python
@app.command("valve")
async def handle_valve(payload: str, ctx: DeviceContext) -> dict[str, object]:
    return {"state": payload}
```

Full signature:

```python
@app.command(
    name: str | None = None,     # Device name (None = root device)
    *,
    init: Callable[..., Any] | None = None,   # Per-device state factory
    enabled: bool = True,        # False to skip registration
)
```

- **`name`**: device name. `None` → root device
- **MQTT params**: `topic` and `payload` injected **by name** (declare only what you
  need)
- **Returns**: `dict[str, object]` → published as state. `None` → no publication.
- **`init`**: same as telemetry — injectable per-device state factory
- **`enabled`**: conditional registration

### `@app.device(name, ...)`

Full-lifecycle coroutine. Runs as a concurrent asyncio task with full control.

```python
@app.device("blind")
async def blind(ctx: DeviceContext) -> None:
    gpio = ctx.adapter(GpioPort)

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        await execute(payload, gpio)
        await ctx.publish_state({"position": get_position()})

    await ctx.publish_state({"position": None})
    while not ctx.shutdown_requested:
        await ctx.sleep(10)
```

Full signature:

```python
@app.device(
    name: str | None = None,     # Device name (None = root device)
    *,
    init: Callable[..., Any] | None = None,   # Per-device state factory
    enabled: bool = True,        # False to skip registration
)
```

- Must manage its own loop with `ctx.shutdown_requested` + `ctx.sleep()`
- Register command handler via `@ctx.on_command` inside the function
- Has access to all DI types including `DeviceStore` for persistence

### Scoped Name Uniqueness (since 0.1.7)

A device name can be reused across different scopes. For example, a telemetry device
named `"outdoor"` and a command device named `"outdoor"` can coexist — they share a
common MQTT topic prefix. This enables the pattern of pairing a telemetry poller with
a command handler for the same logical device.

### `init=` Callback (since 0.1.4)

Per-device state injection. The callback is invoked once at device startup. Its return
value is available to the handler via type-based DI:

```python
class SensorState:
    last_reading: float | None = None

def create_state(settings: MySettings) -> SensorState:
    return SensorState()

@app.telemetry("sensor", interval=5.0, init=create_state)
async def sensor(state: SensorState) -> dict[str, object]:
    reading = await read_sensor()
    state.last_reading = reading
    return {"temperature": reading}
```

The `init` callable itself supports DI — it can declare parameters for `Settings`,
adapters, `ClockPort`, etc.

---

## Imperative Registration (since 0.1.5)

For dynamic or loop-based registration, use the imperative methods:

```python
for group in config.groups:
    app.add_telemetry(
        name=group.name,
        func=make_handler(group),
        interval=lambda s: getattr(s, f"{group.name}_interval"),
        publish=OnChange(),
    )
```

| Method | Corresponding Decorator |
| ------ | ----------------------- |
| `app.add_telemetry(name, func, *, interval, ...)` | `@app.telemetry()` |
| `app.add_command(name, func, *, init, enabled)` | `@app.command()` |
| `app.add_device(name, func, *, init, enabled)` | `@app.device()` |

All imperative methods require an explicit `name` (no `None` / root device support).
They accept the same keyword arguments as their decorator counterparts.

---

## Configuration Hook (since 0.2.0)

### `@app.on_configure`

Runs after settings and adapters are resolved, before devices start:

```python
@app.on_configure
def register_devices(settings: MySettings) -> None:
    for cal in settings.calendars:
        app.add_telemetry(cal.key, make_handler(cal), interval=cal.interval)
```

- Use plain decorator syntax (no parentheses)
- Parameters are injected by type annotation (`Settings`, adapters, `Logger`, `ClockPort`)
- Lifespan-yielded state is NOT available in `on_configure` hooks
- Use for settings-dependent dynamic device registration

---

## Adapter Registration

### `app.adapter(port_type, impl, *, dry_run=None)`

```python
app.adapter(GasMeterPort, SerialGasMeter, dry_run=FakeGasMeter)
```

- `impl`: class, `"module:ClassName"` lazy string, or factory callable
- `dry_run`: optional alternative for `--dry-run` mode
- One adapter per port type
- All forms support DI — factory/class `__init__` with `Settings` parameter gets
  auto-injected

**Factory settings injection (since 0.1.1):**

```python
def create_meter(settings: Gas2MqttSettings) -> SerialGasMeter:
    meter = SerialGasMeter()
    meter.connect(settings.serial_port, baud_rate=settings.baud_rate)
    return meter

app.adapter(GasMeterPort, create_meter)
```

**Adapter lifecycle (since 0.1.5):** Adapters implementing
`async def __aenter__` / `async def __aexit__` are automatically entered/exited by the
framework. Entry happens after settings resolution, before device tasks start. The
framework catches `CancelledError` during entry for clean shutdown.

---

## DeviceContext API

| Method/Property                                      | Description                                    |
| ---------------------------------------------------- | ---------------------------------------------- |
| `.name`                                              | Device name                                    |
| `.settings`                                          | Settings instance                              |
| `.clock`                                             | ClockPort                                      |
| `.shutdown_requested`                                | `bool` — True when shutting down               |
| `await .publish_state(payload, *, retain=True)`      | Publish to `{prefix}/{device}/state`           |
| `await .publish(channel, payload, *, retain, qos)`   | Publish to `{prefix}/{device}/{channel}`       |
| `await .sleep(seconds)`                              | Shutdown-aware sleep                           |
| `await .sleep_until(target, *, tz)`                  | Shutdown-aware sleep until wall-clock time     |
| `.on_command(handler)` or `@ctx.on_command("subtopic")` | Register command handler (with optional sub-topic routing) |
| `.commands(*, timeout=None)`                         | Async iterator yielding `Command` or `None` on timeout |
| `async with .sub_entity(name)`                       | Scoped sub-entity context manager              |
| `.command_handlers`                                  | Read-only mapping of registered command handlers |
| `.adapter(port_type) -> T`                           | Resolve registered adapter                     |

`DeviceStore` is injected via DI (type annotation), not as a `DeviceContext` property.

---

## SubEntityContext API

Created via `async with ctx.sub_entity("name")`. Auto-publishes "online"/"offline"
availability on enter/exit.

| Method/Property                                      | Description                                    |
| ---------------------------------------------------- | ---------------------------------------------- |
| `await .publish_state(payload, *, retain=True)`      | Publish to `{prefix}/{device}/{name}/state`    |
| `.on_command(handler)`                               | Register command handler for this sub-entity's sub-topic |
| `.name`                                              | Sub-entity name                                |
| `.parent`                                            | Parent `DeviceContext`                         |

---

## AppContext API

| Method/Property              | Description                                    |
| ---------------------------- | ---------------------------------------------- |
| `.settings`                  | Settings instance                              |
| `.adapter(port_type) -> T`   | Resolve registered adapter                     |

Subset of `DeviceContext` — no publish, no `on_command`, no sleep.
Available in `lifespan` hooks only.

---

## Dependency Injection

Parameters resolved by **type annotation** (not name), except `topic`/`payload` in
`@app.command`.

| Type Annotation      | Injected Value                               |
| -------------------- | -------------------------------------------- |
| `DeviceContext`      | Per-device context                           |
| `Settings` (or sub)  | App settings (matches via `issubclass`)      |
| `logging.Logger`     | `logging.getLogger("cosalette.{device}")`    |
| `ClockPort`          | Clock instance                               |
| `asyncio.Event`      | Shutdown event                               |
| `DeviceStore`        | Scoped device store (requires `store=` on App) |
| Any adapter port     | Registered adapter instance                  |
| `init=` return type  | Value returned by the `init` callback        |
| Lifespan yielded type | Value yielded by `lifespan=` context manager (ADR-027) |

Zero-parameter functions are valid. Missing annotations fail at registration time.

Lifespan-yielded type: only concrete runtime type is matched. Not available in
`on_configure` hooks. Single value per App.

---

## Configuration Pattern

Subclass `Settings` and add your fields:

```python
from pydantic import Field
from pydantic_settings import SettingsConfigDict
import cosalette

class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    serial_port: str = Field(default="/dev/ttyUSB0")
    poll_interval: int = Field(default=60, ge=1)
```

Pass to App: `App(name="gas2mqtt", settings_class=Gas2MqttSettings)`

> **Important:** Add `.env` to `.gitignore` to prevent accidental credential
> exposure in version control.

**Priority:** CLI flags > env vars > .env file > defaults.

### Deferred Interval Resolution (since 0.1.8)

When telemetry intervals depend on settings values, use a callable to defer resolution:

```python
app.add_telemetry(
    name="outdoor",
    func=outdoor_handler,
    interval=lambda s: s.outdoor_interval,  # resolved after settings are ready
)
```

This avoids accessing `app.settings` at module level — which would crash `--help` /
`--version` when required env vars are absent (see ADR-020).

---

## Telemetry Coalescing Groups (since 0.1.6)

Group telemetry devices to share a single scheduler loop:

```python
@app.telemetry("temp", interval=60.0, group="environment")
async def temperature() -> dict[str, object]:
    return {"value": read_temp()}

@app.telemetry("humidity", interval=60.0, group="environment")
async def humidity() -> dict[str, object]:
    return {"value": read_humidity()}
```

All devices in a group execute concurrently in the same scheduler iteration. They must
share the same interval value (or the same callable). Groups publish atomically — all
or nothing per cycle. See ADR-018.

---

## Persistence Pattern (since 0.1.5)

```python
app = App(
    name="myapp",
    store=JsonFileStore("state.json"),  # or SqliteStore, MemoryStore
)

@app.telemetry("counter", interval=10.0, persist=SaveOnChange())
async def counter(store: DeviceStore) -> dict[str, object]:
    count = store.get("count", 0)
    count += 1
    store["count"] = count
    return {"count": count}
```

- `DeviceStore` is a `MutableMapping` scoped to the device name
- Dirty tracking — only saves when data changed
- `store.mark_dirty()` for nested mutations (e.g. modifying a nested dict)
- Framework always saves on shutdown (safety net)

---

## Testing

### Exports from `cosalette.testing`

| Export           | Description                                            |
| ---------------- | ------------------------------------------------------ |
| `AppHarness`     | Full integration harness (App + MockMqtt + FakeClock)  |
| `MockMqttClient` | Records publishes, simulates inbound messages          |
| `NullMqttClient` | Silent no-op adapter                                   |
| `FakeClock`      | Deterministic clock (set `._time` manually)            |
| `make_settings`  | Isolated Settings (no env leakage)                     |

### AppHarness

```python
harness = AppHarness.create(
    name="testapp",
    version="1.0.0",
    store=MemoryStore(),         # optional
    **settings_overrides,
)
await harness.run()              # starts app in background
harness.trigger_shutdown()       # signals shutdown
```

### Pytest Plugin Fixtures

Register via `pytest_plugins = ["cosalette.testing._plugin"]`

| Fixture          | Type             | Description                              |
| ---------------- | ---------------- | ---------------------------------------- |
| `mock_mqtt`      | `MockMqttClient` | Fresh per-test                           |
| `fake_clock`     | `FakeClock`      | Starts at time 0                         |
| `device_context` | `DeviceContext`  | Wired with mock_mqtt, fake_clock, name="test_device" |

### Test Pattern

```python
import pytest
from cosalette.testing import MockMqttClient, FakeClock

@pytest.mark.unit
async def test_sensor_publishes(mock_mqtt: MockMqttClient, fake_clock: FakeClock):
    # Arrange
    fake_clock._time = 100.0
    # Act — call your device function with injected test doubles
    result = await sensor()
    # Assert
    assert result == {"temperature": 22.5}
```

---

## MQTT Topic Convention

```text
{prefix}/{device}/state          — retained, QoS 1 (device state JSON)
{prefix}/{device}/set            — inbound (command input)
{prefix}/{device}/availability   — retained, QoS 1 ("online"/"offline")
{prefix}/{device}/error          — not retained, QoS 1 (error JSON)
{prefix}/{device}/{sub_entity}/state         — retained, QoS 1 (sub-entity state)
{prefix}/{device}/{sub_entity}/set           — inbound (sub-entity commands)
{prefix}/{device}/{sub_entity}/availability  — retained, QoS 1 ("online"/"offline")
{prefix}/error                   — not retained, QoS 1 (global errors)
{prefix}/status                  — retained, QoS 1 (heartbeat + LWT)
```

Root devices omit the `/{device}/` segment. `{prefix}` defaults to `App(name=...)`.

---

## Application Lifecycle

```text
Bootstrap → Settings → Logging → Adapters (construct + DI) → on_configure hooks
            → Expand name specs → Resolve intervals
Lifecycle → Adapter __aenter__ → Signal handlers → Health check startup
Wire      → Device contexts → Command router → Subscribe /set topics
Run       → Lifespan startup (yield DI state) → Heartbeat → Device tasks
            → Health check loop → Block on shutdown
Teardown  → Cancel tasks → Cancel heartbeat → Lifespan teardown → Offline
            → Adapter __aexit__ → Disconnect
```

SIGTERM/SIGINT → sets shutdown event → `ctx.sleep()` returns early →
`ctx.shutdown_requested` becomes True. Signal handlers are installed before adapter
lifecycle entry (since 0.1.5).

---

## Migration Patterns

| Legacy Pattern                                     | cosalette Equivalent                              |
| -------------------------------------------------- | ------------------------------------------------- |
| `while True: read(); publish(); sleep(N)`          | `@app.telemetry("sensor", interval=N)`            |
| `mqtt.on_message(callback)` + manual dispatch      | `@app.command("device")`                          |
| Global MQTT client variable                        | `DeviceContext` injection — no globals            |
| `try/except` around publish                        | Automatic error isolation + error topics          |
| Manual `signal.signal(SIGTERM, handler)`            | Built-in: `ctx.shutdown_requested` + `ctx.sleep()`|
| Manual LWT setup                                   | Automatic via `HealthReporter`                    |
| Polling loop with `asyncio.sleep`                  | `@app.telemetry` or `ctx.sleep()` in `@app.device`|
| Request/response via MQTT                          | `@app.command("name")`                            |
| Complex stateful device                            | `@app.device("name")` with manual loop            |
| Separate config / argparse                         | `Settings` subclass + `.env` + CLI flags          |
| Hardware globals (`bus = smbus2.SMBus(1)`)          | `app.adapter(Port, Impl)` + `ctx.adapter(Port)`  |
| Init/cleanup in main()                             | `lifespan` async context manager                  |
| Interval from config at import time                | `interval=lambda s: s.my_interval` (ADR-020)      |
| Per-device mutable state via closure               | `init=` callback + type injection                 |
| Manual JSON file read/write for state              | `store=JsonFileStore()` + `DeviceStore` DI        |
| Conditional feature via if-else around handlers    | `enabled=settings.feature_flag`                   |
| Loop registering multiple similar devices          | `app.add_telemetry()` in a for-loop               |
| Multiple sensors polling in lockstep               | `group="name"` coalescing                         |
| Noisy sensor smoothing                             | `Pt1Filter` / `MedianFilter` / `OneEuroFilter`   |
| Publish only on change (dead-band)                 | `publish=OnChange(threshold=0.1)`                 |
| Rate-limit publishes                               | `publish=Every(seconds=30)`                       |
| Adapter needing cleanup                            | `__aenter__`/`__aexit__` protocol                 |
| Fixed-interval poll for day-aligned data           | `schedule="0 0 6,18 * * ?"` on `@app.telemetry`   |
| Device with day-aligned polling in loop            | `ctx.sleep_until()` in `@app.device`              |
| Manual retry + backoff around hardware reads       | `retry=3, backoff=ExponentialBackoff()`            |
| Manual adapter health check + restart logic        | `HealthCheckable` protocol + auto-restart          |
| Separate state injection via closures/globals      | Lifespan yield → DI injection (ADR-027)           |
| Multiple devices sharing resources with cleanup    | `ctx.sub_entity()` context manager                |
| Settings-dependent device registration             | `@app.on_configure` hook                          |

---

## Device Error Handling — Do / Don't

The framework provides **automatic error isolation** for all device types:

- **`@app.telemetry`**: exceptions are logged, published to the error topic, and the
  polling loop continues automatically.
- **`@app.command`**: command dispatch errors are handled by the framework.
- **`@app.device`**: task-level errors are caught, logged, and published to the error
  topic. **The coroutine is not restarted** — if your device loop must survive transient
  errors, catch expected exceptions locally (log and continue).

**Consecutive identical errors are deduplicated** — logged once until recovery.

### Don't: Catch and swallow domain errors

```python
# ❌ BAD — duplicates framework behaviour and hides errors from the error topic
@app.telemetry("sensor", interval=5.0)
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    try:
        reading = await read_sensor()
        return {"temperature": reading}
    except OSError:
        logger.exception("Read failed")  # swallowed — never reaches error topic
```

```python
# ❌ BAD — broad except in callback prevents framework error handling
def register_callback(self, callback):
    def _wrapper(raw_data):
        try:
            reading = parse(raw_data)
            callback(reading)
        except Exception:
            logger.exception("Error")  # swallowed
    self._driver.register(_wrapper)
```

### Do: Let errors propagate to the framework

```python
# ✅ GOOD — framework logs, publishes to error topic, continues loop
@app.telemetry("sensor", interval=5.0)
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    reading = await read_sensor()
    return {"temperature": reading}
```

```python
# ✅ GOOD — skip unparsable input, propagate real errors
def register_callback(self, callback):
    def _wrapper(raw_data):
        match = FRAME_RE.search(raw_data)
        if match is None:
            logger.warning("Unparsable frame: %r", raw_data)
            return  # skip — not an error
        reading = parse(match)
        callback(reading)
    self._driver.register(_wrapper)
```

### When local error handling IS appropriate

- **Skipping bad input** (e.g., unparsable frames): log a warning and `return` — this
  is filtering, not error handling.
- **Cleanup / resource release**: use `try/finally`, not `try/except`.
- **Retry with backoff**: only when the framework's default "log + continue" is
  insufficient and the retry logic adds value beyond what the next poll cycle provides.
- **Thread boundaries**: callbacks invoked from foreign threads (e.g., serial reader
  threads in hardware libraries) are outside the framework's asyncio error boundary.
  Use local error handling to keep the thread alive and marshal errors into the event
  loop if framework-level reporting is needed.

---

## Known Constraints (0.3.0)

- **Python 3.14+** required (PEP 695 `type` statement syntax)
- **QoS 1 hard-coded** for framework publishes (use `ctx.publish()` for QoS 0)
- **One adapter per port type** — no multi-instance registry
- **At most one root (unnamed) device** per archetype (one root telemetry, one root
  command, one root device)
- **Error type map uses exact class match** — no subclass matching
- **Generic types rejected** for injection — must be concrete
- **`@app.telemetry` is periodic-return-dict only** — conditional/event-driven →
  use `@app.device`
- **`@app.command` has no background work** — need periodic + commands → use
  `@app.device`
- **Lifespan `AppContext` has no publish/sleep** — runtime MQTT via devices only
- **Callable intervals** validated at resolution time (deferred), not registration time
- **Coalescing groups** require all members to share the same interval value
- **`DeviceStore` requires `store=` on App** — `None` store + `DeviceStore` DI
  raises at registration
- **`schedule=` and `interval=` are mutually exclusive** — cannot combine on same telemetry
- **`schedule=` cannot combine with `group=`** — coalescing groups require `interval=`
- **`on_command()` and `commands()` are mutually exclusive** on the same device
- **Lifespan-yielded state** not available in `on_configure` hooks; only concrete runtime type
- **Adapter auto-restart** loses in-flight `@app.device` state during restart; brief ~5s gap
- **`ctx.sleep_until()` uses wall clock** — DST transitions may shift ±1 hour
- **Local timezone default** for `sleep_until()` and `schedule=` — behaviour varies by container TZ
- **Sub-entity availability** not tracked by `HealthReporter`
- **Reserved sub-entity names**: state, set, availability, status, error, config, attributes, json_attributes, diagnostic, firmware
- **Signal filters** require Rust extension (abi3 wheel) — no Python fallback since 0.2.0
- **orjson** is a hard dependency since 0.2.0
