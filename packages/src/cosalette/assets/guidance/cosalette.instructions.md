---
description: 'cosalette framework development guidance for AI agents'
applyTo: '**/*.py'
---

# cosalette Framework Instructions

## Framework Overview

**cosalette** is a Python framework for IoT-to-MQTT bridge applications. Build declarative apps using the `App` composition root and decorators for device registration.

## App Composition Pattern

Create apps in `app.py` or `main.py` using declarative registration:

```python
import cosalette

app = cosalette.App(
    name="mybridge",
    version="0.1.0",
    settings_class=MySettings,  # optional custom settings
)

# The entry point runs the app
if __name__ == "__main__":
    app.run()
```

## Device Registration

### Telemetry (Periodic Publishing)
```python
@app.telemetry("sensor", interval=30.0)  # seconds
async def sensor() -> dict[str, object]:
    """Return dict - framework publishes automatically."""
    return {"temperature": 23.5, "humidity": 65.0}

# With dependency injection
@app.telemetry("cpu", interval=10.0, init=make_monitor)
async def cpu_usage(monitor: CpuMonitor) -> dict[str, object]:
    return {"cpu_percent": monitor.get_usage()}

# With context access
@app.telemetry("memory", interval=60.0)
async def memory(ctx: cosalette.DeviceContext) -> dict[str, object]:
    # Access settings, state, shutdown signal
    if ctx.shutdown_requested:
        return {}
    return {"memory_mb": get_memory_usage()}
```

### Command Handlers
```python
@app.command("lights")
async def control_lights(payload: str) -> dict[str, object] | None:
    """Handle commands sent to mybridge/lights/set."""
    import json
    data = json.loads(payload)
    if data.get("action") == "turn_on":
        await turn_on_lights()
        return {"state": "on"}  # Published to /state
    elif data.get("action") == "turn_off":
        await turn_off_lights()
        return {"state": "off"}
    return None
```

### Device Coroutines (Full Control)
```python
@app.device("sensor")
async def sensor_loop(ctx: cosalette.DeviceContext) -> None:
    """Manage your own lifecycle and publishing."""
    while not ctx.shutdown_requested:
        data = await read_sensor()
        await ctx.publish_state({"value": data})
        await ctx.sleep(30)  # respect shutdown
```

## Type-Based Dependency Injection

Use `init=` factories for dependencies:

```python
def make_client() -> SensorClient:
    return SensorClient("/dev/ttyUSB0")

@app.telemetry("temp", interval=30, init=make_client)
async def temperature(client: SensorClient) -> dict[str, object]:
    return {"celsius": await client.read_temperature()}

# Single dependency example
@app.telemetry("combo", interval=60, init=make_logger)
async def combo(logger: logging.Logger) -> dict[str, object]:
    logger.info("Reading combined sensors")
    return {"status": "active"}
```

## Publishing and Persistence

Configure publishing strategies and state persistence:

```python
from cosalette import OnChange, SaveOnChange

@app.telemetry("filtered", interval=5.0,
               publish=OnChange(),      # publish only when value changes
               persist=SaveOnChange())  # save state only when changed
async def filtered_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    # Use ctx.state for persistent storage
    ctx.state.setdefault("calibration", 0.0)
    raw_value = await read_raw_sensor()
    return {"value": raw_value + ctx.state["calibration"]}
```

## Configuration and Settings

Extend `cosalette.Settings` for custom configuration:

```python
from cosalette import Settings
from pydantic_settings import SettingsConfigDict

class MyAppSettings(Settings):
    sensor_port: str = "/dev/ttyUSB0"
    poll_interval: float = 30.0
    calibration_offset: float = 0.0

    model_config = SettingsConfigDict(
        env_prefix="MYAPP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

app = cosalette.App(
    name="mybridge",
    version="1.0.0",
    settings_class=MyAppSettings
)

# Access settings in handlers via context
@app.telemetry("sensor", interval=app.settings.poll_interval)
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    port = ctx.settings.sensor_port
    return await read_sensor(port)
```

## Dynamic Device Registration

Use `@app.on_configure` for device registration based on runtime settings:

```python
@app.on_configure
def register_devices(settings: MySettings) -> None:
    """Register devices based on runtime settings."""
    for sensor in settings.sensors:
        app.add_telemetry(
            name=sensor.name,
            func=make_sensor_handler(sensor),
            interval=sensor.poll_interval
        )

    for outlet in settings.outlets:
        app.add_device(outlet.name, make_outlet_handler(outlet))
```

## Testing

Use framework testing utilities:

- `cosalette.testing.AppHarness` — one-liner setup for integration tests
- `MockMqttClient` — in-memory MQTT double for testing
- `FakeClock` — deterministic timing for tests
- Pytest plugin for fixtures: `pytest_plugins = ["cosalette.testing._plugin"]`

Test handlers directly or use `AppHarness.create()` for full app testing.
Refer to `cosalette ai help testing` for detailed patterns.

## Error Handling

```python
@app.telemetry("sensor", interval=30)
async def sensor_with_errors() -> dict[str, object] | None:
    try:
        return {"value": await read_sensor()}
    except SensorTimeout:
        return None  # Temporary failure - retry later
    except SensorNotFound:
        raise  # Permanent failure - stop device
```

## Triggerable Telemetry

Add `triggerable=True` to make a telemetry device respond to inbound MQTT messages
on `{prefix}/{device}/set` in addition to the normal polling interval:

```python
@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor() -> dict[str, object]:
    return {"temperature": await read_sensor()}
```

Opt into `TriggerPayload` to distinguish triggered vs scheduled runs:

```python
from cosalette import TriggerPayload

@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor(trigger: TriggerPayload) -> dict[str, object]:
    days = trigger.get("days", 7) if trigger.is_triggered else 7
    return {"data": await read_sensor(days=days)}
```

Constraints: root (unnamed) devices cannot be triggerable; `triggerable=` and `group=`
are mutually exclusive. Refer to `cosalette ai help triggerable` for details.

Install the instruction file via: `cosalette ai init`
For comprehensive topic help: `cosalette ai help <topic>` (architecture, telemetry, testing, configuration, commands, health, scheduling, resilience, sub-entities, triggerable)
