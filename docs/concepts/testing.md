---
icon: material/test-tube
---

# Testing Strategy

Cosalette provides a **three-layer testing strategy** with purpose-built test
doubles and a pytest plugin — making it straightforward to test device code
without a real MQTT broker or hardware.

## Three Layers

| Layer           | What you test                | Dependencies                    | Speed   |
|-----------------|------------------------------|---------------------------------|---------|
| **Domain**      | Pure business logic          | None — no cosalette imports      | Fastest |
| **Device**      | Device functions + MQTT flow | `cosalette.testing` fixtures     | Fast    |
| **Integration** | Full app lifecycle           | `AppHarness` (all test doubles) | Fast    |

### Sociable Over Isolated

Cosalette follows the **sociable unit test** philosophy: test collaborating
objects together rather than mocking every boundary. Device tests use real
`DeviceContext` with a `MockMqttClient` — testing the actual publish flow,
not a mock of it.

!!! info "Why sociable?"
    Isolated unit tests that mock every dependency tend to test the mocking
    framework, not the code. Sociable tests catch integration issues early
    while remaining fast because the test doubles are lightweight in-memory
    implementations.

## Test Doubles

Cosalette ships four test doubles, each targeting a different boundary:

| Double            | Boundary      | Why it exists                                              |
|-------------------|---------------|------------------------------------------------------------|
| `MockMqttClient`  | MQTT broker   | Records all publishes/subscribes so tests can assert on them |
| `FakeClock`       | System time   | Deterministic uptime and strategy timing without real delays |
| `NullMqttClient`  | MQTT broker   | Silent no-op for cases where MQTT output is irrelevant     |
| `make_settings()` | Configuration | Strips env/dotenv sources so settings are reproducible     |

The doubles satisfy the same Protocol interfaces as their production
counterparts. Production code and test code share the same function
signatures — no conditional logic and no separate test paths.

!!! warning "`FakeClock` proves what happened, not what didn't"
    `FakeClock.sleep()` advances virtual time with no real delay, so it
    completes in a single event-loop iteration and wins any race against a
    real `asyncio.Event` — regardless of the duration requested. That makes
    it a sound instrument for what *did* happen and an unsound one for what
    did *not*: a test cannot prove that a scheduled tick was absent, and an
    exact publish count only measures how many event-loop yields the test
    happened to burn. Discriminate a trigger-initiated run from a scheduled
    tick with `TriggerPayload.is_triggered`.
    [ADR-071](../adr/ADR-071-test-clock-doubles-for-tick-and-throttle-timing-assertions.md)
    records the direction for closing this gap.

See the [Testing Utilities reference](../reference/testing.md) for full API
docs, and the [Test Your Application guide](../guides/testing.md) for usage
recipes including `MockMqttClient` failure injection, `FakeClock` time
control, and harness assembly patterns.

## AppHarness

The `AppHarness` is the highest-level test utility. It pre-wires an `App`
with `MockMqttClient`, `FakeClock`, and isolated `Settings`, then exposes
a single `run()` method that exercises the complete `_run_async()` lifecycle.
Integration tests use it as a one-liner entry point rather than assembling
doubles manually.

See the [Test Your Application guide](../guides/testing.md) for usage recipes
and the [Testing Utilities reference](../reference/testing.md) for the full API.

## Test Seams in `_run_async()`

The framework's `_run_async()` method accepts optional parameters specifically
designed as injection points: `settings`, `shutdown_event`, `mqtt`, and `clock`.
When a parameter is `None`, the real implementation is used; when provided, the
double replaces it for that run only.

This design means:

- **No test flag or mode switch** — production and test code call the same method.
- **Opt-in granularity** — inject only the doubles you need; leave the rest real.
- **AppHarness assembles them automatically** — the direct injection pattern
  exists for advanced isolation tests that need finer control.

See [Testing Utilities reference](../reference/testing.md#test-seams) for the
parameter table.

---

## See Also

- [Test Your Application](../guides/testing.md) — per-archetype usage recipes
- [Testing Utilities](../reference/testing.md) — API reference for all test doubles
- [Architecture](architecture.md) — test seams in the composition root
- [Hexagonal Architecture](hexagonal.md) — Protocol-based ports and test doubles
- [ADR-007 — Testing Strategy](../adr/ADR-007-testing-strategy.md)
