---
icon: material/test-tube
---

# Test Your Application

cosalette ships with a testing module designed for fast, deterministic tests without
a real MQTT broker or hardware. This guide covers the three test layers, the pytest
plugin, and practical patterns for testing telemetry and command devices.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## Setup: Pytest Plugin

Register the cosalette pytest plugin in your `conftest.py`:

```python title="tests/conftest.py"
pytest_plugins = ["cosalette.testing._plugin"]  # (1)!
```

1. This is `cosalette.testing._plugin` — **not** `cosalette.testing`. The plugin
   module path includes the leading underscore.

This registers three fixtures automatically:

| Fixture          | Type              | Description                           |
| ---------------- | ----------------- | ------------------------------------- |
| `mock_mqtt`      | `MockMqttClient`  | In-memory MQTT double                 |
| `fake_clock`     | `FakeClock`       | Deterministic clock starting at 0     |
| `device_context` | `DeviceContext`    | Pre-wired context with test doubles   |

## Three Test Layers

cosalette encourages a layered testing approach
([ADR-007](../adr/ADR-007-testing-strategy.md)):

| Layer           | What to test                | Fixtures                      | Speed      |
| --------------- | --------------------------- | ----------------------------- | ---------- |
| **Domain**      | Pure logic, no framework    | None (plain pytest)           | Fastest    |
| **Device**      | Device functions in isolation | `device_context`             | Fast       |
| **Integration** | Full app with `AppHarness`  | `AppHarness.create()`         | Moderate   |

### Layer 1: Domain Tests

Test pure business logic without any framework involvement:

```python title="tests/unit/test_domain.py"
"""Domain tests for gas meter reading logic.

Test Techniques Used:
- Boundary Value Analysis: Edge cases for impulse count validation.
- Equivalence Partitioning: Valid vs invalid reading ranges.
"""


def test_validate_impulse_count_rejects_negative():
    """Negative impulse counts are invalid."""
    # Arrange
    count = -1

    # Act & Assert
    assert count < 0  # Your domain validation logic here
```

### Layer 2: Device Tests

Test device functions using the `device_context` fixture:

```python title="tests/unit/test_telemetry.py"
"""Device-layer tests for the counter telemetry device.

Test Techniques Used:
- Specification-based: Verify the return-dict contract.
- Error Guessing: Adapter failure during read.
"""

import pytest


@pytest.mark.asyncio
async def test_counter_returns_impulse_dict(device_context):
    """Counter device returns dict with impulse count."""
    # Arrange — register a fake adapter on the context
    from gas2mqtt.ports import GasMeterPort

    class StubMeter:
        def read_impulses(self) -> int:
            return 42

        def read_temperature(self) -> float:
            return 21.5

    device_context._adapters[GasMeterPort] = StubMeter()

    # Act — call the telemetry function directly
    from gas2mqtt.app import counter

    result = await counter(device_context)

    # Assert
    assert result == {"impulses": 42, "temperature_celsius": 21.5, "unit": "m³"}
```

### Layer 3: Integration Tests

Test the full application lifecycle with `AppHarness`:

```python title="tests/integration/test_app.py"
"""Integration tests for the gas2mqtt application.

Test Techniques Used:
- State Transition Testing: App lifecycle (startup → running → shutdown).
"""

import asyncio

import pytest
from cosalette.testing import AppHarness


@pytest.mark.asyncio
async def test_telemetry_publishes_state():
    """Full app lifecycle publishes at least one telemetry reading."""
    # Arrange
    harness = AppHarness.create(name="gas2mqtt")

    @harness.app.telemetry("counter", interval=1)
    async def counter(ctx):
        return {"impulses": 42}

    # Act — schedule shutdown after a brief delay
    async def shutdown_after_delay():
        await asyncio.sleep(0.1)
        harness.trigger_shutdown()

    asyncio.create_task(shutdown_after_delay())
    await harness.run()

    # Assert
    state_messages = harness.mqtt.get_messages_for("gas2mqtt/counter/state")
    assert len(state_messages) >= 1
    assert '"impulses": 42' in state_messages[0][0]  # (1)!
```

1. `get_messages_for()` returns `(payload, retain, qos)` tuples.

