---
icon: material/devices
---

# Multi-Device Registration

Many IoT bridges manage **multiple similar devices** — a fleet of BLE sensors, a rack
of Modbus meters, or a set of GPIO-controlled relays. Hardcoding one decorator per
device leads to copy-paste boilerplate that doesn't scale.

cosalette provides two complementary features for this:

- **`@app.on_configure`** — a lifecycle hook for settings-driven setup
- **Dict-name decorators** — `name=callable` on `@app.telemetry`, `@app.device`, and
  `@app.command` for declarative multi-device registration

!!! note "Prerequisites"

    This guide assumes familiarity with
    [Telemetry Devices](telemetry-device.md) and
    [Configuration](configuration.md).

## The Problem

Consider a BLE sensor bridge with three sensors. The naïve approach duplicates the
handler for each device:

```python title="app.py — copy-paste approach (don't do this)"
@app.telemetry("sensor_a", interval=10)
async def sensor_a(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return await read_ble("AA:BB:CC:DD:EE:01")

@app.telemetry("sensor_b", interval=10)
async def sensor_b(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return await read_ble("AA:BB:CC:DD:EE:02")

@app.telemetry("sensor_c", interval=10)
async def sensor_c(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return await read_ble("AA:BB:CC:DD:EE:03")
```

Adding a fourth sensor means editing code. Changing the interval means changing it in
three places. The device list is baked into the source — not configurable at deploy
time.

## Dict-Name Decorators

The `name=` parameter on `@app.telemetry`, `@app.device`, and `@app.command` accepts
a **callable** that receives `Settings` and returns either:

- a **`dict[str, config]`** — one device per key, with per-device config injected via DI
- a **`list[str]`** — one device per name, no per-device config

### Basic Example: Dict Name with Config

```python title="app.py"
from dataclasses import dataclass

import cosalette


@dataclass
class SensorConfig:
    mac: str
    location: str = ""


app = cosalette.App(name="ble2mqtt", version="1.0.0")


@app.telemetry(
    name=lambda s: {                                 # (1)!
        "living_room": SensorConfig(mac="AA:BB:CC:DD:EE:01", location="Living Room"),
        "bedroom":     SensorConfig(mac="AA:BB:CC:DD:EE:02", location="Bedroom"),
        "kitchen":     SensorConfig(mac="AA:BB:CC:DD:EE:03", location="Kitchen"),
    },
    interval=10,
)
async def sensor(                                    # (2)!
    ctx: cosalette.DeviceContext,
    config: SensorConfig,                            # (3)!
) -> dict[str, object]:
    reading = await read_ble(config.mac)
    return {"temperature": reading, "location": config.location}


app.run()
```

1. The callable receives `Settings` and returns a dict. Each key becomes a device name;
   values are per-device config objects.
2. **One handler, three devices.** The framework creates a separate registration for
   each dict entry — `living_room`, `bedroom`, `kitchen` — all pointing at the same
   handler function.
3. The per-device config is **injected by type**. The framework matches
   `SensorConfig` in the handler signature to the dict value for _that_ device.
   `living_room` gets `SensorConfig(mac="AA:...:01")`, `bedroom` gets
   `SensorConfig(mac="AA:...:02")`, etc.

**MQTT topic layout:**

| Device         | Topic                            | Interval |
|----------------|----------------------------------|----------|
| `living_room`  | `ble2mqtt/living_room/state`     | 10 s     |
| `bedroom`      | `ble2mqtt/bedroom/state`         | 10 s     |
| `kitchen`      | `ble2mqtt/kitchen/state`         | 10 s     |

### List Name (No Config)

When you just need multiple device names without per-device configuration, return a
list:

```python title="app.py"
@app.telemetry(
    name=lambda s: ["sensor_a", "sensor_b", "sensor_c"],
    interval=10,
)
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await read_sensor(ctx.name)}  # (1)!
```

1. Use `ctx.name` to distinguish which device is running — it will be `"sensor_a"`,
   `"sensor_b"`, or `"sensor_c"` depending on the invocation.

### Per-Device Intervals

When combined with a dict name, the `interval=` parameter can also be a **callable**
that receives the per-device config and returns a float. This gives each device its
own polling frequency:

```python title="app.py"
@dataclass
class SensorConfig:
    mac: str
    poll_seconds: float = 10.0


@app.telemetry(
    name=lambda s: {
        "fast_sensor": SensorConfig(mac="AA:...:01", poll_seconds=2.0),
        "slow_sensor": SensorConfig(mac="AA:...:02", poll_seconds=60.0),
    },
    interval=lambda cfg: cfg.poll_seconds,           # (1)!
)
async def sensor(
    ctx: cosalette.DeviceContext, config: SensorConfig,
) -> dict[str, object]:
    return {"value": await read_ble(config.mac)}
```

