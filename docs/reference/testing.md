---
icon: material/test-tube
---

# Testing Utilities

Reference for the `cosalette.testing` package — test doubles, factories, and
pytest fixtures for testing cosalette applications.

!!! tip "Looking for recipes?"
    See the [Test Your Application guide](../guides/testing.md) for per-archetype
    usage patterns, and the [Testing Strategy concept](../concepts/testing.md)
    for the design rationale.

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

!!! warning "What `FakeClock` cannot measure"
    `FakeClock.sleep()` advances virtual time with no real delay, so it
    completes in a single event-loop iteration and wins any race against a
    real `asyncio.Event` that another task has yet to set — regardless of the
    duration requested. A test
    therefore cannot use it to prove that a scheduled tick did *not* fire,
    and cannot assert an exact publish count (that count reflects how many
    event-loop yields the test happened to burn). To discriminate a
    trigger-initiated run from a scheduled tick, check
    `TriggerPayload.is_triggered`. See
    [ADR-071](../adr/ADR-071-test-clock-doubles-for-tick-and-throttle-timing-assertions.md).

    Use `clock.advance(seconds)` to move virtual time forward *relatively*
    without yielding to the event loop; assigning `clock._time` sets it
    *absolutely*.

## MQTT Test Doubles

::: cosalette.testing.MockMqttClient

::: cosalette.testing.NullMqttClient

## Settings Factory

::: cosalette.testing.make_settings

## Pytest Fixtures

The `cosalette.testing` package registers a
[pytest plugin](https://docs.pytest.org/en/stable/how-to/writing_plugins.html#making-your-plugin-installable-by-others)
via the `pytest11` entry point. The fixtures below are available automatically
when `cosalette` is installed — no `conftest.py` changes are needed.

!!! note "Manual registration"
    If entry-point plugin discovery is disabled (e.g. `-p no:cosalette`), you
    can load the plugin explicitly:

    ```python title="tests/conftest.py"
    pytest_plugins = ["cosalette.testing._plugin"]
    ```

The fixtures below are available automatically once the plugin is registered:

| Fixture | Type | Scope | Description |
|---------|------|-------|-------------|
| `mock_mqtt` | `MockMqttClient` | function | In-memory MQTT client for capturing published messages |
| `fake_clock` | `FakeClock` | function | Deterministic clock starting at `0.0` |
| `device_context` | `DeviceContext` | function | Pre-wired context with `mock_mqtt` and `fake_clock`; `name="test_device"`, `topic_prefix="test"` |

All fixtures are function-scoped — each test receives a fresh instance.

## Stream Handler Proxy

::: cosalette.testing.StreamHandlerProxy

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

## Test Seams

The `_run_async()` method accepts four optional injection parameters. When a
parameter is `None`, the real implementation is used; when provided, the double
replaces it for that run. `AppHarness.create()` assembles all four automatically.

| Parameter        | Type              | Default (when `None`)         | Purpose                                |
|------------------|-------------------|-------------------------------|----------------------------------------|
| `settings`       | `Settings`        | `Settings()` from env/dotenv  | Skip environment variable loading      |
| `shutdown_event` | `asyncio.Event`   | Internal `Event`              | Programmatic shutdown signal           |
| `mqtt`           | `MqttClientPort`  | Real MQTT client              | In-memory recording or no-op MQTT      |
| `clock`          | `ClockPort`       | `time.monotonic`-based clock  | Deterministic time for uptime/strategy |

See [Direct Injection](../guides/testing.md#direct-injection-advanced) in the
guide for a usage example.
