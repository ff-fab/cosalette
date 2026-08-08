---
icon: material/clock-fast
---

# Quickstart

In this tutorial you'll build a minimal cosalette app from scratch — a simulated
temperature sensor that publishes telemetry to MQTT every 5 seconds. Along the way
you'll learn the core concepts: the `App` orchestrator, device decorators,
configuration, and testing.

!!! tip "No hardware required"

    This quickstart uses a simulated sensor so you can follow along on any machine.
    The same patterns apply when you swap in real hardware (I²C, BLE, GPIO, etc.)
    — that's the hexagonal architecture at work (see
    [ADR-006](../adr/ADR-006-hexagonal-architecture.md)).

## Prerequisites

Before you begin, make sure you have:

- **Python 3.14+** — cosalette uses modern Python features (PEP 695 type parameter
  syntax, PEP 544 Protocols).
- **An MQTT broker** — [Mosquitto](https://mosquitto.org/) is the easiest to set up
  locally. On Debian/Ubuntu: `sudo apt install mosquitto`. On macOS:
  `brew install mosquitto`.
- **cosalette installed** — see the [installation instructions](index.md#installation).

!!! note "uv recommended"

    The examples below use [uv](https://docs.astral.sh/uv/) for project management.
    You can substitute `pip` if you prefer, but `uv` handles virtual environments
    and lockfiles automatically.

## 1. Create the Project

Set up a minimal project structure:

```bash
mkdir weather2mqtt && cd weather2mqtt
uv init --lib
uv add cosalette
```

!!! tip "Using GitHub Copilot or another coding agent?"

    Run `cosalette ai init` now to install `.github/instructions/cosalette.instructions.md`
    in your repository. See [AI-Assisted Development](ai-assisted-development.md).

Your project should look like this:

```text
weather2mqtt/
├── pyproject.toml
├── src/
│   └── weather2mqtt/
│       ├── __init__.py
│       └── app.py          # ← you'll create this
└── tests/
    └── test_app.py         # ← you'll create this
```

## 2. Define Your App

Create `src/weather2mqtt/app.py`:

```python title="src/weather2mqtt/app.py"
import cosalette  # (1)!

app = cosalette.App(  # (2)!
    name="weather2mqtt",
    version="0.1.0",
)
```

1.  The `cosalette` package re-exports everything you need from a single namespace.
    No need to import from private modules.
2.  `App` is the **composition root** — the central orchestrator that collects device
    registrations, lifespan logic, and adapter mappings, then runs the full async
    lifecycle. This follows the Inversion of Control principle
    (see [ADR-001](../adr/ADR-001-framework-architecture-style.md)).

The `name` parameter sets the **MQTT topic prefix** (`weather2mqtt/...`) and the
**log service name**. The `version` is exposed via the `--version` CLI flag.

## 3. Add a Telemetry Device

cosalette supports **three** device archetypes (see
[ADR-010](../adr/ADR-010-device-archetypes.md)):

- **Telemetry** (`@app.telemetry()`) — periodic read-and-publish, unidirectional.
- **Command** (`@app.command()`) — declarative per-command handler (recommended for most command use cases).
- **Command & Control** (`@app.device()`) — async generator with full lifecycle control and reaction boundaries.

For a sensor, the telemetry pattern is the right fit. Add this to `app.py`:

```python title="src/weather2mqtt/app.py" hl_lines="3-4 12-21"
import random

import cosalette

app = cosalette.App(
    name="weather2mqtt",
    version="0.1.0",
)


@app.telemetry("sensor", interval=5.0)  # (1)!
async def sensor() -> dict[str, object]:  # (2)!
    """Simulate a temperature and humidity sensor."""
    temperature = 20.0 + random.uniform(-2.0, 2.0)  # (3)!
    humidity = 55.0 + random.uniform(-5.0, 5.0)
    return {  # (4)!
        "temperature": round(temperature, 1),
        "humidity": round(humidity, 1),
    }
```

1.  `@app.telemetry` registers a periodic polling device. The framework calls your
    function every `interval` seconds (here, every 5s) and publishes the result
    automatically. The `interval` keyword argument is required.

    !!! tip "Configurable intervals"

        When using a custom settings class, `app.settings` is available at
        decoration time. This lets you drive `interval=` from configuration:

        ```python
        app = cosalette.App(
            name="weather2mqtt",
            version="0.1.0",
            settings_class=Weather2MqttSettings,
        )

        @app.telemetry("sensor", interval=app.settings.poll_interval)
        ```

        See the [Configuration guide](../guides/configuration.md#using-settings-in-decorator-arguments)
        for details.
2.  Handlers declare only the parameters they need. This simple sensor needs no
    infrastructure access, so it takes zero arguments. If you need settings, adapters,
    or the shutdown event, add a `ctx: cosalette.DeviceContext` parameter and the
    framework injects it automatically.
3.  We're simulating readings here. In a real app, you'd call your hardware
    adapter — e.g., `sensor.read()` for I²C, or `await ble_client.read()` for BLE.
4.  Returning a `dict` is the telemetry contract. The framework calls
    `ctx.publish_state(result)` for you, serialising the dict as JSON to the topic
    `weather2mqtt/sensor/state` with `retain=True` and `qos=1`.

!!! info "Telemetry vs. Command vs. Device"

    With `@app.telemetry()`, you **return** a dict and the framework publishes it.
    With `@app.command()`, you declare a handler for a single command device —
    the framework subscribes and dispatches automatically. Handlers only declare
    the parameters they need (`topic`, `payload`, or both).
    With `@app.device()`, you manage your own loop and call `ctx.publish_state()`
    yourself — giving you full control over timing, state transitions, and command
    handling.

## 4. Add an Entry Point

Add the entry point to `app.py`:

```python title="src/weather2mqtt/app.py" hl_lines="23-24"
import random

import cosalette

app = cosalette.App(
    name="weather2mqtt",
    version="0.1.0",
)


@app.telemetry("sensor", interval=5.0)
async def sensor() -> dict[str, object]:
    """Simulate a temperature and humidity sensor."""
    temperature = 20.0 + random.uniform(-2.0, 2.0)
    humidity = 55.0 + random.uniform(-5.0, 5.0)
    return {
        "temperature": round(temperature, 1),
        "humidity": round(humidity, 1),
    }


if __name__ == "__main__":
    app.run()  # (1)!
```

1.  `app.run()` builds a [Typer](https://typer.tiangolo.com/)-based CLI, parses
    command-line arguments, loads settings, and starts the async lifecycle. You get
    `--dry-run`, `--version`, `--log-level`, `--log-format`, and `--env-file` flags
    for free (see [ADR-005](../adr/ADR-005-cli-framework.md)).

## 5. Run the App

!!! warning "Broker required"

    Make sure your MQTT broker is running before starting the app. If Mosquitto is
    installed locally, `sudo systemctl start mosquitto` (or `brew services start
    mosquitto` on macOS).

Start the app:

```bash
uv run python src/weather2mqtt/app.py
```

You should see structured JSON log output as the app connects to MQTT and starts
publishing:

```json
{"timestamp": "2026-02-17T10:00:00+00:00", "level": "INFO", "logger": "cosalette._mqtt", "message": "MQTT connected to localhost:1883", "service": "weather2mqtt"}
```

In another terminal, subscribe to see the telemetry:

```bash
mosquitto_sub -t "weather2mqtt/#" -v
```

Every 5 seconds you'll see messages like:

```text
weather2mqtt/sensor/state {"temperature": 19.3, "humidity": 52.7}
weather2mqtt/sensor/availability online
```

The framework automatically publishes per-device **availability** on
`{prefix}/{device}/availability` when devices start. On unexpected disconnection,
the broker publishes an **LWT** (Last Will & Testament) "offline" message on
`{prefix}/status` — that's the health reporting system
(see [ADR-012](../adr/ADR-012-health-and-availability-reporting.md)).

Press ++ctrl+c++ to shut down gracefully. The framework handles SIGINT/SIGTERM,
cancels device tasks, publishes an offline status, and disconnects cleanly.

## 6. Add Configuration

cosalette uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
for configuration. The base `Settings` class provides MQTT and logging configuration
out of the box with the `__` (double underscore) nested delimiter.

### Environment Variables

Override any setting via environment variables:

```bash
MQTT__HOST=192.168.1.100 MQTT__PORT=1883 uv run python src/weather2mqtt/app.py
```

### `.env` Files

Create a `.env` file in your project root:

```bash title=".env"
MQTT__HOST=192.168.1.100
MQTT__PORT=1883
MQTT__USERNAME=iot
MQTT__PASSWORD=secret
LOGGING__LEVEL=DEBUG
LOGGING__FORMAT=text
```

The app loads `.env` automatically on startup (configurable with `--env-file`).

!!! tip "Configuration hierarchy"

    Settings are resolved in this order (highest priority first):

    1. CLI flags (`--log-level`, `--log-format`)
    2. Environment variables
    3. `.env` file
    4. Model defaults (e.g., `mqtt.host="localhost"`)

    This follows the [twelve-factor app](https://12factor.net/config) methodology.
    See [ADR-003](../adr/ADR-003-configuration-system.md) for the full rationale.

## 7. Explore the CLI

`app.run()` gives you a full CLI for free. Try these flags:

```bash
# Show version
uv run python src/weather2mqtt/app.py --version

# Override log level for debugging
uv run python src/weather2mqtt/app.py --log-level DEBUG

# Use human-readable log format instead of JSON
uv run python src/weather2mqtt/app.py --log-format text

# Point to a different .env file
uv run python src/weather2mqtt/app.py --env-file production.env
```

### Dry-Run Mode

The `--dry-run` flag is designed for testing without real hardware. When you register
adapters with a `dry_run` variant, the framework automatically swaps implementations:

```bash
uv run python src/weather2mqtt/app.py --dry-run
```

For the simple weather app (which has no custom adapters), `--dry-run` sets the flag
but doesn't change behaviour. It becomes powerful when you add hardware adapters
with mock alternatives — for example, an I²C adapter with a simulated dry-run variant.

!!! info "Dry-run and adapters"

    Dry-run mode swaps **registered adapters** to their dry-run variants, not the MQTT
    client itself. This means your app still connects to MQTT and publishes — but the
    hardware interactions use safe stand-ins. Register dry-run adapters like this:

    ```python
    app.adapter(
        SensorPort,
        RealSensorAdapter,
        dry_run=SimulatedSensorAdapter,  # used when --dry-run is passed
    )
    ```

    See [Hardware Adapters](../guides/adapters.md) for the full pattern.

## 8. Add a Test

Testing is a first-class concern in cosalette (see
[ADR-007](../adr/ADR-007-testing-strategy.md)). The `cosalette.testing` module
provides pre-built test doubles so you never need a real MQTT broker or hardware in
your test suite.

Create `tests/test_app.py`:

```python title="tests/test_app.py"
import asyncio
import contextlib
import json

import pytest

from cosalette.testing import AppHarness  # (1)!


@pytest.fixture
def harness() -> AppHarness:
    """Create a test harness with fresh doubles."""
    return AppHarness.create(name="weather2mqtt")  # (2)!


@pytest.mark.asyncio
async def test_sensor_publishes_telemetry(harness: AppHarness) -> None:
    """Verify the sensor device publishes state to MQTT."""
    from weather2mqtt.app import sensor  # (3)!

    # Register the telemetry function on the harness's app
    harness.app.telemetry("sensor", interval=0.01)(sensor)  # (4)!

    # Track when a publish arrives, then trigger shutdown
    publish_done = asyncio.Event()
    original_publish = harness.mqtt.publish

    async def _tracking_publish(  # (5)!
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        await original_publish(topic, payload, retain=retain, qos=qos)
        if topic == "weather2mqtt/sensor/state":
            publish_done.set()

    harness.mqtt.publish = _tracking_publish  # type: ignore[assignment]

    async def _shutdown_after_first_publish() -> None:  # (6)!
        await publish_done.wait()
        harness.trigger_shutdown()

    _task = asyncio.create_task(_shutdown_after_first_publish())
    try:
        await asyncio.wait_for(harness.run(), timeout=5.0)  # (7)!
    finally:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task

    # Assert: the sensor published to the correct topic
    messages = harness.mqtt.get_messages_for("weather2mqtt/sensor/state")  # (8)!
    assert len(messages) >= 1

    payload_str, retain, qos = messages[0]
    assert retain is True
    assert qos == 1

    payload = json.loads(payload_str)
    assert "temperature" in payload  # (9)!
    assert "humidity" in payload
```

1.  `AppHarness` is the integration-test entry point. It bundles a fresh `App`,
    `MockMqttClient`, `FakeClock`, and `Settings` — no real I/O anywhere.
2.  `create()` is a classmethod that wires everything together. You can pass
    `**settings_overrides` to customise configuration.
3.  Import your device function. Since it's decorated with `@app.telemetry` at
    module level, you need to re-register it on the harness's app.
4.  The `@app.telemetry` decorator returns the original function unchanged, so you
    can re-register the same function on a different `App` instance. We use a tiny
    interval (`0.01s`) so the test runs fast.
5.  We wrap `publish` with a tracking function that sets an event when the
    telemetry message arrives. This is the idiomatic pattern for waiting on
    async MQTT publishes in tests.
6.  A background task waits for the first publish, then triggers graceful shutdown.
    This runs concurrently with the app lifecycle via `asyncio.create_task`.
7.  `asyncio.wait_for` adds a safety timeout — if something goes wrong, the test
    fails after 5 seconds instead of hanging forever. The `try/finally` ensures
    the background task is always cancelled, avoiding "Task was destroyed but it
    is still pending" warnings.
8.  `get_messages_for()` returns `(payload, retain, qos)` tuples for a given topic.
    This is the primary assertion point for MQTT behaviour.
9.  We check for key presence rather than exact values since the simulated sensor
    uses `random.uniform`. In a real app with deterministic hardware mocks, you'd
    assert exact values.

Run the test:

```bash
uv run pytest tests/test_app.py -v
```

!!! tip "Test utilities at a glance"

    | Class / Function | Purpose |
    |------------------|---------|
    | `AppHarness.create()` | Full integration harness with test doubles |
    | `MockMqttClient` | In-memory MQTT double — records publishes and subscriptions |
    | `FakeClock` | Deterministic clock — manually set `._time` |
    | `NullMqttClient` | Silent no-op MQTT adapter |
    | `make_settings()` | Create `Settings` without `.env` files or environment leakage |

    Import everything from `cosalette.testing`:

    ```python
    from cosalette.testing import AppHarness, MockMqttClient, FakeClock, make_settings
    ```

## 9. Next Step: Router Composition

You've used app-level decorators (`@app.telemetry()`, `@app.command()`) throughout
this quickstart — and that's the right pattern for small, single-file apps like this
weather daemon. But when your application grows beyond a few devices or needs
multi-module organization, use **Router** for composition.

### When to Use Router

| Pattern              | Use when                                                     |
| -------------------- | ------------------------------------------------------------ |
| App-level decorators | Single-file apps, quickstart examples, simple bridges (≤3 devices) |
| **Router composition** | Multi-module production apps, shared libraries, testable boundaries |

### Quick Router Example

Instead of decorating functions directly on `app`, you can group related devices
in a router and include it:

```python title="sensors.py — router module"
import cosalette

router = cosalette.Router(prefix="sensors", tags=["environment"])


@router.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, object]:
    return {"celsius": 22.5}


@router.telemetry("humidity", interval=30)
async def read_humidity() -> dict[str, object]:
    return {"percent": 55.0}
```

```python title="main.py — composition root"
import cosalette
from sensors import router as sensors_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(sensors_router)

if __name__ == "__main__":
    app.run()
```

**MQTT topics:**

- `home2mqtt/sensors/temperature/state`
- `home2mqtt/sensors/humidity/state`

The `prefix="sensors"` parameter adds a topic segment, and `sensors.py` can be
unit-tested independently without creating an `App`.

### Why Router?

- **No circular imports** — router modules don't import `app`
- **Testable boundaries** — unit test router registrations in isolation
- **Topic organization** — group devices under shared prefixes
- **Reusable modules** — package routers as shared libraries

For full details, see the [Router Composition guide](../guides/router-composition.md)
and [Router concept](../concepts/router.md).

## 10. Typed Contracts & Schema Inspection

So far you've used untyped `dict[str, object]` payloads and returns — that's fine for
quick prototypes, but production apps benefit from **Pydantic-validated contracts**.
cosalette supports typed payloads, typed returns, and dependency injection for
type-safe command handlers and telemetry.

### Typed Command Payloads

Instead of reading a raw JSON string, use `Annotated[Model, cosalette.Payload()]` to
parse and validate incoming commands:

```python title="src/weather2mqtt/app.py — typed command" hl_lines="1 3-7 10-13"
from typing import Annotated
from pydantic import BaseModel
import cosalette

class SetpointCommand(BaseModel):
    """Command payload schema for setting temperature threshold."""
    value: float
    unit: str = "celsius"

@app.command("sensor/set_threshold")
async def set_threshold(cmd: Annotated[SetpointCommand, cosalette.Payload()]) -> None:
    """Handle threshold updates with validated payload."""
    print(f"Threshold set to {cmd.value}°{cmd.unit}")
```

When a message arrives on `weather2mqtt/sensor/set_threshold/set`, cosalette
deserialises the JSON payload as a `SetpointCommand` instance. If validation fails
(e.g., `value` is missing or not a float), a `PayloadValidationError` is published to
`weather2mqtt/sensor/set_threshold/error` automatically.

### Typed Returns

You can also return a Pydantic model instead of a dict. The framework serialises it
to JSON automatically:

```python title="src/weather2mqtt/app.py — typed return" hl_lines="4-8 11-12"
from pydantic import BaseModel
import cosalette

class SensorState(BaseModel):
    """Telemetry state schema."""
    temperature: float
    humidity: float
    unit: str = "celsius"

@app.telemetry("sensor", interval=5.0)
async def sensor() -> SensorState:
    return SensorState(temperature=21.5, humidity=55.0)
```

The framework validates the return value against the model schema. If validation fails,
a `ReturnValidationError` is published to the device error topic.

### Dependency Injection

Use `cosalette.Depends()` to inject shared logic — for example, extracting a device ID
from configuration or context:

```python title="src/weather2mqtt/app.py — DI" hl_lines="1-3 6-9"
def get_device_id() -> str:
    """Dependency that returns the device identifier."""
    return "sensor-001"

@app.command("sensor/calibrate")
async def calibrate(
    device_id: Annotated[str, cosalette.Depends(get_device_id)],
) -> None:
    print(f"Calibrating device {device_id}")
```

Dependencies can declare their own dependencies, creating a DI graph. The framework
caches the resolution **plan** (the mapping from parameter names to types) but invokes
dependency callables on every resolution — results are not memoized. See the
[Dependency Injection concept](../concepts/dependency-injection.md) for full details.

### AsyncAPI Inspection

cosalette introspects your app's device registrations and generates an **AsyncAPI 3.0.0**
contract document. This is useful for:

- Generating consumer code (TypeScript, Go, etc.)
- Validating message schemas in integration tests
- Documenting the MQTT API for other developers

**Inspect via CLI:**

```bash
cosalette manifest weather2mqtt.app:app           # JSON output
cosalette manifest weather2mqtt.app:app --table   # human-readable table
```

**Or programmatically:**

```python
doc = app.asyncapi()  # returns dict conforming to AsyncAPI 3.0.0
```

The document includes:

- All device topic patterns (`weather2mqtt/sensor/state`, `weather2mqtt/sensor/set_threshold/set`, etc.)
- Pydantic schemas for typed payloads and returns (as JSON Schema)
- Operation metadata (archetype, interval, tags)
- `x-cosalette-contract-version` extension for contract evolution tracking

See the [Contract-First Route Design guide](../guides/contract-first-route-design.md)
and [Schema Enforcement guide](../guides/schema-enforcement.md) for full patterns.

## What's Next?

You've built a working telemetry daemon with configuration, a CLI, and tests.
Here's where to go from here:

<div class="grid cards" markdown>

-   :material-map:{ .lg .middle } **Architecture**

    ---

    Understand the composition-root pattern, the async lifecycle, and how cosalette
    orchestrates your devices.

    [:octicons-arrow-right-24: Architecture](../concepts/architecture.md)

-   :material-devices:{ .lg .middle } **Device Archetypes**

    ---

    Learn about telemetry vs. command & control devices, and when to use each pattern.

    [:octicons-arrow-right-24: Device Archetypes](../concepts/device-archetypes.md)

-   :material-cog:{ .lg .middle } **Configuration Guide**

    ---

    Subclass `Settings`, add custom fields, wire environment variables and `.env`
    files.

    [:octicons-arrow-right-24: Configuration](../guides/configuration.md)

-   :material-test-tube:{ .lg .middle } **Testing Guide**

    ---

    Advanced testing patterns — `AppHarness`, adapter mocking, command simulation,
    and error injection.

    [:octicons-arrow-right-24: Testing](../guides/testing.md)

-   :material-filter:{ .lg .middle } **Publish Strategies**

    ---

    Control when telemetry is published — time-based, change-based, or threshold-based
    filtering.

    [:octicons-arrow-right-24: Telemetry Guide — Strategies](../guides/telemetry-device.md#publish-strategies)

-   :material-rocket-launch:{ .lg .middle } **Full App Capstone**

    ---

    Put it all together — hardware adapters, command devices, lifespan, custom errors,
    and a full test suite in one production-ready bridge.

    [:octicons-arrow-right-24: Build a Complete IoT Bridge](full-app.md)

</div>
