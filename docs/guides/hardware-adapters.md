---
icon: material/puzzle
---

# Register Hardware Adapters

The adapter pattern is how cosalette achieves hardware abstraction. Define a Protocol
port for what your code _needs_, then register concrete implementations that
satisfy that port. This lets you swap real hardware for mocks in tests and dry-run
mode — without changing any device code.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## The Adapter Pattern in cosalette

cosalette follows hexagonal (ports-and-adapters) architecture
([ADR-006](../adr/ADR-006-hexagonal-architecture.md)):

1. **Port** — a `Protocol` class defining the interface your code depends on.
2. **Adapter** — a concrete class satisfying that protocol.
3. **Registration** — `app.adapter(PortType, Impl)` wires them together.
4. **Resolution** — `ctx.adapter(PortType)` retrieves the instance at runtime.

The framework resolves adapters during startup and injects the same instances into
all device contexts. In dry-run mode (`--dry-run`), it automatically substitutes
dry-run variants.

## Step 1: Define a Protocol Port

Ports use PEP 544 `Protocol` with `@runtime_checkable`:

```python title="ports.py"
from typing import Protocol, runtime_checkable


@runtime_checkable  # (1)!
class GasMeterPort(Protocol):
    """Hardware abstraction for gas meter impulse sensors."""

    def read_impulses(self) -> int: ...  # (2)!
    def read_temperature(self) -> float: ...
```

1. `@runtime_checkable` enables `isinstance()` checks at runtime. This is a PEP 544
   feature — structural subtyping means any class with matching methods satisfies the
   protocol, no inheritance required.
2. Use `...` (Ellipsis) as the method body. Protocols define the interface, not
   the implementation.

!!! tip "Protocol design guidelines"

    - Keep ports **narrow** — one responsibility per protocol (Interface Segregation
      Principle from SOLID).
    - Use **primitive types** in method signatures — strings, ints, floats, dicts.
      Avoid leaking hardware library types through the port.
    - Name ports with a `Port` suffix by convention: `GasMeterPort`, `RelayPort`,
      `DisplayPort`.

## Step 2: Implement the Adapter

Write a concrete class that matches the protocol's method signatures:

```python title="adapters.py"
import serial


class SerialGasMeter:
    """Real gas meter adapter communicating over a serial port."""

    def __init__(self) -> None:
        self._conn: serial.Serial | None = None

    def connect(self, port: str, baud_rate: int = 9600) -> None:
        """Open the serial connection."""
        self._conn = serial.Serial(port, baud_rate, timeout=5)

    def read_impulses(self) -> int:
        """Read impulse count from the meter."""
        assert self._conn is not None
        self._conn.write(b"READ_IMPULSES\n")
        response = self._conn.readline().decode().strip()
        return int(response)

    def read_temperature(self) -> float:
        """Read temperature from the meter's built-in sensor."""
        assert self._conn is not None
        self._conn.write(b"READ_TEMP\n")
        response = self._conn.readline().decode().strip()
        return float(response)

    def close(self) -> None:
        """Close the serial connection."""
        if self._conn is not None:
            self._conn.close()
```

!!! info "No inheritance needed"

    `SerialGasMeter` doesn't inherit from `GasMeterPort`. Python's structural
    subtyping (PEP 544) means it satisfies the protocol as long as it has matching
    `read_impulses()` and `read_temperature()` methods. This is duck typing with
    static type-checking support.

## Step 3: Register the Adapter

cosalette supports three registration forms:

=== "Class (direct)"

    ```python title="app.py"
    from gas2mqtt.adapters import SerialGasMeter
    from gas2mqtt.ports import GasMeterPort

    app.adapter(GasMeterPort, SerialGasMeter)  # (1)!
    ```

    1. The framework calls `SerialGasMeter()` at startup to create the instance.

=== "Lazy import string"

    ```python title="app.py"
    from gas2mqtt.ports import GasMeterPort

    app.adapter(GasMeterPort, "gas2mqtt.adapters:SerialGasMeter")  # (1)!
    ```

    1. The `"module:ClassName"` string is imported lazily at startup. This avoids
       importing hardware libraries (like `pyserial`) at module level — useful when
       the library isn't installed on every machine (e.g. CI).