## MockMqttClient

`MockMqttClient` is an in-memory test double that records all MQTT interactions:

```python title="tests/unit/test_publish.py"
import pytest
from cosalette.testing import MockMqttClient


@pytest.mark.asyncio
async def test_publish_records_message():
    """MockMqttClient records published messages."""
    mqtt = MockMqttClient()

    await mqtt.publish("test/topic", '{"value": 1}', retain=True, qos=1)

    assert mqtt.publish_count == 1
    assert mqtt.published[0] == ("test/topic", '{"value": 1}', True, 1)
```

### Key Properties and Methods

| Member                         | Description                                     |
| ------------------------------ | ----------------------------------------------- |
| `published`                    | List of `(topic, payload, retain, qos)` tuples  |
| `subscriptions`                | List of subscribed topic strings                 |
| `publish_count`                | Number of published messages                     |
| `subscribe_count`              | Number of subscriptions                          |
| `get_messages_for(topic)`      | Filter published messages by topic               |
| `deliver(topic, payload)`      | Simulate an inbound MQTT message                 |
| `raise_on_publish`             | Set to an exception to inject publish failures   |
| `reset()`                      | Clear all recorded data                          |

### Simulating Inbound Commands

Use `deliver()` to simulate MQTT messages arriving from external publishers:

```python title="tests/unit/test_commands.py"
@pytest.mark.asyncio
async def test_valve_responds_to_open_command(device_context, mock_mqtt):
    """Valve device processes 'open' command and publishes state."""
    # Arrange
    state = {"current": "closed"}

    @device_context.on_command
    async def handle(topic: str, payload: str) -> None:
        state["current"] = payload
        await device_context.publish_state({"state": payload})

    # Act — simulate an inbound command
    await handle("gas2mqtt/valve/set", "open")

    # Assert
    assert state["current"] == "open"
    messages = mock_mqtt.get_messages_for("test/test_device/state")
    assert len(messages) == 1
```

### Error Injection

Test error handling by setting `raise_on_publish`:

```python title="tests/unit/test_errors.py"
@pytest.mark.asyncio
async def test_publish_failure_is_handled(mock_mqtt):
    """MockMqttClient can simulate publish failures."""
    mock_mqtt.raise_on_publish = ConnectionError("Broker down")

    with pytest.raises(ConnectionError, match="Broker down"):
        await mock_mqtt.publish("test/topic", "payload")
```

## FakeClock

`FakeClock` provides deterministic time control:

```python title="tests/unit/test_timing.py"
from cosalette.testing import FakeClock


def test_fake_clock_returns_set_time():
    """FakeClock returns manually controlled time values."""
    clock = FakeClock(0.0)

    assert clock.now() == 0.0

    clock._time = 42.0
    assert clock.now() == 42.0
```

Use it to test time-dependent logic without real delays.

## AppHarness

`AppHarness` wraps the entire framework with test doubles for integration testing:

```python title="tests/integration/test_harness.py"
from cosalette.testing import AppHarness


def test_harness_creates_fresh_doubles():
    """AppHarness.create() provides wired test doubles."""
    harness = AppHarness.create(name="gas2mqtt")

    assert harness.app is not None
    assert harness.mqtt is not None
    assert harness.clock is not None
    assert harness.settings is not None
    assert harness.shutdown_event is not None
```

### AppHarness.create() Parameters

| Parameter              | Default      | Description                           |
| ---------------------- | ------------ | ------------------------------------- |
| `name`                 | `"testapp"`  | App name (used as MQTT topic prefix)  |
| `version`              | `"1.0.0"`    | App version                           |
| `dry_run`              | `False`      | Use dry-run adapter variants          |
| `**settings_overrides` | —            | Forwarded to `make_settings()`        |

### Typical Integration Test Pattern

