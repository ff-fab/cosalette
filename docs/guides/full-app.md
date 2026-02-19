---
icon: material/rocket-launch
---

# Build a Complete IoT Bridge

This capstone guide combines everything from the previous guides into a complete,
production-ready `gas2mqtt` application. You'll build a gas meter bridge daemon with
telemetry polling, valve commands, hardware abstraction, lifecycle management, custom
error types, and a full test suite.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md) and are familiar with the individual
    guides:

    - [Telemetry Device](telemetry-device.md)
    - [Command & Control Device](command-device.md)
    - [Configuration](configuration.md)
    - [Hardware Adapters](adapters.md)
    - [Lifecycle Hooks](lifecycle-hooks.md)
    - [Testing](testing.md)
    - [Custom Error Types](error-types.md)

## 1. Project Structure

```text
gas2mqtt/
├── pyproject.toml
├── .env
├── src/
│   └── gas2mqtt/
│       ├── __init__.py
│       ├── app.py            # App assembly + devices
│       ├── settings.py       # Custom settings
│       ├── ports.py          # Protocol ports
│       ├── adapters.py       # Hardware adapters
│       └── errors.py         # Domain exceptions
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_counter.py
    │   ├── test_valve.py
    │   └── test_errors.py
    └── integration/
        └── test_app.py
```

Each file has a single responsibility — this keeps the codebase navigable and
testable as the project grows.

## 2. Custom Settings

Define app-specific configuration fields, inheriting MQTT and logging settings from
the framework:

```python title="src/gas2mqtt/settings.py"
"""Configuration for gas2mqtt."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

import cosalette


class Gas2MqttSettings(cosalette.Settings):
    """Gas meter bridge configuration.

    Environment variables use the ``GAS2MQTT_`` prefix.
    Nested models use ``__`` as delimiter:
    ``GAS2MQTT_MQTT__HOST=broker.local``.
    """

    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Hardware
    serial_port: str = Field(
        default="/dev/ttyUSB0",
        description="Serial port for the gas meter sensor.",
    )
    baud_rate: int = Field(
        default=9600,
        description="Serial baud rate.",
    )

    # Polling
    counter_interval: int = Field(
        default=60,
        ge=1,
        description="Impulse counter polling interval in seconds.",
    )

    @field_validator("serial_port")
    @classmethod
    def serial_port_must_be_device(cls, v: str) -> str:
        """Validate that serial_port looks like a device path."""
        if not v.startswith("/dev/"):
            msg = f"serial_port must be a /dev/ path, got: {v!r}"
            raise ValueError(msg)
        return v
```

!!! info "Why subclass `Settings`?"

    The base `cosalette.Settings` includes `mqtt` and `logging` sub-models. By
    subclassing, your app inherits broker connection and logging config for free —
    you only add the fields unique to gas2mqtt. See [Configuration](configuration.md)
    for the full guide.

## 3. Protocol Port

Define the hardware abstraction as a PEP 544 Protocol:

```python title="src/gas2mqtt/ports.py"
"""Protocol ports for gas2mqtt hardware abstraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GasMeterPort(Protocol):
    """Hardware abstraction for gas meter sensors.

    Implementations provide impulse counting and temperature
    reading over the protocol boundary.
    """

    def connect(self, port: str, baud_rate: int = 9600) -> None:
        """Open connection to the sensor."""
        ...

    def read_impulses(self) -> int:
        """Read the current impulse count."""
        ...

    def read_temperature(self) -> float:
        """Read the sensor's temperature reading in Celsius."""
        ...

    def close(self) -> None:
        """Close the hardware connection."""
        ...
```

The port defines _what_ your code needs. The adapters in the next section define
_how_ to provide it.

## 4. Real Adapter

The production adapter communicates over a serial port:

```python title="src/gas2mqtt/adapters.py"
"""Hardware adapter implementations for gas2mqtt."""

from __future__ import annotations


class SerialGasMeter:
    """Real gas meter adapter communicating over a serial port.

    Uses ``pyserial`` for UART communication. Imported lazily by
    the framework via ``"gas2mqtt.adapters:SerialGasMeter"`` so
    that ``pyserial`` doesn't need to be installed on dev machines.
    """

    def __init__(self) -> None:
        self._conn = None

    def connect(self, port: str, baud_rate: int = 9600) -> None:
        """Open the serial connection."""
        import serial  # (1)!

        self._conn = serial.Serial(port, baud_rate, timeout=5)

    def read_impulses(self) -> int:
        """Read impulse count from the meter."""
        assert self._conn is not None, "Call connect() first"
        self._conn.write(b"READ_IMPULSES\n")
        response = self._conn.readline().decode().strip()
        return int(response)

    def read_temperature(self) -> float:
        """Read temperature from the meter's built-in sensor."""
        assert self._conn is not None, "Call connect() first"
        self._conn.write(b"READ_TEMP\n")
        response = self._conn.readline().decode().strip()
        return float(response)

    def close(self) -> None:
        """Close the serial connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
```

1. `pyserial` is imported inside the method, not at module level. This is the
   hexagonal lazy-import pattern ([ADR-006](../adr/ADR-006-hexagonal-architecture.md))
   — the module can be imported on machines without `pyserial` installed.

## 5. Mock Adapter

A fake implementation for `--dry-run` mode and testing:

```python title="src/gas2mqtt/adapters.py (continued)"
class FakeGasMeter:
    """Mock gas meter for dry-run mode and testing.

    Returns incrementing impulse counts and a fixed temperature.
    Requires no hardware or external libraries.
    """

    def __init__(self) -> None:
        self._impulses = 0
        self._connected = False

    def connect(self, port: str, baud_rate: int = 9600) -> None:
        self._connected = True

    def read_impulses(self) -> int:
        self._impulses += 1
        return self._impulses

    def read_temperature(self) -> float:
        return 21.5

    def close(self) -> None:
        self._connected = False
```

!!! tip "Fake vs Stub vs Mock"

    `FakeGasMeter` is a _fake_ — it has working logic (incrementing counter) but no
    real hardware dependency. Fakes are great for dry-run mode because they produce
    realistic-looking data. In unit tests, you might use simpler stubs with fixed
    return values.

## 6. Telemetry Device

The impulse counter polls the gas meter sensor at a fixed interval:

```python title="src/gas2mqtt/app.py (counter device)"
@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read gas meter impulses and temperature.

    The framework calls this every 60 seconds. The returned dict
    is published as JSON to ``gas2mqtt/counter/state``.
    """
    meter = ctx.adapter(GasMeterPort)

    impulses = meter.read_impulses()
    temperature = meter.read_temperature()

    if impulses < 0:
        raise InvalidReadingError(f"Negative impulse count: {impulses}")

    return {
        "impulses": impulses,
        "temperature_celsius": temperature,
        "unit": "m³",
    }
```

This is the return-dict contract in action: your function reads the sensor and
returns data. The framework handles JSON serialisation, MQTT publication, error
catching, and the timing loop.

## 7. Command Device

The valve device receives open/close commands via MQTT and publishes state:

```python title="src/gas2mqtt/app.py (valve device)"
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    """Control the gas valve via MQTT commands.

    Subscribes to ``gas2mqtt/valve/set`` for inbound commands.
    Publishes state to ``gas2mqtt/valve/state``.
    """
    state = "closed"

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        match payload:
            case "open":
                state = "open"
            case "close":
                state = "closed"
            case "toggle":
                state = "open" if state == "closed" else "closed"
            case _:
                raise ValueError(
                    f"Unknown command: {payload!r}. Valid: open, close, toggle"
                )

        await ctx.publish_state({"state": state})

    # Publish initial state
    await ctx.publish_state({"state": state})

    # Keep alive
    while not ctx.shutdown_requested:
        await ctx.sleep(30)
```