1. The `interval=` callable receives the per-device config object (not `Settings`).
   `fast_sensor` runs every 2 seconds; `slow_sensor` runs every 60 seconds.

!!! warning "Per-device intervals and coalescing groups"

    Per-device intervals (callable `interval=`) **cannot** be combined with
    `group=`. Coalescing groups require all members to share the same interval.
    The framework raises `ValueError` if you try to combine both.

### Works with All Decorators

Dict-name and list-name work identically on all three device decorators:

=== "@app.telemetry"

    ```python
    @app.telemetry(
        name=lambda s: {"a": MyConfig(value=1), "b": MyConfig(value=2)},
        interval=10,
    )
    async def handler(ctx: cosalette.DeviceContext, config: MyConfig) -> dict[str, object]:
        return {"value": config.value}
    ```

=== "@app.device"

    ```python
    @app.device(
        name=lambda s: {"motor1": MotorConfig(pin=1), "motor2": MotorConfig(pin=2)},
    )
    async def motor(ctx: cosalette.DeviceContext, config: MotorConfig) -> None:
        gpio = ctx.adapter(GpioPort)
        while not ctx.shutdown_requested:
            gpio.write(config.pin, await read_command())
            await ctx.sleep(1)
    ```

=== "@app.command"

    ```python
    @app.command(
        name=lambda s: {"relay1": RelayConfig(pin=5), "relay2": RelayConfig(pin=6)},
    )
    async def relay(
        payload: str, ctx: cosalette.DeviceContext, config: RelayConfig
    ) -> dict[str, object]:
        gpio = ctx.adapter(GpioPort)
        gpio.write(config.pin, payload == "on")
        return {"state": payload}
    ```

## `@app.on_configure` — Settings-Driven Setup

Dict-name callables receive `Settings`, but they're limited to returning a
dict or list. When you need more complex setup logic — reading device lists from
configuration files, conditional registration, computing derived values — use
`@app.on_configure`.

### How It Works

`@app.on_configure` registers a **lifecycle hook** that runs:

- **After** adapters are constructed (but before lifecycle entry)
- **Before** devices are wired and started

```text
Settings loaded
    ↓
Adapters resolved
    ↓
→ on_configure hooks execute (in registration order) ←
    ↓
Name specs expanded (dict/list → concrete registrations)
    ↓
Intervals resolved
    ↓
MQTT connected
    ↓
Lifecycle adapters entered
    ↓
Devices wired & started
```

The hook receives dependencies via the same injection system as device handlers.
Available injectables: `Settings` (or your subclass), adapter port types,
`logging.Logger`, `ClockPort`.

### Basic Example

```python title="app.py"
import cosalette
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class SensorSettings(cosalette.Settings):
    model_config = SettingsConfigDict(env_prefix="BLE_")

    sensor_macs: dict[str, str] = Field(default_factory=dict)  # (1)!


app = cosalette.App(
    name="ble2mqtt",
    version="1.0.0",
    settings_class=SensorSettings,
)


@app.on_configure                                    # (2)!
def setup(settings: SensorSettings) -> None:
    """Register one telemetry device per configured sensor."""
    for name, mac in settings.sensor_macs.items():   # (3)!
        app.add_telemetry(name, read_sensor, interval=10)


async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await read_ble(ctx.name)}


app.run()
```

1. Device list lives in configuration — deploy-time JSON via
   `BLE_SENSOR_MACS='{"living_room": "AA:BB:CC:DD:EE:01"}'`.
2. No parentheses — `@app.on_configure`, not `@app.on_configure()`.
   Both sync and async hooks are supported.
3. The hook has access to fully resolved settings, so it can drive
   device registration from configuration.

### Why Not Just Use `app.settings`?

You might wonder why `@app.on_configure` is needed when `app.settings` is available
at module level:

```python title="Looks reasonable, but has a gotcha"
for name in app.settings.sensor_macs:
    app.add_telemetry(name, read_sensor, interval=10)
```

This works — until someone runs `myapp --help`. The `App` constructor tries to
instantiate `Settings`, which reads environment variables. If required variables
are missing, `--help` crashes with a `ValidationError` instead of showing usage.

`@app.on_configure` runs **after** the CLI has parsed arguments and settings are
fully resolved. It's safe even when the app is invoked with `--help` or
`--version` — those exit before hooks execute.

!!! tip "Rule of thumb"

    - **Static values known at import time** → use `app.settings` directly
      (e.g. `enabled=app.settings.enable_debug`)
    - **Dynamic registration based on settings** → use `@app.on_configure`