```python title="tests/integration/test_full_lifecycle.py"
import asyncio

import pytest
from cosalette.testing import AppHarness
import cosalette


@pytest.mark.asyncio
async def test_full_app_lifecycle():
    """End-to-end test: register devices, run, verify MQTT output."""
    # Arrange
    harness = AppHarness.create(name="gas2mqtt")

    @harness.app.telemetry("counter", interval=1)
    async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
        return {"impulses": 99}

    @harness.app.device("valve")
    async def valve(ctx: cosalette.DeviceContext) -> None:
        @ctx.on_command
        async def handle(topic: str, payload: str) -> None:
            await ctx.publish_state({"state": payload})

        await ctx.publish_state({"state": "closed"})
        while not ctx.shutdown_requested:
            await ctx.sleep(30)

    # Act
    async def run_briefly():
        await asyncio.sleep(0.1)
        harness.trigger_shutdown()

    asyncio.create_task(run_briefly())
    await harness.run()

    # Assert — telemetry published
    counter_msgs = harness.mqtt.get_messages_for("gas2mqtt/counter/state")
    assert len(counter_msgs) >= 1

    # Assert — device published initial state
    valve_msgs = harness.mqtt.get_messages_for("gas2mqtt/valve/state")
    assert len(valve_msgs) >= 1
    assert '"closed"' in valve_msgs[0][0]
```

## make_settings()

`make_settings()` creates `Settings` instances isolated from environment variables
and `.env` files:

```python title="tests/conftest.py"
from cosalette.testing import make_settings


def test_make_settings_defaults():
    """make_settings produces isolated defaults."""
    settings = make_settings()

    assert settings.mqtt.host == "localhost"
    assert settings.mqtt.port == 1883
    assert settings.logging.level == "INFO"
```

Override nested fields by passing model instances:

```python title="tests/unit/test_settings.py"
from cosalette._settings import MqttSettings
from cosalette.testing import make_settings


def test_make_settings_with_overrides():
    """make_settings accepts keyword overrides."""
    settings = make_settings(mqtt=MqttSettings(host="broker.test", port=8883))

    assert settings.mqtt.host == "broker.test"
    assert settings.mqtt.port == 8883
```

## Testing Telemetry Devices

The recommended pattern for testing telemetry functions:

```python title="tests/unit/test_counter.py"
"""Unit tests for the counter telemetry device.

Test Techniques Used:
- Specification-based: Return-dict contract verification.
- Error Guessing: Hardware failure during read.
"""

import pytest


class StubGasMeter:
    """Stub adapter for testing."""

    def __init__(self, impulses: int = 42, temperature: float = 21.5) -> None:
        self.impulses = impulses
        self.temperature = temperature

    def read_impulses(self) -> int:
        return self.impulses

    def read_temperature(self) -> float:
        return self.temperature


@pytest.mark.asyncio
async def test_counter_returns_expected_dict(device_context):
    """Counter returns dict with impulses, temperature, and unit."""
    from gas2mqtt.ports import GasMeterPort

    device_context._adapters[GasMeterPort] = StubGasMeter(impulses=100)

    from gas2mqtt.app import counter

    result = await counter(device_context)

    assert result["impulses"] == 100
    assert "unit" in result


@pytest.mark.asyncio
async def test_counter_propagates_adapter_error(device_context):
    """Hardware failure in adapter raises (framework catches in production)."""
    from gas2mqtt.ports import GasMeterPort

    class FailingMeter:
        def read_impulses(self) -> int:
            raise OSError("Serial timeout")

        def read_temperature(self) -> float:
            return 0.0

    device_context._adapters[GasMeterPort] = FailingMeter()

    from gas2mqtt.app import counter

    with pytest.raises(OSError, match="Serial timeout"):
        await counter(device_context)
```

## Testing Command Devices

Test command handlers by calling them directly:

```python title="tests/unit/test_valve.py"
"""Unit tests for the valve command device.

Test Techniques Used:
- Decision Table: Command × current state → new state.
- Error Guessing: Invalid command handling.
"""

import pytest


@pytest.mark.asyncio
async def test_valve_open_command(device_context, mock_mqtt):
    """'open' command sets valve state to open."""
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
async def test_valve_rejects_unknown_command(device_context):
    """Unknown commands raise ValueError."""

    @device_context.on_command
    async def handle(topic: str, payload: str) -> None:
        valid = {"open", "close", "toggle"}
        if payload not in valid:
            raise ValueError(f"Unknown command: {payload!r}")

    with pytest.raises(ValueError, match="Unknown command"):
        await handle("gas2mqtt/valve/set", "blink")
```