## 8. Lifecycle Hooks

Initialise the serial connection at startup, close it at shutdown:

```python title="src/gas2mqtt/app.py (hooks)"
@app.on_startup
async def init_serial(ctx: cosalette.AppContext) -> None:
    """Open serial connection to the gas meter before devices start."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)


@app.on_shutdown
async def close_serial(ctx: cosalette.AppContext) -> None:
    """Close serial connection after devices stop."""
    meter = ctx.adapter(GasMeterPort)
    meter.close()
```

!!! warning "AppContext — limited API"

    Hooks receive `AppContext`, which has only `.settings` and `.adapter()`. There is
    NO `publish_state()`, `sleep()`, or `on_command` — those are `DeviceContext`-only.
    See [Lifecycle Hooks](lifecycle-hooks.md) for details.

## 9. Custom Error Types

Define domain exceptions and the error type map:

```python title="src/gas2mqtt/errors.py"
"""Domain exceptions for gas2mqtt."""


class SensorTimeoutError(Exception):
    """Gas meter sensor didn't respond within the timeout period."""


class InvalidReadingError(Exception):
    """Sensor returned a reading outside valid physical bounds."""


class ConnectionLostError(Exception):
    """Serial connection to the gas meter was lost."""


error_type_map: dict[type[Exception], str] = {
    SensorTimeoutError: "sensor_timeout",
    InvalidReadingError: "invalid_reading",
    ConnectionLostError: "connection_lost",
}
```

When `counter` raises `InvalidReadingError("Negative impulse count: -3")`, the
framework's error isolation catches it and publishes:

```json title="gas2mqtt/counter/error"
{
    "error_type": "error",
    "message": "Negative impulse count: -3",
    "device": "counter",
    "timestamp": "2026-02-18T10:30:00+00:00",
    "details": {}
}
```

The framework uses the generic `"error"` type for all auto-caught exceptions.
To get domain-specific types like `"invalid_reading"`, use `build_error_payload()`
manually — see [Custom Error Types](error-types.md)
for the full guide.

## 10. App Assembly

Wire everything together in `app.py`:

```python title="src/gas2mqtt/app.py"
"""gas2mqtt — Gas meter IoT-to-MQTT bridge.

A complete cosalette application with telemetry polling,
command control, hardware abstraction, and lifecycle hooks.
"""

from __future__ import annotations

import cosalette

from gas2mqtt.adapters import FakeGasMeter
from gas2mqtt.errors import InvalidReadingError
from gas2mqtt.ports import GasMeterPort
from gas2mqtt.settings import Gas2MqttSettings

# --- App construction ---

app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
)

# --- Adapter registration ---

app.adapter(
    GasMeterPort,
    "gas2mqtt.adapters:SerialGasMeter",  # (1)!
    dry_run=FakeGasMeter,  # (2)!
)

# --- Lifecycle hooks ---


@app.on_startup
async def init_serial(ctx: cosalette.AppContext) -> None:
    """Open serial connection before devices start."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)


@app.on_shutdown
async def close_serial(ctx: cosalette.AppContext) -> None:
    """Close serial connection after devices stop."""
    meter = ctx.adapter(GasMeterPort)
    meter.close()


# --- Telemetry device ---


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read gas meter impulses and temperature."""
    meter = ctx.adapter(GasMeterPort)

    impulses = meter.read_impulses()
    temperature = meter.read_temperature()

    if impulses < 0:
        raise InvalidReadingError(f"Negative impulse count: {impulses}")

    return {
        "impulses": impulses,
        "temperature_celsius": temperature,
        "unit": "m³",
    }


# --- Command device ---


@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    """Control the gas valve via MQTT commands."""
    state = "closed"

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        match payload:
            case "open":
                state = "open"
            case "close":
                state = "closed"
            case "toggle":
                state = "open" if state == "closed" else "closed"
            case _:
                raise ValueError(
                    f"Unknown command: {payload!r}. "
                    f"Valid: open, close, toggle"
                )

        await ctx.publish_state({"state": state})

    await ctx.publish_state({"state": state})
    while not ctx.shutdown_requested:
        await ctx.sleep(30)


# --- Entry point ---


app.run()
```