=== "Factory callable"

    ```python title="app.py"
    from gas2mqtt.ports import GasMeterPort

    def create_meter() -> SerialGasMeter:  # (1)!
        meter = SerialGasMeter()
        meter.connect("/dev/ttyUSB0", baud_rate=115200)
        return meter

    app.adapter(GasMeterPort, create_meter)
    ```

    1. When the impl is a callable but _not_ a type, the framework invokes it as a
       factory. Use this when an adapter needs constructor arguments or initialisation.

!!! warning "One adapter per port type"

    Calling `app.adapter()` twice for the same port type raises `ValueError`. Each
    port has exactly one implementation (real _or_ dry-run).

### Settings Injection

All adapter forms — classes, lazy import strings, and factory callables — support
automatic dependency injection.  If the class `__init__` or factory callable declares
a parameter annotated with `Settings` (or a subclass), the framework injects the
parsed settings instance at resolution time.  Zero-arg constructors and callables
still work unchanged.

This uses the same dependency injection machinery as device handlers — consistent
mental model across the framework.

=== "Class with DI"

    ```python title="app.py"
    class SerialGasMeter:
        def __init__(self, settings: Gas2MqttSettings) -> None:  # (1)!
            self.port = settings.serial_port
            self.baud = settings.baud_rate

        def read_value(self) -> float: ...

    app.adapter(GasMeterPort, SerialGasMeter)
    ```

    1. The framework inspects `__init__`, detects the `Settings`-typed
       parameter, and injects the already-parsed instance automatically.

=== "Factory with DI"

    ```python title="app.py"
    def create_meter(settings: Gas2MqttSettings) -> SerialGasMeter:  # (1)!
        meter = SerialGasMeter()
        meter.connect(settings.serial_port, baud_rate=settings.baud_rate)
        return meter

    app.adapter(GasMeterPort, create_meter)
    ```

    1. The framework detects the `Settings`-typed parameter and injects the
       already-parsed instance automatically.

=== "Before (workaround)"

    ```python title="app.py"
    def create_meter() -> SerialGasMeter:
        s = Gas2MqttSettings()  # (1)!
        meter = SerialGasMeter()
        meter.connect(s.serial_port, baud_rate=s.baud_rate)
        return meter

    app.adapter(GasMeterPort, create_meter)
    ```

    1. Duplicate parse of environment variables — the framework already parsed
       settings, but the factory can't access them.

!!! info "What's injectable?"

    Classes and factory callables can receive `Settings` (or any subclass).
    This is the same type available during adapter resolution at startup.

### Declarative Registration

Instead of calling `app.adapter()` after construction, you can pass all adapters
as a dict to the `App` constructor:

```python
app = cosalette.App(
    name="gas2mqtt",
    version=__version__,
    settings_class=Gas2MqttSettings,
    adapters={
        MagnetometerPort: (Qmc5883lAdapter, FakeMagnetometer),
        StateStoragePort: make_storage_adapter,
    },
)
```

Each key is a port **Protocol type**. Each value is either:

- A **single implementation** (class, lazy-import string, or factory callable) — registered with no dry-run variant
- A **(impl, dry_run) tuple** — the first element is the real implementation, the second is the dry-run variant

This is equivalent to calling `app.adapter()` for each entry:

```python
app.adapter(MagnetometerPort, Qmc5883lAdapter, dry_run=FakeMagnetometer)
app.adapter(StateStoragePort, make_storage_adapter)
```

Both styles coexist — you can use `adapters=` for the bulk of your adapters
and add more with `app.adapter()` afterwards. Duplicate port types raise
`ValueError` regardless of which registration path is used.

/// admonition | When to use which
    type: tip

Use `adapters=` when you want all wiring visible at construction time.
Use `app.adapter()` when adapters are registered conditionally or in
separate modules.
///

## Fail-Fast Validation

When `impl` or `dry_run` is a class or factory callable, the framework validates
its signature **at registration time** — not at startup resolution.  This means
errors like un-annotated parameters surface immediately when `app.adapter()` is
called, rather than later when the framework tries to resolve them.

```python title="app.py"
# This raises TypeError immediately — `port` has no annotation
def bad_factory(port) -> SerialGasMeter:  # (1)!
    meter = SerialGasMeter()
    meter.connect(port)
    return meter

app.adapter(GasMeterPort, bad_factory)  # TypeError at this line!
```