## Testing Adapters

Test adapter registration and resolution:

```python title="tests/unit/test_adapters.py"
"""Unit tests for adapter registration."""

import pytest
from cosalette.testing import AppHarness

from typing import Protocol, runtime_checkable


@runtime_checkable
class SamplePort(Protocol):
    def do_thing(self) -> str: ...


class RealAdapter:
    def do_thing(self) -> str:
        return "real"


class FakeAdapter:
    def do_thing(self) -> str:
        return "fake"


def test_adapter_resolves_real_by_default():
    """Normal mode resolves the real adapter."""
    harness = AppHarness.create(name="gas2mqtt")
    harness.app.adapter(SamplePort, RealAdapter, dry_run=FakeAdapter)

    resolved = harness.app._resolve_adapters()

    assert isinstance(resolved[SamplePort], RealAdapter)


def test_adapter_resolves_fake_in_dry_run():
    """Dry-run mode resolves the dry-run adapter."""
    harness = AppHarness.create(name="gas2mqtt", dry_run=True)
    harness.app.adapter(SamplePort, RealAdapter, dry_run=FakeAdapter)

    resolved = harness.app._resolve_adapters()

    assert isinstance(resolved[SamplePort], FakeAdapter)
```

## Testing Publish Strategies

Publish strategies are plain objects that you can test directly — no full app
or MQTT broker needed.

### Testing OnChange Thresholds

```python title="tests/unit/test_strategies.py"
from cosalette import OnChange


def test_onchange_suppresses_small_delta():
    """Small temperature change within threshold is suppressed."""
    strategy = OnChange(threshold=0.5)
    current = {"celsius": 20.3}
    previous = {"celsius": 20.0}

    assert strategy.should_publish(current, previous) is False


def test_onchange_publishes_large_delta():
    """Temperature change exceeding threshold triggers publish."""
    strategy = OnChange(threshold=0.5)
    current = {"celsius": 21.0}
    previous = {"celsius": 20.0}

    assert strategy.should_publish(current, previous) is True
```

### Testing Every with FakeClock

`Every(seconds=N)` uses a `ClockPort` for time tracking. Bind a `FakeClock`
to control time deterministically:

```python title="tests/unit/test_strategies.py"
from cosalette import Every
from cosalette.testing import FakeClock


def test_every_seconds_respects_elapsed_time():
    """Every(seconds=N) publishes only after N seconds elapse."""
    clock = FakeClock(0.0)
    strategy = Every(seconds=60)
    strategy._bind(clock)  # (1)!

    payload = {"value": 1}

    # Less than 60s elapsed — suppressed
    clock._time = 30.0
    assert strategy.should_publish(payload, payload) is False

    # 60s elapsed — publishes
    clock._time = 61.0
    assert strategy.should_publish(payload, payload) is True
    strategy.on_published()

    # Clock reset — less than 60s since last publish
    clock._time = 90.0
    assert strategy.should_publish(payload, payload) is False
```

1. `_bind()` is called automatically by the framework. In tests, call it
   manually to inject the `FakeClock`. Note: first-publish logic
   (`previous is None`) lives in the framework loop, not in the strategy
   itself — see [Under the hood](telemetry-device.md#how-telemetry-works).

### Testing Nested Threshold with Dot-Notation

```python title="tests/unit/test_strategies.py"
from cosalette import OnChange


def test_per_field_threshold_with_nested_payload():
    """Per-field thresholds use dot-notation for nested keys."""
    strategy = OnChange(threshold={"sensor.temp": 0.5})
    current = {"sensor": {"temp": 21.0, "humidity": 55}}
    previous = {"sensor": {"temp": 20.0, "humidity": 55}}

    # temp delta 1.0 > 0.5 → publish
    assert strategy.should_publish(current, previous) is True

    # temp delta 0.1 ≤ 0.5 → suppress
    small_change = {"sensor": {"temp": 20.1, "humidity": 55}}
    assert strategy.should_publish(small_change, previous) is False
```

---

## See Also

- [Testing](../concepts/testing.md) — conceptual overview of the testing strategy
- [ADR-007](../adr/ADR-007-testing-strategy.md) — testing strategy decisions
