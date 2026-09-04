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

## Clocks

Two clock doubles ship, as siblings rather than as a subclass pair — their
`sleep()` contracts are deliberately incompatible.

| Double | `sleep()` | Reach for it when |
|--------|-----------|-------------------|
| `FakeClock` | Self-completes in one event-loop iteration, advancing virtual time | The test only needs virtual elapsed time |
| `ManualClock` | Blocks on a per-sleeper deadline until `advance()` releases it | The test asserts *absence* — that no scheduled tick fired — or an exact publish count |

::: cosalette.testing.FakeClock
    options:
      inherited_members: true

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
    *absolutely*. When the assertion is about a tick that must *not* fire,
    use `ManualClock` instead.

::: cosalette.testing.ManualClock
    options:
      inherited_members: true

### Quiescence contract

`ManualClock.settle()` and `ManualClock.advance()` share one heuristic, and
it is the part of the double worth understanding before relying on it.

`settle()` yields to the event loop one round at a time and, after each
round, compares three observations: the set of pending asyncio tasks, the
pending sleep deadlines registered on the clock, and a counter of sleep
registrations and releases. Quiescence is declared only after **three
consecutive** rounds change none of them — one quiet round is not enough,
because a callback chain being handed along internally by `asyncio.wait`
passes through rounds where none of the three quantities moves. `settle()`
then returns **without moving virtual time**. Only `advance()` moves time.

`advance(seconds)` steps virtual time deadline by deadline rather than
jumping straight to the target, so a sleeper due at `t+1` inside
`advance(10)` reads `now() == t+1`. After each release it calls `settle()`
before moving time again, and once more when time reaches the target — so on
return, every task the advance woke has run as far as it can.

!!! warning "The heuristic has two edges"
    asyncio exposes no supported loop-idle hook, and this deliberately does
    not read the loop's private ready queue
    ([ADR-071](../adr/ADR-071-test-clock-doubles-for-tick-and-throttle-timing-assertions.md)).
    So:

    - A task that spins on `asyncio.sleep(0)` without touching the clock and
      without starting or finishing tasks is invisible to the observation
      and can be reported as quiescent.
    - A task that churns any of the three observed quantities forever is
      caught by the retry bound and raises `RuntimeError` — loudly, rather
      than returning as if all were well. Raise the bound with
      `settle(max_rounds=...)` or `advance(..., max_wakes=...)` when a test
      legitimately needs more rounds.

```python
from cosalette.testing import ManualClock

clock = ManualClock()
fired: list[float] = []


async def tick() -> None:
    await clock.sleep(3600)
    fired.append(clock.now())


task = asyncio.create_task(tick())

await clock.settle()
assert fired == []          # a real negative assertion — time did not move

await clock.advance(3600)
assert fired == [3600.0]
await task
```

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
