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
uv add "cosalette @ git+https://github.com/ff-fab/cosalette.git"
```

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
- **Command & Control** (`@app.device()`) — bidirectional coroutine with full lifecycle control.

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

</div>
