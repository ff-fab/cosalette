# ADR-006: Hexagonal Architecture (Ports & Adapters)

## Status

Accepted **Date:** 2026-02-14

## Context

cosalette applications interact with diverse hardware interfaces: GPIO relays
(RPi.GPIO), BLE sensors (bleak), WiFi smart bulbs (pywizlight), I²C magnetometers
(smbus2), USB serial (pyserial), and SSH (asyncssh). Domain logic — command parsing,
position estimation, trigger detection, EWMA filtering — must be testable without
any hardware present. Development machines typically lack the GPIO, I²C, and BLE
interfaces available on deployment targets (Raspberry Pi).

The velux2mqtt reference implementation already uses PEP 544 `Protocol` classes to
define port contracts (`GpioPort`, `MqttPort`, `ClockPort`) with structural subtyping,
allowing the domain layer to depend only on protocol shapes rather than concrete
implementations. This pattern needs to be formalised and embedded in the framework.

A key practical concern is **lazy imports**: hardware libraries like `RPi.GPIO` and
`smbus2` are not installable on development machines. Adapter registration must support
string-based import paths to defer importing hardware libraries until runtime on actual
hardware.

## Decision

Use **PEP 544 Protocol classes for ports**, **adapter registration via `app.adapter()`**,
and **string-based lazy imports for optional hardware** because structural subtyping
keeps the domain free of framework imports while lazy imports solve the dev-machine
portability problem.

### Ports (Protocol classes)

```python
@runtime_checkable
class GpioPort(Protocol):
    async def pulse(self, pin: int, duration: float) -> None: ...
    async def cleanup(self) -> None: ...
```

### Adapter registration

```python
# Direct class registration (when hardware lib is available)
app.adapter(GpioPort, RpiGpioAdapter, dry_run=DryRunGpioAdapter)

# String-based lazy import (when hardware lib may be absent)
app.adapter(GpioPort, "velux2mqtt.adapters.rpi_gpio:RpiGpioAdapter",
            dry_run="velux2mqtt.adapters.dry_run:DryRunGpioAdapter")
```

### Dependency rule

```text
domain/  →  imports nothing (pure Python)
ports/   →  imports domain types only
devices  →  import domain + ports (NOT adapters directly)
adapters →  import ports + external libraries
cosalette      →  wires adapters to ports at runtime
```

### Hexagonal ↔ FastAPI mapping

| Hexagonal Concept   | cosalette Equivalent                                          |
| -------------------- | ------------------------------------------------------------- |
| Domain layer         | Project's `domain/` package — pure logic, no cosalette imports |
| Port protocols       | Project's `ports/` — PEP 544 `Protocol` classes               |
| Adapters             | Registered via `app.adapter()`, resolved via `ctx.adapter()`   |
| Application layer    | Device functions — orchestrate domain + ports                  |
| Composition root     | `app.run()` — the framework IS the composition root            |
| Infrastructure       | cosalette provides MQTT, logging, clock; projects provide HW   |

## Decision Drivers

- Domain logic must be pure and testable without hardware
- Hardware libraries (RPi.GPIO, smbus2) are not available on dev machines
- PEP 544 structural subtyping avoids framework imports in domain layer
- Adapters must be swappable (real hardware, dry-run mode, test doubles)
- `--dry-run` flag must swap adapters transparently at the framework level
- Go-style interface satisfaction (shape, not inheritance) aligns with
  Python's duck-typing philosophy

## Considered Options

### Option 1: ABC base classes

Use `abc.ABC` with `@abstractmethod` for port definitions.

- *Advantages:* Explicit, well-understood pattern in Python. `isinstance()` checks
  work without `@runtime_checkable`.
- *Disadvantages:* Requires inheritance — adapters must explicitly subclass the ABC.
  This forces the domain to import from the port module's base class, coupling
  adapters to the framework's class hierarchy. Violates the dependency rule if the
  ABC lives in the framework package.

### Option 2: Dependency injection containers

Use a DI container library (e.g., `dependency-injector`, `inject`) for wiring.

- *Advantages:* Automatic wiring, lifecycle management, well-established pattern.
- *Disadvantages:* Heavy dependency for a framework that only needs port/adapter
  resolution. DI containers add a layer of indirection that makes debugging harder.
  The scope of cosalette's wiring needs (a few adapters per app) does not justify
  a full DI framework.

### Option 3: PEP 544 Protocols with manual `app.adapter()` registration (chosen)

Ports are Protocol classes. Adapters are registered on the App object and resolved
by type at runtime. String-based imports enable lazy loading.

- *Advantages:* Structural subtyping — adapters satisfy contracts by shape, no
  inheritance required. Domain stays 100% pure (no framework imports). String-based
  import paths defer hardware library loading to runtime. `--dry-run` adapter swapping
  is built into the registration API.
- *Disadvantages:* String-based imports lose IDE navigation and static type checking
  for the import path. Protocol conformance is only checked at runtime (unless using
  a type checker with `runtime_checkable` protocols).

## Consequences

### Positive

- Domain layer has zero framework dependencies — pure Python, fully testable
- Hardware adapters can be swapped transparently: real hardware vs. dry-run vs. test
  doubles
- String-based lazy imports allow development and testing without hardware libraries
  installed
- `--dry-run` mode works automatically for any adapter that registers a `dry_run`
  variant
- Protocol-based ports are compatible with type checkers (pyright, mypy) for static
  verification

### Negative

- String-based import paths are not statically verifiable — typos in import paths
  fail at runtime, not at import time
- Structural subtyping means an adapter can accidentally satisfy a protocol without
  intending to — though `@runtime_checkable` plus type checker usage mitigates this
- Developers must understand the ports & adapters pattern to contribute effectively

_2026-02-14_
