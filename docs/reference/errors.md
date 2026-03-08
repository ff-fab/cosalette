# Error Taxonomy

Complete catalog of all exceptions raised by the cosalette framework. Errors are
grouped by **when** they occur: during registration (app setup), at runtime (after
bootstrap), or through the error publishing pipeline.

!!! tip "Concept vs Reference"

    For the **design rationale** behind error handling, see
    [Error Handling (concept)](../concepts/error-handling.md).
    For the **error payload JSON schema** and MQTT topics, see
    [Payload Schemas](payloads.md).
    This page catalogs every framework-raised exception with its trigger,
    message, and fix.

## Registration-Time Errors

These exceptions are raised **before the app starts running** — during device
registration, decorator application, or `App()` construction. They indicate
a programming mistake in your setup code.

### TypeError

Registration `TypeError` exceptions mean the framework received a value of
the wrong type, or a callback/handler violates a structural requirement.

#### Decorator Parentheses

Raised when `@app.device` or `@app.command` is used without parentheses.

| Location | Message |
|---|---|
| `App.device()` | `Use @app.device(), not @app.device (parentheses required)` |
| `App.command()` | `Use @app.command(), not @app.command (parentheses required)` |

**Cause:** Python calls the decorator with the function as the first argument
when parentheses are missing, which is never the intended use.

**Fix:** Always use parentheses, even with no arguments:

```python
# Wrong
@app.device
async def my_device(ctx: DeviceContext) -> dict[str, object]:
    ...

# Correct
@app.device()
async def my_device(ctx: DeviceContext) -> dict[str, object]:
    ...
```

#### Async `init` Callback

Raised when the `init=` parameter of `@app.device()` receives an async function
or a callable with an async `__call__`.

| Location | Message |
|---|---|
| `_registration` | `init= must be a synchronous callable, not async. Use a regular function or a class with __call__.` |
| `_registration` | `init= must be a synchronous callable, not async. The __call__ method is a coroutine function.` |

**Cause:** The `init=` callback runs during synchronous bootstrap. Async functions
cannot be awaited in that phase.

**Fix:** Use a regular synchronous function:

```python
# Wrong
async def setup_sensor():
    return SensorClient()

@app.device(init=setup_sensor)  # TypeError!
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    ...

# Correct
def setup_sensor():
    return SensorClient()

@app.device(init=setup_sensor)
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    ...
```

#### `init` Result Shadows Injectable

Raised when the `init=` callback returns a type that the framework already
provides via dependency injection (e.g. `AppContext`, `MqttPort`).

| Location | Message |
|---|---|
| `_registration` | `init= callback returned {type}!, which shadows a framework-provided type. Use a wrapper class or a different type.` |

**Fix:** Wrap the value in a domain-specific type instead of returning a
framework type directly.

#### Bool Parameters (Type Guard)

Because `bool` is a subclass of `int` (and `float`), numeric constructor
parameters explicitly reject booleans to catch accidental `True`/`False`
arguments.

| Location | Parameter | Message |
|---|---|---|
| `Pt1Filter` | `tau` | `tau must be a number, got bool: {tau!r}` |
| `Pt1Filter` | `dt` | `dt must be a number, got bool: {dt!r}` |
| `MedianFilter` | `window` | `window must be an int, got bool: {window!r}` |
| `OnChange` | per-field threshold | `Threshold for '{field}' must be a number, got bool` |
| `OnChange` | global threshold | `Threshold must be a number, got bool` |
| `DeadbandFilter` | per-field threshold | `Threshold for '{field}' must be a number, got bool` |

**Cause:** `isinstance(True, int)` is `True` in Python, so without an
explicit guard, `Pt1Filter(tau=True)` would silently pass as `tau=1`.

**Fix:** Pass a numeric literal:

```python
# Wrong
Pt1Filter(tau=True, dt=0.1)   # TypeError

# Correct
Pt1Filter(tau=1.0, dt=0.1)
```

#### Non-Int `window`

Raised when `MedianFilter(window=...)` receives a value that is not
an `int` (and not a `bool`).

| Location | Message |
|---|---|
| `MedianFilter` | `window must be an int, got {type}: {window!r}` |

