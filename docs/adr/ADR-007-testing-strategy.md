# ADR-007: Testing Strategy

## Status

Accepted **Date:** 2026-02-14

## Context

The cosalette framework will be consumed by 8+ IoT-to-MQTT bridge projects. Each project
needs consistent test patterns for:

- **Domain tests:** Pure logic (no framework imports, no I/O)
- **Device tests:** Framework-managed device functions with mock MQTT, fake clocks
- **Integration tests:** Full app lifecycle (startup → MQTT → device → shutdown)

The velux2mqtt reference project demonstrates the challenge: testing a device function
requires mocking the MQTT client, injecting a fake clock, and providing test
configuration. Without framework-provided test utilities, each project must reimplement
these test doubles independently — leading to inconsistent patterns and duplicated
fixture code.

The framework user explicitly requires test fixture factories (`make_mqtt_mock()`,
`make_clock_fake()`), a standard test harness for integration tests, and a pytest plugin
with shared fixtures.

## Decision

Use a **`cosalette.testing` module with a pytest plugin**, **sociable unit tests**, and
**pure domain testing** because this provides consistent test infrastructure across all
projects while keeping domain tests free of framework dependencies.

### `cosalette.testing` module

The framework ships a `testing` subpackage (optional dependency group) with:

| Component        | Type              | Purpose                                    |
| ---------------- | ----------------- | ------------------------------------------ |
| `MockMqttClient` | Test double       | Records publish/subscribe calls            |
| `FakeClock`      | Test double       | Deterministic time control                 |
| `make_settings`  | Factory function  | Create test settings with sensible defaults |
| `AppHarness`     | Integration tool  | Full app lifecycle in tests                |
| `DeviceContext`  | Isolated context  | Test a single device in isolation          |

### pytest plugin

Projects activate the plugin with a single line:

```python
# conftest.py
pytest_plugins = ["cosalette.testing"]
```

This auto-registers fixtures: `mock_mqtt`, `fake_clock`, `make_settings`,
`app_harness`, `device_context`.

### Testing layers

1. **Domain tests** — never import cosalette. Test pure functions and dataclasses:
   ```python
   from velux2mqtt.domain.commands import parse_command, Up
   def test_parse_up():
       assert isinstance(parse_command("UP"), Up)
   ```

2. **Device tests** — use `cosalette.testing` fixtures for sociable unit tests that
   exercise device functions with mock infrastructure.

3. **Integration tests** — use `AppHarness` to spin up the full app, send MQTT
   messages, and assert published state.

## Decision Drivers

- Consistent test patterns across 8+ projects without reimplementing test doubles
- Domain tests must be independent of the framework (hexagonal purity)
- pytest is the standard test runner (already in use)
- Sociable unit tests (test collaborating objects together) over isolated mocks —
  more realistic, less brittle
- Integration tests must be easy to write and deterministic

## Considered Options

### Option 1: Fixtures only (no module)

Provide example fixture code in documentation; each project copies and adapts.

- *Advantages:* No framework test code to maintain. Projects have full control.
- *Disadvantages:* Fixtures drift across projects. Bug fixes must be applied in 8+
  places. No standardisation of test patterns. Violates the framework's "batteries
  included" principle.

### Option 2: Test base classes

Provide `TestCase`-style base classes that projects inherit from.

- *Advantages:* Familiar pattern (JUnit, Django TestCase).
- *Disadvantages:* Couples test structure to inheritance hierarchy. Does not work
  well with pytest's fixture model. Forces a specific test organisation. Modern
  Python testing has moved away from base classes toward composition.

### Option 3: Separate test package

Ship test utilities as a separate PyPI package (`cosalette-testing`).

- *Advantages:* Clear separation of concerns. Projects can pin test utils independently.
- *Disadvantages:* Over-engineered for the scope. Requires coordinated releases between
  two packages. The test utilities are tightly coupled to the framework's internals —
  splitting them adds complexity without meaningful independence.

### Option 4: `cosalette.testing` module with pytest plugin (chosen)

Ship test utilities as a submodule with optional dependencies, activated via
pytest plugin registration.

- *Advantages:* Single package, single version. Pytest plugin auto-registers fixtures.
  Factory functions and test doubles are framework-maintained — bug fixes propagate to
  all projects automatically. Optional dependency group (`pip install cosalette[testing]`)
  keeps production installs lean.
- *Disadvantages:* Test utilities are coupled to framework internals — internal
  refactoring may require updating the testing module. The testing module adds
  maintenance scope to the framework.

## Consequences

### Positive

- All 8+ projects share identical test infrastructure — fixture factories, test doubles,
  and integration harness
- Domain tests remain pure Python with zero framework imports — the hexagonal boundary
  is preserved in tests
- pytest plugin activation is a single line in `conftest.py`
- Bug fixes in test doubles (e.g., `MockMqttClient`) propagate to all projects via
  framework updates
- `AppHarness` enables deterministic integration tests without real MQTT brokers

### Negative

- The `cosalette.testing` module adds maintenance scope — it must evolve alongside
  the framework's internal APIs
- Sociable unit tests may be harder to debug than fully isolated mocks when failures
  cross component boundaries
- Projects that need highly custom test setups may outgrow the provided fixtures

_2026-02-14_
