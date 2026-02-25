# Testing Utilities

Reference for the `cosalette.testing` package — test doubles, factories, and
pytest fixtures for testing cosalette applications.

## Test Harness

::: cosalette.testing.AppHarness

## Clock

::: cosalette.testing.FakeClock

## MQTT Test Doubles

::: cosalette.testing.MockMqttClient

::: cosalette.testing.NullMqttClient

## Settings Factory

::: cosalette.testing.make_settings

## Pytest Fixtures

The `cosalette.testing` package registers a
[pytest plugin](https://docs.pytest.org/en/stable/how-to/writing_plugins.html#making-your-plugin-installable-by-others)
via the `pytest11` entry point. The fixtures below are available
automatically when `cosalette` is installed:

| Fixture | Type | Description |
|---------|------|-------------|
| `mock_mqtt` | `MockMqttClient` | In-memory MQTT client for capturing published messages |
| `fake_clock` | `FakeClock` | Deterministic clock starting at `0.0` |
| `device_context` | `DeviceContext` | Pre-wired context with `mock_mqtt` and `fake_clock` |

All fixtures are function-scoped. Import them by name — no explicit
import needed.

## MemoryStore

::: cosalette.MemoryStore

`MemoryStore` is the recommended test double for persistence. It stores
data in an in-memory dictionary, avoiding filesystem access in tests.

```python
from cosalette import MemoryStore
from cosalette.testing import AppHarness

backend = MemoryStore()
harness = AppHarness.create(store=backend)

# Pre-seed data
backend.save("sensor", {"count": 99})

# After test, inspect stored data
assert backend.load("sensor") == {"count": 99}
```