1. The parameter `port` lacks a type annotation.  The framework's injection
   system requires annotations to resolve dependencies, so it rejects the
   factory immediately rather than allowing it to fail silently at runtime.

Both `impl` and `dry_run` are validated when they are classes or factory
callables.  Lazy import strings are validated later at resolution time
(since the target class isn't available until import).

!!! tip "Why fail-fast matters"

    Without this validation, a typo or missing annotation in a factory callable
    would only surface when the app starts up and tries to resolve adapters.
    By catching it at registration time, the error appears at the
    `app.adapter()` call site — closer to the bug, easier to diagnose.

## Step 4: Dry-Run Variants

The `dry_run` parameter registers an alternative implementation used when the app
runs with `--dry-run`:

```python title="app.py"
from gas2mqtt.ports import GasMeterPort


class FakeGasMeter:
    """Mock gas meter for dry-run mode and testing."""

    def read_impulses(self) -> int:
        return 42

    def read_temperature(self) -> float:
        return 21.5


app.adapter(
    GasMeterPort,
    "gas2mqtt.adapters:SerialGasMeter",  # (1)!
    dry_run=FakeGasMeter,  # (2)!
)
```

1. Real adapter — used in production. Lazy-imported to avoid `pyserial` dependency
   during development.
2. Fake adapter — used when running `gas2mqtt --dry-run`. No hardware needed.

The `dry_run` parameter accepts the same three forms: class, lazy import string, or
factory callable.

## Step 5: Resolve in Device Code

Use `ctx.adapter(PortType)` to get the registered instance:

```python title="app.py"
from gas2mqtt.ports import GasMeterPort


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)  # (1)!
    return {"impulses": meter.read_impulses()}
```

1. Returns the adapter instance. The framework resolved it at startup — this is a
   simple dict lookup, no instantiation happens here.

### Resolution in Lifespan

Adapters are also available in the lifespan function via `AppContext`:

```python title="app.py"
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    meter = ctx.adapter(GasMeterPort)  # (1)!
    # Perform one-time initialisation...
    meter.connect(ctx.settings.serial_port)
    yield
    meter.close()
```

1. Same resolution mechanism, different context type. `AppContext` has `.settings`
   and `.adapter()` — but _no_ publish, sleep, or on_command methods.

## TYPE_CHECKING Guard

For type checkers to understand the adapter's type without importing the real
implementation at runtime, use the `TYPE_CHECKING` guard:

```python title="app.py"
from __future__ import annotations

from typing import TYPE_CHECKING

import cosalette

if TYPE_CHECKING:
    from gas2mqtt.ports import GasMeterPort  # (1)!


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    from gas2mqtt.ports import GasMeterPort  # (2)!

    meter = ctx.adapter(GasMeterPort)
    return {"impulses": meter.read_impulses()}
```

1. Import for type-checking only — mypy/pyright sees it, Python doesn't execute it.
2. Runtime import inside the function body. This is the pattern when you want to avoid
   top-level imports of hardware-dependent modules.

!!! info "Why the double import?"

    `from __future__ import annotations` makes all annotations string-based (PEP 563),
    so the `TYPE_CHECKING` import works for type hints. The runtime import inside the
    function is needed because `ctx.adapter()` needs the actual class object as a dict
    key. This is the standard pattern in hexagonal architecture codebases.

## Practical Example: GPIO Adapter

A complete adapter setup for a gas meter impulse sensor using GPIO:

```python title="ports.py"
"""Port definitions for gas2mqtt."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class GasMeterPort(Protocol):
    """Read gas meter impulse counts and temperature."""

    def read_impulses(self) -> int: ...
    def read_temperature(self) -> float: ...
    def close(self) -> None: ...
```

```python title="adapters.py"
"""Adapter implementations for gas2mqtt."""


class GpioGasMeter:
    """Real adapter using GPIO pin to count reed switch impulses."""

    def __init__(self) -> None:
        import RPi.GPIO as GPIO  # (1)!

        self._gpio = GPIO
        self._pin = 17
        self._count = 0
        self._gpio.setmode(GPIO.BCM)
        self._gpio.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._gpio.add_event_detect(
            self._pin, GPIO.FALLING, callback=self._on_impulse
        )

    def _on_impulse(self, channel: int) -> None:
        self._count += 1

    def read_impulses(self) -> int:
        return self._count

    def read_temperature(self) -> float:
        return 0.0  # GPIO-only — no temperature sensor

    def close(self) -> None:
        self._gpio.cleanup(self._pin)


class FakeGasMeter:
    """Mock adapter for dry-run mode and testing."""

    def __init__(self) -> None:
        self._impulses = 0

    def read_impulses(self) -> int:
        self._impulses += 1  # (2)!
        return self._impulses

    def read_temperature(self) -> float:
        return 21.5

    def close(self) -> None:
        pass
```

1. GPIO library imported inside `__init__` — only runs on actual Raspberry Pi hardware.
   On dev machines, the lazy import string avoids this import entirely.
2. The fake increments on each read, simulating realistic changing data.

```python title="app.py"
"""gas2mqtt — wire adapters and run."""

import cosalette
from gas2mqtt.adapters import FakeGasMeter
from gas2mqtt.ports import GasMeterPort

app = cosalette.App(name="gas2mqtt", version="1.0.0")

app.adapter(
    GasMeterPort,
    "gas2mqtt.adapters:GpioGasMeter",
    dry_run=FakeGasMeter,
)


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)
    return {"impulses": meter.read_impulses()}


app.run()
```

---

## Adapter Lifecycle Management

If your adapter implements the async context manager protocol (`__aenter__`/`__aexit__`),
the framework auto-manages it — entering during startup and exiting during shutdown.
No `lifespan=` hook needed.

### Making an Adapter Lifecycle-Managed

Implement `__aenter__` and `__aexit__` on your adapter class:

```python title="adapters.py"
import aiosqlite


class SqliteAdapter:
    """Database adapter with automatic lifecycle management."""

    def __init__(self, db_path: str = "data.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "SqliteAdapter":  # (1)!
        self._conn = await aiosqlite.connect(self._db_path)
        return self

    async def __aexit__(self, *exc: object) -> None:  # (2)!
        if self._conn:
            await self._conn.close()

    async def query(self, sql: str) -> list[dict[str, object]]:
        assert self._conn is not None
        async with self._conn.execute(sql) as cursor:
            return [dict(row) async for row in cursor]
```

1. `__aenter__` runs during startup, before the lifespan hook and device tasks.
2. `__aexit__` runs during shutdown, after the lifespan hook exits.

Register it normally — the framework detects the protocol automatically:

```python title="app.py"
app.adapter(DatabasePort, SqliteAdapter)
# No lifespan= needed — the framework enters/exits SqliteAdapter for you
```

### What the Framework Does

During startup, the framework scans resolved adapters for `__aenter__`/`__aexit__`
and enters them via `AsyncExitStack`:

```text
Adapter __aenter__  →  lifespan startup  →  devices run  →  lifespan teardown  →  Adapter __aexit__
```

This means:

- Lifespan code can use already-entered adapters (e.g. query a connected database)
- Adapter cleanup runs after lifespan teardown completes
- `AsyncExitStack` guarantees LIFO cleanup order and exception safety

### When You Still Need `lifespan=`

The adapter lifecycle protocol handles the common case. Use `lifespan=` when you need:

- **Ordering constraints** — e.g. adapter A must initialise before adapter B
- **Multi-step initialisation** — actions between different adapter setups
- **Non-adapter resources** — things that aren't registered as adapters (caches,
  background tasks, external services)
- **Conditional logic** — init paths that depend on runtime state

See [Manage App Lifespan](lifespan.md) for details.

!!! info "Both mechanisms can coexist"

    Lifecycle adapters are entered _before_ the lifespan hook and exited _after_ it.
    You can have auto-managed adapters and a `lifespan=` hook in the same app — the
    lifespan code can safely use the already-entered adapters.

---

## See Also

- [Hexagonal Architecture](../concepts/hexagonal.md) — the conceptual foundation for
  ports and adapters
- [ADR-006](../adr/ADR-006-hexagonal-architecture.md) — hexagonal architecture
  decisions
- [ADR-009](../adr/ADR-009-python-version-and-dependencies.md) — Python version and
  dependency decisions
- [ADR-016](../adr/ADR-016-adapter-lifecycle-protocol.md) — adapter lifecycle protocol
  decisions