**Fix:** Pass an integer: `MedianFilter(window=5)`.

#### Handler Annotation Errors

Raised when a device or command handler has parameters that the injection
system cannot resolve.

| Location | Message |
|---|---|
| `_injection` | `Parameter '{name}' of handler {qualname!r} has no type annotation...` |
| `_injection` | `Parameter '{name}' of handler {qualname!r} has unsupported kind...` |
| `_injection` | `Parameter '{name}' of handler {qualname!r} has annotation {annotation!r} which is not a type...` |

**Cause:** The injection system resolves handler parameters by their type
annotations. Every parameter must have a concrete type annotation — no
`*args`, `**kwargs`, positional-only, or non-type annotations.

**Fix:** Annotate every parameter with a concrete type:

```python
# Wrong — missing annotation
@app.device()
async def sensor(ctx):  # TypeError!
    ...

# Wrong — *args
@app.device()
async def sensor(*args: DeviceContext) -> dict[str, object]:  # TypeError!
    ...

# Correct
@app.device()
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    ...
```

#### Adapter `__aenter__` Not Callable

Raised when an adapter has an `__aenter__` attribute that is not callable.

| Location | Message |
|---|---|
| `_adapter_lifecycle` | `Adapter {adapter!r} has __aenter__ but it's not callable` |

**Fix:** Ensure the adapter is a proper async context manager with a
callable `__aenter__` method.

#### Unresolved Interval

Raised when a telemetry device's interval is still a callable at runtime,
meaning `resolve_intervals()` was never called during bootstrap.

| Location | Message |
|---|---|
| `_telemetry_runner` | `Interval for {name!r} has not been resolved (still a callable). Was resolve_intervals() called?` |

**Cause:** This is an internal consistency error — the framework should
resolve deferred intervals during bootstrap. If you see this, it may
indicate a framework bug or manual misuse of internal APIs.

### ValueError

Registration `ValueError` exceptions indicate a value that is the right
type but violates a constraint (negative interval, duplicate name, etc.).

#### Negative or Zero Intervals

Raised when a time interval is not positive.

| Location | Parameter | Message |
|---|---|---|
| `App()` | `heartbeat_interval` | `heartbeat_interval must be positive, got {value}` |
| `App.device()` | `interval` | `Telemetry interval must be positive, got {interval}` |
| `_wiring` | resolved interval | `Telemetry interval for {name!r} must be positive, got {resolved}` |

**Fix:** Pass a positive numeric value:

```python
# Wrong
app = App(heartbeat_interval=-5)   # ValueError
app = App(heartbeat_interval=0)    # ValueError

# Correct
app = App(heartbeat_interval=30)
```

#### Duplicate Registration

Raised when registering a device name or handler that already exists.

| Location | Message |
|---|---|
| `_registration` | `Device name '{name}' is already registered` |
| `_registration` | `Only one root device (unnamed) is allowed per app` |
| `_registration` | `Cannot share name '{name}' between root and named registrations — MQTT topic namespaces would conflict` |
| `_router` | `Handler already registered for device '{device_name}'` |
| `_router` | `Root handler already registered` |

**Cause:** Each device name must be unique within an app (see ADR-019
for scoped name uniqueness). The root device (unnamed) is limited to one.

**Fix:** Use distinct names for each device:

```python
@app.device(name="temperature")
async def temp_device(ctx: DeviceContext) -> dict[str, object]:
    ...

@app.device(name="humidity")   # Different name
async def humidity_device(ctx: DeviceContext) -> dict[str, object]:
    ...
```

#### Duplicate Adapter

Raised when registering a second adapter for the same port type.

| Location | Message |
|---|---|
| `App.adapter()` | `Adapter already registered for {port_type!r}` |

**Fix:** Register only one adapter per port type.

#### Invalid Adapter Tuple

Raised when the `adapters=` dict value is a tuple that is not a 2-tuple.

| Location | Message |
|---|---|
| `App()` | `adapters value for {port_type!r} must be an impl or (impl, dry_run) 2-tuple, got {len}-tuple` |

**Fix:** Pass either a single adapter instance or a `(impl, dry_run)` pair:

```python
# Single adapter
app = App(adapters={MqttPort: my_mqtt_client})

# Adapter + dry-run pair
app = App(adapters={MqttPort: (my_mqtt_client, null_mqtt_client)})
```

#### Empty Group Name

Raised when a coalescing group name is an empty string.

| Location | Message |
|---|---|
| `App.device()` | `group must be non-empty` |
| `App.command()` | `group must be non-empty` |

**Fix:** Pass a non-empty string for the `group` parameter.

#### Persist Without Store

Raised when `persist=True` is set but no `store=` backend was provided
to `App()`.

| Location | Message |
|---|---|
| `App.device()` | `persist= requires a store= backend on the App. Pass store=MemoryStore() (or another Store) to App().` |
| `App.command()` | `persist= requires a store= backend on the App. Pass store=MemoryStore() (or another Store) to App().` |

**Fix:** Pass a store backend when constructing the app:

```python
from cosalette import App, MemoryStore

app = App(store=MemoryStore())

@app.device(persist=True)
async def sensor(ctx: DeviceContext) -> dict[str, object]:
    ...
```

#### Filter and Strategy Parameters

Numeric parameters on filters and strategies must be within valid ranges.

| Component | Parameter | Constraint | Message |
|---|---|---|---|
| `Pt1Filter` | `tau` | `> 0` | `tau must be positive, got {tau!r}` |
| `Pt1Filter` | `dt` | `> 0` | `dt must be positive, got {dt!r}` |
| `MedianFilter` | `window` | `>= 1` | `window must be >= 1, got {window!r}` |
| `OneEuroFilter` | `min_cutoff` | `> 0` | `min_cutoff must be positive, got {value!r}` |
| `OneEuroFilter` | `beta` | `>= 0` | `beta must be non-negative, got {value!r}` |
| `OneEuroFilter` | `d_cutoff` | `> 0` | `d_cutoff must be positive, got {value!r}` |
| `OneEuroFilter` | `dt` | `> 0` | `dt must be positive, got {value!r}` |
| `OnChange` | per-field threshold | `>= 0` | `Threshold for '{field}' must be non-negative, got {value}` |
| `OnChange` | global threshold | `>= 0` | `Threshold must be non-negative, got {threshold}` |
| `Every` | `seconds` | `> 0` | `'seconds' must be positive` |
| `Every` | `n` | `> 0` | `'n' must be positive` |

#### Strategy Mutual Exclusion

`Every()` requires exactly one of `seconds` or `n`, not both and not neither.

| Location | Message |
|---|---|
| `Every()` | `Specify exactly one of 'seconds' or 'n', not both` |
| `Every()` | `Specify exactly one of 'seconds' or 'n'` |

**Fix:**

```python
# Wrong
Every(seconds=5, n=10)  # ValueError — both specified
Every()                  # ValueError — neither specified

# Correct
Every(seconds=5)
Every(n=10)
```

#### Composite Policy Children

`AnySavePolicy` and `AllSavePolicy` require at least one child policy.

| Location | Message |
|---|---|
| `AnySavePolicy` | `AnySavePolicy requires at least one child policy` |
| `AllSavePolicy` | `AllSavePolicy requires at least one child policy` |

**Fix:** Pass at least one child policy to the composite.

#### Import Path Format

Raised when an import path string does not follow the `module.path:attr_name`
convention.

| Location | Message |
|---|---|
| `_utils` | `Expected 'module.path:attr_name', got {dotted_path!r}` |

**Fix:** Use the colon-separated format: `"mypackage.module:MyClass"`.

## Runtime Errors

These exceptions are raised **after the app has started** — during bootstrap
completion, MQTT operations, or store access.

### RuntimeError

#### Settings Unavailable

Raised when the settings model cannot be instantiated, typically because
required environment variables are missing.

| Location | Message |
|---|---|
| `_context` | `Settings could not be instantiated at construction time (missing required fields?). Ensure required environment variables are set, or use app.cli() with --env-file.` |

**Fix:** Set the required environment variables before running the app,
or use `app.cli()` with `--env-file` to load them from a file.