1. `SerialGasMeter` is imported lazily — `pyserial` doesn't need to be installed
   on dev machines or in CI. The framework imports it at startup only in production.
2. `FakeGasMeter` is used when running `gas2mqtt --dry-run`. It returns simulated
   data without any hardware.

## 11. Test Suite

### Test Configuration

```python title="tests/conftest.py"
"""Shared pytest configuration for gas2mqtt tests."""

pytest_plugins = ["cosalette.testing._plugin"]
```

### Unit Tests: Counter

```python title="tests/unit/test_counter.py"
"""Unit tests for the counter telemetry device.

Test Techniques Used:
- Specification-based: Return-dict contract verification.
- Error Guessing: Invalid reading detection.
- Boundary Value Analysis: Edge case at impulses = 0.
"""

from __future__ import annotations

import pytest

from gas2mqtt.adapters import FakeGasMeter
from gas2mqtt.errors import InvalidReadingError
from gas2mqtt.ports import GasMeterPort


class StubGasMeter:
    """Stub with configurable return values."""

    def __init__(self, impulses: int = 42, temperature: float = 21.5) -> None:
        self.impulses = impulses
        self.temperature = temperature

    def connect(self, port: str, baud_rate: int = 9600) -> None:
        pass

    def read_impulses(self) -> int:
        return self.impulses

    def read_temperature(self) -> float:
        return self.temperature

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_counter_returns_impulse_dict(device_context):
    """Counter returns dict with impulses, temperature, and unit."""
    device_context._adapters[GasMeterPort] = StubGasMeter(
        impulses=100, temperature=22.0
    )

    from gas2mqtt.app import counter

    result = await counter(device_context)

    assert result == {
        "impulses": 100,
        "temperature_celsius": 22.0,
        "unit": "m³",
    }


@pytest.mark.asyncio
async def test_counter_rejects_negative_impulses(device_context):
    """Negative impulse count raises InvalidReadingError."""
    device_context._adapters[GasMeterPort] = StubGasMeter(impulses=-1)

    from gas2mqtt.app import counter

    with pytest.raises(InvalidReadingError, match="Negative impulse count"):
        await counter(device_context)


@pytest.mark.asyncio
async def test_counter_accepts_zero_impulses(device_context):
    """Zero is a valid impulse count (boundary value)."""
    device_context._adapters[GasMeterPort] = StubGasMeter(impulses=0)

    from gas2mqtt.app import counter

    result = await counter(device_context)
    assert result["impulses"] == 0
```

### Unit Tests: Valve

```python title="tests/unit/test_valve.py"
"""Unit tests for the valve command device.

Test Techniques Used:
- Decision Table: Command × current state → new state.
- Error Guessing: Invalid command handling.
"""

import pytest


@pytest.mark.asyncio
async def test_valve_open_command(device_context, mock_mqtt):
    """'open' command publishes state 'open'."""
    state = {"current": "closed"}

    @device_context.on_command
    async def handle(topic: str, payload: str) -> None:
        state["current"] = payload
        await device_context.publish_state({"state": payload})

    await handle("gas2mqtt/valve/set", "open")

    assert state["current"] == "open"
    messages = mock_mqtt.get_messages_for("test/test_device/state")
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_valve_rejects_invalid_command(device_context):
    """Unknown commands raise ValueError."""

    @device_context.on_command
    async def handle(topic: str, payload: str) -> None:
        valid = {"open", "close", "toggle"}
        if payload not in valid:
            raise ValueError(f"Unknown command: {payload!r}")

    with pytest.raises(ValueError, match="Unknown command"):
        await handle("gas2mqtt/valve/set", "blink")
```

