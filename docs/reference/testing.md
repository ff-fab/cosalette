# Testing Utilities

Reference for the `cosalette.testing` package — test doubles, factories, and
pytest fixtures for testing cosalette applications.

## Test Harness

::: cosalette.testing.AppHarness

### Quick Examples

```python
harness = AppHarness.create(name="myapp")
# ... run harness ...

# Assert a retained JSON message is a superset of expected
harness.assert_state("myapp/sensor/state", {"value": 42})

# Assert the app subscribed to a topic
harness.assert_subscribed("myapp/sensor/set")

# Inject a command with a dict payload (auto-serialized to JSON)
await harness.inject_command("sensor", {"threshold": 10})
```

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

!!! tip "Default store in tests"
    Since ADR-049, omitting `store=` from `App(...)` auto-resolves a
    `JsonFileStore` at an XDG-derived path. In tests, always pass an
    explicit store to keep tests hermetic:

    - `store=MemoryStore()` — hermetic in-memory persistence; inspect with
      `backend.load(key)`.
    - `store=None` — disable persistence entirely (no retained-topic
      cleanup).

    The test suite sandboxes `XDG_STATE_HOME` to a temp dir so the
    default resolution does not touch the developer's home directory.

```python
from cosalette import MemoryStore
from cosalette.testing import AppHarness

# Hermetic persistence — use MemoryStore()
backend = MemoryStore()
harness = AppHarness.create(store=backend)

# Pre-seed data
backend.save("sensor", {"count": 99})

# After test, inspect stored data
assert backend.load("sensor") == {"count": 99}

# No persistence at all — pass store=None
harness_no_store = AppHarness.create(store=None)
```