#### MQTT Not Connected

Raised when attempting to publish or subscribe but the MQTT client is not
connected.

| Location | Message |
|---|---|
| `MqttClient` | `MqttClient is not connected` |

**Cause:** Publishing was attempted before the MQTT client connected, or
after it disconnected. The framework manages connection lifecycle
automatically — this typically indicates use of the `MqttClient` outside
the normal lifecycle.

#### aiomqtt Not Installed

Raised when `MqttClient` is instantiated but the `aiomqtt` package is not
available.

| Location | Message |
|---|---|
| `MqttClient` | `aiomqtt is required to use MqttClient` |

**Fix:** Install the MQTT extra: `pip install cosalette[mqtt]` or
`uv add cosalette[mqtt]`.

#### Store Not Loaded

Raised when accessing `DeviceStore` data before `load()` has been called.

| Location | Message |
|---|---|
| `DeviceStore` | `DeviceStore.load() must be called before accessing data` |

**Cause:** The framework calls `load()` during bootstrap. This error
indicates manual use of `DeviceStore` outside the normal lifecycle,
or a framework bug.

#### Store Not Set

Raised internally when `create_device_store()` is called but no store
backend was configured.

| Location | Message |
|---|---|
| `_runner_utils` | `store must be set before calling create_device_store` |

### LookupError

#### Adapter Not Found

Raised when requesting an adapter for a port type that was never registered.

| Location | Message |
|---|---|
| `AppContext.adapter()` | `No adapter registered for {port_type!r}` |

**Fix:** Register the required adapter when constructing the app:

```python
app = App(adapters={MqttPort: my_mqtt_client})
```

## CLI Errors

These exceptions are raised by the CLI layer (Typer) when the user
provides invalid command-line arguments.

### typer.BadParameter

| Location | Message |
|---|---|
| `_cli` | `Invalid log level '{value}'. Choose from: {choices}` |
| `_cli` | `Invalid log format '{value}'. Choose from: {choices}` |

### SystemExit

The CLI exits with code 1 (`EXIT_CONFIG_ERROR`) when the configuration
model raises a validation error (e.g. from pydantic).

## Error Publishing Pipeline

The framework includes a built-in error publishing system for reporting
runtime errors via MQTT. This section summarizes the pipeline — for full
details, see [Error Handling (concept)](../concepts/error-handling.md) and
[Payload Schemas](payloads.md).

### Pipeline Flow

```text
Exception raised in device function
    ↓
build_error_payload(error, error_type_map=..., device=...)
    ↓
ErrorPayload(error_type, message, device, timestamp, details)
    ↓
ErrorPublisher.publish()
    ↓
MQTT: {prefix}/error              (global, always)
MQTT: {prefix}/{device}/error     (per-device, when device known)
```

### `error_type_map` Pattern

The `error_type_map` is a `dict[type[Exception], str]` that maps
**exact exception classes** (no subclass matching) to machine-readable
`error_type` strings. Unmapped exceptions produce `"error"` as the type.

```python
error_type_map: dict[type[Exception], str] = {
    InvalidCommandError: "invalid_command",
    TimeoutError: "timeout",
    ConnectionError: "connection_lost",
}
```

### Publication Behaviour

| Property | Value |
|---|---|
| QoS | 1 (at-least-once) |
| Retained | No — errors are events, not state |
| Failure handling | Fire-and-forget — logged but never propagated |
| Output | Dual — logged at WARNING + published to MQTT |

### Topic Layout

| Topic | Description |
|---|---|
| `{prefix}/error` | Global error topic — receives all errors |
| `{prefix}/{device}/error` | Per-device error topic — when device name is known |

Root devices (unnamed) only publish to the global topic to avoid
duplicating the same error on both topics.

## See Also

- [Error Handling (concept)](../concepts/error-handling.md) — design principles
  and rationale
- [Payload Schemas](payloads.md) — JSON schema for `ErrorPayload`
- [Map Custom Error Types (guide)](../guides/error-types.md) — how to use
  `build_error_payload()` with custom domain exceptions
- [ADR-011](../adr/ADR-011-error-handling-and-publishing.md) — architecture
  decision record