### Unit Tests: Error Types

```python title="tests/unit/test_errors.py"
"""Unit tests for gas2mqtt error type map.

Test Techniques Used:
- Decision Table: Exception class → error_type string mapping.
"""

from cosalette import build_error_payload
from gas2mqtt.errors import (
    InvalidReadingError,
    SensorTimeoutError,
    error_type_map,
)


def test_sensor_timeout_maps_correctly():
    """SensorTimeoutError → 'sensor_timeout'."""
    payload = build_error_payload(
        SensorTimeoutError("timed out"),
        error_type_map=error_type_map,
        device="counter",
    )
    assert payload.error_type == "sensor_timeout"


def test_invalid_reading_maps_correctly():
    """InvalidReadingError → 'invalid_reading'."""
    payload = build_error_payload(
        InvalidReadingError("bad value"),
        error_type_map=error_type_map,
        device="counter",
    )
    assert payload.error_type == "invalid_reading"


def test_unmapped_exception_falls_back_to_error():
    """Unmapped exceptions get default 'error' type."""
    payload = build_error_payload(
        RuntimeError("unexpected"),
        error_type_map=error_type_map,
    )
    assert payload.error_type == "error"
```

### Integration Test: Full Lifecycle

```python title="tests/integration/test_app.py"
"""Integration tests for the gas2mqtt application.

Test Techniques Used:
- State Transition Testing: Full app lifecycle.
"""

import asyncio

import pytest
import cosalette
from cosalette.testing import AppHarness

from gas2mqtt.adapters import FakeGasMeter
from gas2mqtt.ports import GasMeterPort


@pytest.mark.asyncio
async def test_full_lifecycle_publishes_telemetry():
    """Full app lifecycle: startup → telemetry → shutdown."""
    # Arrange
    harness = AppHarness.create(name="gas2mqtt")
    harness.app.adapter(GasMeterPort, FakeGasMeter)

    @harness.app.telemetry("counter", interval=1)
    async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
        meter = ctx.adapter(GasMeterPort)
        return {"impulses": meter.read_impulses()}

    # Act — auto-shutdown after brief run
    async def shutdown_after_delay():
        await asyncio.sleep(0.1)
        harness.trigger_shutdown()

    asyncio.create_task(shutdown_after_delay())
    await harness.run()

    # Assert
    messages = harness.mqtt.get_messages_for("gas2mqtt/counter/state")
    assert len(messages) >= 1


@pytest.mark.asyncio
async def test_valve_command_publishes_state():
    """Valve device publishes initial state on startup."""
    # Arrange
    harness = AppHarness.create(name="gas2mqtt")

    @harness.app.device("valve")
    async def valve(ctx: cosalette.DeviceContext) -> None:
        @ctx.on_command
        async def handle(topic: str, payload: str) -> None:
            await ctx.publish_state({"state": payload})

        await ctx.publish_state({"state": "closed"})
        while not ctx.shutdown_requested:
            await ctx.sleep(30)

    # Act
    async def shutdown_after_delay():
        await asyncio.sleep(0.1)
        harness.trigger_shutdown()

    asyncio.create_task(shutdown_after_delay())
    await harness.run()

    # Assert
    messages = harness.mqtt.get_messages_for("gas2mqtt/valve/state")
    assert len(messages) >= 1
    assert '"closed"' in messages[0][0]
```

## 12. Running the Application

### With a `.env` File

```bash title=".env"
# MQTT broker
GAS2MQTT_MQTT__HOST=broker.local
GAS2MQTT_MQTT__PORT=1883
GAS2MQTT_MQTT__USERNAME=gas2mqtt
GAS2MQTT_MQTT__PASSWORD=s3cret

# Logging
GAS2MQTT_LOGGING__LEVEL=INFO
GAS2MQTT_LOGGING__FORMAT=json

# App settings
GAS2MQTT_SERIAL_PORT=/dev/ttyUSB0
GAS2MQTT_BAUD_RATE=9600
GAS2MQTT_COUNTER_INTERVAL=60
```