## Combining Both: A Complete Example

The most powerful pattern combines `@app.on_configure` for settings-driven setup
with dict-name for declarative multi-device registration:

```python title="app.py — a well-structured multi-sensor bridge"
from __future__ import annotations

from dataclasses import dataclass

import cosalette
from pydantic import Field
from pydantic_settings import SettingsConfigDict

from ble2mqtt.ports import BleAdapterPort


# -- Settings ---------------------------------------------------------------

@dataclass
class SensorConfig:
    """Per-sensor configuration — one instance per BLE device."""
    mac: str
    poll_seconds: float = 10.0
    location: str = ""


class BleSettings(cosalette.Settings):
    model_config = SettingsConfigDict(env_prefix="BLE_")

    sensors: dict[str, SensorConfig] = Field(default_factory=dict)  # (1)!
    adapter_timeout: float = 5.0


# -- App setup ---------------------------------------------------------------

app = cosalette.App(
    name="ble2mqtt",
    version="1.0.0",
    settings_class=BleSettings,
)

app.adapter(BleAdapterPort, BleAdapter, dry_run=MockBleAdapter)


# -- Lifecycle hook ----------------------------------------------------------

@app.on_configure                                    # (2)!
def validate_sensors(settings: BleSettings) -> None:
    """Fail fast if no sensors are configured."""
    if not settings.sensors:
        msg = "No sensors configured — set BLE_SENSORS env var"
        raise SystemExit(msg)


# -- Devices -----------------------------------------------------------------

@app.telemetry(
    name=lambda s: {                                 # (3)!
        name: cfg
        for name, cfg in s.sensors.items()
    },
    interval=lambda cfg: cfg.poll_seconds,           # (4)!
)
async def sensor(
    ctx: cosalette.DeviceContext,
    config: SensorConfig,                            # (5)!
) -> dict[str, object]:
    ble = ctx.adapter(BleAdapterPort)
    reading = await ble.read(config.mac, timeout=ctx.settings.adapter_timeout)
    return {
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "location": config.location,
    }


app.run()
```

1. Sensors are configured via environment:
   `BLE_SENSORS='{"kitchen": {"mac": "AA:...:01", "poll_seconds": 5}}'`
2. The `on_configure` hook runs first — validates that at least one sensor is
   configured. Fails fast with a clear message instead of silently doing nothing.
3. Dict-name callable reads `s.sensors` (the resolved `BleSettings` instance).
   Each configured sensor becomes a separate telemetry registration.
4. Per-device interval drawn from each sensor's `poll_seconds` field.
5. The `SensorConfig` for the current device is injected automatically.

**Deploy-time configuration:**

```bash
export BLE_SENSORS='{"kitchen": {"mac": "AA:01", "poll_seconds": 5}, "garage": {"mac": "AA:02", "poll_seconds": 60}}'
export BLE_ADAPTER_TIMEOUT=10.0
myapp run
```

**Result:** Two independent telemetry devices (`kitchen` polling every 5 s, `garage`
every 60 s), each publishing to `ble2mqtt/{name}/state`, with per-device config
injected into the handler — all from **one handler function** and **zero hardcoded
device names**.

## When to Use What

| Pattern | Best for | Example |
|---------|----------|---------|
| Static `name="sensor"` | Fixed, known devices | `@app.telemetry("temperature", interval=60)` |
| `name=lambda s: [...]` | Multiple names, no config | `name=lambda s: ["a", "b", "c"]` |
| `name=lambda s: {...}` | Multiple devices with config | `name=lambda s: {"a": Config(...)}` |
| `@app.on_configure` + imperative | Complex conditional logic | Validate settings, compute derived values, register dynamically |
| Dict-name + `on_configure` | Production multi-device apps | Settings-driven fleet with validation |

## Gotchas

### Empty Returns

If a `name=` callable returns an empty dict or list, the framework logs a warning
and the handler is silently not registered. No error is raised — this supports
optional device groups that may be empty in some deployments.

### Config Type Shadowing

The per-device config is injected by its **type**. If your config class has the same
type as a framework-provided injectable (e.g. `Settings`, `DeviceContext`,
`ClockPort`), the framework raises `TypeError` at startup. Use a dedicated dataclass
for your config.

### Duplicate Names

If two name-spec expansions produce the same device name, or a dynamic name collides
with a statically registered device, the framework raises `ValueError`. Names must be
unique across all registration types (telemetry, device, command), following the
[scoped name uniqueness](../concepts/architecture.md) convention.

### `--help` Safety

Dict-name callables and `@app.on_configure` hooks both run **after** CLI parsing.
They are never executed during `--help` or `--version`, so they're safe even when
required environment variables are not set.