### Production

```bash
# Run normally
uv run gas2mqtt

# Override log level
uv run gas2mqtt --log-level DEBUG --log-format text

# Use a custom .env file
uv run gas2mqtt --env-file /etc/gas2mqtt/.env
```

### Dry-Run Mode

```bash
# Uses FakeGasMeter instead of SerialGasMeter
uv run gas2mqtt --dry-run
```

Dry-run mode resolves `FakeGasMeter` for `GasMeterPort`, so the app runs without
hardware. This is useful for development, CI testing, and demo setups.

### Docker Deployment

```dockerfile title="Dockerfile"
FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv==0.6.6 && uv sync --frozen

COPY src/ src/
COPY .env .env

CMD ["uv", "run", "gas2mqtt"]
```

```yaml title="docker-compose.yml"
services:
  gas2mqtt:
    build: .
    restart: unless-stopped
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0  # Pass through serial device
    environment:
      GAS2MQTT_MQTT__HOST: mosquitto
      GAS2MQTT_LOGGING__FORMAT: json
    depends_on:
      - mosquitto

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - mosquitto-data:/mosquitto/data
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf

volumes:
  mosquitto-data:
```

!!! tip "Production checklist"

    - [ ] Use `json` log format for container log aggregators
    - [ ] Set `QoS 1` (default) for at-least-once delivery
    - [ ] Configure MQTT authentication
    - [ ] Mount the serial device into the container
    - [ ] Set `restart: unless-stopped` for daemon resilience
    - [ ] Monitor the `gas2mqtt/status` topic for LWT availability

## Summary

Here's what each piece does and how they connect:

```text
┌────────────────────────────────────────────────┐
│                  gas2mqtt App                   │
├───────────────┬────────────────────────────────┤
│ Settings      │ Gas2MqttSettings               │
│               │   serial_port, baud_rate, etc.  │
├───────────────┼────────────────────────────────┤
│ Port          │ GasMeterPort (Protocol)         │
├───────────────┼────────────────────────────────┤
│ Adapters      │ SerialGasMeter (real)           │
│               │ FakeGasMeter   (dry-run)        │
├───────────────┼────────────────────────────────┤
│ Hooks         │ init_serial   (on_startup)      │
│               │ close_serial  (on_shutdown)      │
├───────────────┼────────────────────────────────┤
│ Devices       │ counter  (telemetry, 60s)       │
│               │ valve    (command, open/close)   │
├───────────────┼────────────────────────────────┤
│ Error Types   │ SensorTimeoutError              │
│               │ InvalidReadingError              │
│               │ ConnectionLostError              │
├───────────────┼────────────────────────────────┤
│ MQTT Topics   │ gas2mqtt/counter/state          │
│               │ gas2mqtt/valve/state             │
│               │ gas2mqtt/valve/set               │
│               │ gas2mqtt/error                   │
│               │ gas2mqtt/{device}/error           │
│               │ gas2mqtt/status                  │
└───────────────┴────────────────────────────────┘
```

---

## See Also

- [Telemetry Device](telemetry-device.md) — deep dive into `@app.telemetry`
- [Command & Control Device](command-device.md) — deep dive into `@app.device`
- [Configuration](configuration.md) — settings, `.env`, CLI overrides
- [Hardware Adapters](adapters.md) — ports, adapters, dry-run
- [Lifecycle Hooks](lifecycle-hooks.md) — startup/shutdown hooks
- [Testing](testing.md) — pytest plugin, AppHarness, test doubles
- [Custom Error Types](error-types.md) — error classification
- [Architecture](../concepts/architecture.md) — framework architecture overview
