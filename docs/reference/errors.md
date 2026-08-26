---
icon: material/alert-circle-outline
---

# Error Taxonomy

Complete catalog of all exceptions raised by the cosalette framework. Errors are
grouped by **when** they occur: during registration (app setup), at runtime (after
bootstrap), or through the error publishing pipeline.

!!! tip "Concept vs Reference"

    For the **design rationale** behind error handling, see
    [Error Handling (concept)](../concepts/error-handling.md).
    For the **error payload JSON schema** and MQTT topics, see
    [Payload Schemas](payloads.md).
    This page catalogs every framework-raised exception with its trigger
    and message. For fix examples, see
    [Handling Errors (guide)](../guides/error-types.md).

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
| `app.device()` | `Use @app.device(), not @app.device (parentheses required)` |
| `app.command()` | `Use @app.command(), not @app.command (parentheses required)` |

**Cause:** Python calls the decorator with the function as the first argument
when parentheses are missing, which is never the intended use.

#### Async `init` Callback

Raised when the `init=` parameter of `@app.device()` receives an async function
or a callable with an async `__call__`.

| Location | Message |
|---|---|
| `app.device(init=...)` | `init= must be a synchronous callable, not async. Use a regular function or a class with __call__.` |
| `app.device(init=...)` | `init= must be a synchronous callable, not async. The __call__ method is a coroutine function.` |

**Cause:** The `init=` callback runs during synchronous bootstrap. Async functions
cannot be awaited in that phase.

#### `init` Result Shadows Injectable

Raised when the `init=` callback returns a type that the framework already
provides via dependency injection (e.g. `AppContext`, `MqttPort`).

| Location | Message |
|---|---|
| `app.device(init=...)` | `init= callback returned {type}!, which shadows a framework-provided type. Use a wrapper class or a different type.` |

#### Bool Parameters (Type Guard)

Because `bool` is a subclass of `int` (and `float`), numeric constructor
parameters explicitly reject booleans to catch accidental `True`/`False`
arguments.

| Location | Parameter | Message |
|---|---|---|
| `Pt1Filter` | `tau` | `tau must be a number, got bool: {tau!r}` |
| `Pt1Filter` | `dt` | `dt must be a number, got bool: {dt!r}` |
| `MedianFilter` | `window` | `window must be an int, got bool: {window!r}` |
| `OneEuroFilter` | `min_cutoff`, `beta`, `d_cutoff`, `dt` | `{name} must be a number, got bool: {val!r}` |
| `OnChange` | per-field threshold | `Threshold for '{field}' must be a number, got bool` |
| `OnChange` | global threshold | `Threshold must be a number, got bool` |

**Cause:** `isinstance(True, int)` is `True` in Python, so without an
explicit guard, `Pt1Filter(tau=True)` would silently pass as `tau=1`.

#### Non-Int `window`

Raised when `MedianFilter(window=...)` receives a value that is not
an `int` (and not a `bool`).

| Location | Message |
|---|---|
| `MedianFilter` | `window must be an int, got {type}: {window!r}` |

#### Handler Annotation Errors

Raised when a device or command handler has parameters that the injection
system cannot resolve.

| Location | Message |
|---|---|
| Handler injection | `Parameter '{name}' of handler {qualname!r} has no type annotation...` |
| Handler injection | `Parameter '{name}' of handler {qualname!r} has unsupported kind...` |
| Handler injection | `Parameter '{name}' of handler {qualname!r} has annotation {annotation!r} which is not a type...` |
| Handler injection | `Parameter '{name}' of handler {qualname!r} has unresolvable annotation {annotation!r}: {ExcType}: {exc}. Ensure the type is imported and available.` |

**Cause:** The injection system resolves handler parameters by their type
annotations. Every parameter must have a concrete type annotation — no
`*args`, `**kwargs`, positional-only, or non-type annotations.

The `unresolvable annotation` variant means the annotation could not be
evaluated at all — under `from __future__ import annotations` the annotation
is a string that is resolved at registration time. The underlying exception is
included in the message and chained as `__cause__`, so `NameError: name 'Foo'
is not defined` really does mean a missing import. Errors raised by the DI
markers themselves (see below) are reported verbatim instead.

#### Invalid `Depends()` Dependency

Raised by `Depends()` when the dependency callable cannot be supported. Under
`from __future__ import annotations` the marker is constructed when the
handler's annotations are resolved (registration time), not at `def` time.

| Location | Message |
|---|---|
| `Depends()` | `Async dependency functions are not supported in the first wave. Use a synchronous callable for Depends(). Got: {dependency!r}` |
| `Depends()` | `Async dependency functions are not supported in the first wave. Use a synchronous callable for Depends(). The __call__ method is a coroutine function. Got: {dependency!r}` |
| `Depends()` | `Depends() requires a hashable dependency callable — its injection plan is cached by identity. Got unhashable {type} instance: {dependency!r}. Define __hash__ on the class, or wrap the call in a plain function.` |

**Cause:** Dependencies are resolved synchronously while building handler
kwargs, so nothing can await them. All three async forms are rejected: an
`async def` function, an async generator function, and a callable object whose
`__call__` is either of those. Dependency injection plans are cached by
dependency identity, so the callable must also be hashable.

#### Adapter `__aenter__` Not Callable

Raised when an adapter has an `__aenter__` attribute that is not callable.

| Location | Message |
|---|---|
| Adapter lifecycle | `Adapter {adapter!r} has __aenter__ but it's not callable` |

#### Unresolved Interval

Raised when a telemetry device's interval is still a callable at runtime,
meaning `resolve_intervals()` was never called during bootstrap.

| Location | Message |
|---|---|
| Telemetry runner | `Interval for {name!r} has not been resolved (still a callable). Was resolve_intervals() called?` |

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
| `app.device()` | `interval` | `Telemetry interval must be positive, got {interval}` |
| Bootstrap wiring | resolved interval | `Telemetry interval for {name!r} must be positive, got {resolved}` |

#### Duplicate Registration

Raised when registering a device name or handler that already exists.

| Location | Message |
|---|---|
| `app.device()` / `app.command()` | `Device name '{name}' is already registered` |
| `app.device()` / `app.command()` | `Only one root device (unnamed) is allowed per app` |
| `app.device()` / `app.command()` | `Cannot share name '{name}' between root and named registrations — MQTT topic namespaces would conflict` |
| Command router | `Handler already registered for device '{device_name}'` |
| Command router | `Root handler already registered` |

**Cause:** Each device name must be unique within an app (see ADR-019
for scoped name uniqueness). The root device (unnamed) is limited to one.

#### Duplicate Adapter

Raised when registering a second adapter for the same port type.

| Location | Message |
|---|---|
| `app.adapter()` | `Adapter already registered for {port_type!r}` |

#### Invalid Adapter Tuple

Raised when the `adapters=` dict value is a tuple that is not a 2-tuple.

| Location | Message |
|---|---|
| `App(adapters=...)` | `adapters value for {port_type!r} must be an impl or (impl, dry_run) 2-tuple, got {len}-tuple` |

#### Empty Group Name

Raised when a coalescing group name is an empty string.

| Location | Message |
|---|---|
| `app.device(group=...)` | `group must be non-empty` |
| `app.command(group=...)` | `group must be non-empty` |

#### Persist Without Store

Raised when a `persist=` policy (e.g. `SaveOnPublish()`) is registered on
`@app.telemetry` and the app has explicitly opted out of persistence with
`store=None`.

!!! note "Default store since ADR-049"
    Omitting `store=` from `App(...)` now auto-resolves a `JsonFileStore`
    (see [Default Store Resolution](../concepts/persistence.md#default-store-resolution)
    and [ADR-049](../adr/ADR-049-default-store-path-resolution.md)), so
    `persist=` works without any explicit `store=` argument. This error
    only triggers when `store=None` (explicit opt-out) is combined with
    `persist=`.

| Location | Message |
|---|---|
| `@app.telemetry(..., persist=SaveOnPublish())` | `persist= requires a store= backend on the App. Pass store=MemoryStore() (or another Store) to App().` |

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

#### Composite Policy Children

`AnySavePolicy` and `AllSavePolicy` require at least one child policy.

| Location | Message |
|---|---|
| `AnySavePolicy()` | `AnySavePolicy requires at least one child policy` |
| `AllSavePolicy()` | `AllSavePolicy requires at least one child policy` |

#### Composite Strategy Children

`AnyStrategy` and `AllStrategy` require at least one child strategy.

| Location | Message |
|---|---|
| `AnyStrategy()` | `AnyStrategy requires at least one child strategy` |
| `AllStrategy()` | `AllStrategy requires at least one child strategy` |

#### Import Path Format

Raised when an import path string does not follow the `module.path:attr_name`
convention.

| Location | Message |
|---|---|
| `import_string()` | `Expected 'module.path:attr_name', got {dotted_path!r}` |

## Runtime Errors

These exceptions are raised **after the app has started** — during bootstrap
completion, MQTT operations, or store access.

### TypeError

Runtime `TypeError` exceptions come from dependency resolution, which happens
per dispatch so that adapters can be registered in any order.

#### Unresolved Provider

Raised when a handler parameter's type has no matching provider.

| Location | Message |
|---|---|
| DI resolution | `Cannot resolve parameter '{name}': no provider is registered for type {module.QualName}. Register an implementation with app.adapter({Name}, <implementation>) before the app starts, or annotate the parameter with a type the framework provides. Available types: {sorted qualnames}` |

**Cause:** The type is neither a framework-provided injectable (`DeviceContext`,
`Settings`, `logging.Logger`, `ClockPort`, …) nor a registered adapter port.
Resolution is deliberately deferred to dispatch time, so a missing
`app.adapter()` call surfaces here rather than at registration.

#### Awaitable Dependency Result

Raised when a synchronous `Depends()` callable returns a coroutine (or any
other awaitable) — the case `Depends()` cannot detect statically.

| Location | Message |
|---|---|
| DI resolution | `Dependency {qualname!r} for parameter '{name}' returned an awaitable ({type}). Async dependencies are not supported in the first wave — return a plain value from a synchronous callable.` |

**Cause:** `Depends(lambda: async_fn())` passes every registration-time check
because the lambda itself is synchronous. Injecting its result would hand the
handler an un-awaited coroutine.

#### Circular Dependency

Raised when a `Depends()` callable depends on itself, directly or transitively.

| Location | Message |
|---|---|
| DI resolution | `Circular dependency detected while resolving parameter '{name}': {a -> b -> a}. Depends() callables must not depend on themselves, directly or transitively.` |

**Cause:** Dependencies are resolved recursively; a cycle would otherwise
exhaust the stack with a bare `RecursionError`.

#### Topic Marker Outside Request Context {#request-context-errors}

Raised when a parameter annotated with `Annotated[str, Topic()]` is dispatched
in a context that carries no MQTT topic — for example, a scheduled telemetry
cycle (not triggered by an incoming message).

| Location | Message |
|---|---|
| DI resolution | `Parameter '{name}': Topic() marker requires a request context (MQTT topic) but none is available.` |

**Cause:** `Topic()` extracts the topic from the current MQTT message.
Telemetry and periodic handlers run on a schedule and have no inbound message,
so the topic is `None` at dispatch time.

#### Message Type Outside Request Context

Raised when a parameter is annotated with the `Message` type and the handler
runs in a non-command context (no inbound MQTT topic or payload).

| Location | Message |
|---|---|
| DI resolution | `Parameter '{name}': Message type requires a request context but none is available (non-command context).` |

**Cause:** `Message` bundles the raw MQTT topic and payload into a single
object. Outside a command handler, neither is available.

### RuntimeError

#### Settings Unavailable

Raised when the settings model cannot be instantiated, typically because
required environment variables are missing.

| Location | Message |
|---|---|
| `AppContext` | `Settings could not be instantiated at construction time (missing required fields?). Ensure required environment variables are set, or use app.cli() with --env-file.` |

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
| Device bootstrap | `store must be set before calling create_device_store` |

### LookupError

#### Adapter Not Found

Raised when requesting an adapter for a port type that was never registered.

| Location | Message |
|---|---|
| `AppContext.adapter()` | `No adapter registered for {port_type!r}` |

## CLI Errors

These exceptions are raised by the CLI layer (Typer) when the user
provides invalid command-line arguments.

### typer.BadParameter

| Location | Message |
|---|---|
| `app.cli()` | `Invalid log level '{value}'. Choose from: {choices}` |
| `app.cli()` | `Invalid log format '{value}'. Choose from: {choices}` |

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

### `disclose_messages_for` Pattern

`disclose_messages_for` (`frozenset[type[Exception]] | None = None`) decouples
**message disclosure** from `error_type_map`'s labeling (F-DP1,
[ADR-061](../adr/ADR-061-decoupled-error-message-disclosure.md)). It is
accepted by `App`, `ErrorPublisher`, `build_error_payload()`,
`AppHarness.create()`, and `create_services()`.

| Value | Behaviour |
|---|---|
| `None` (default) | Legacy behaviour: `error_type_map` membership alone implies message disclosure |
| `frozenset({...})` | Fully replaces the disclosure decision: only listed exact types have their `str(error)` published, independent of `error_type_map` — framework-mapped types are **not** auto-added |
| `frozenset()` | Discloses nothing — every exception, mapped or not, publishes only its class name |

`verbose=True` (`MqttSettings.error_publish_verbose`) still overrides both and
discloses every message unconditionally.

```python
app = cosalette.App(
    name="caldates2mqtt",
    error_type_map={CalDavConnectionError: "caldav_connection_error"},
    disclose_messages_for=frozenset(),  # label it, but keep the message redacted
)
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
- [Handling Errors (guide)](../guides/error-types.md) — fix examples for each
  error class, and how to use `build_error_payload()` with custom exceptions
- [ADR-011](../adr/ADR-011-error-handling-and-publishing.md) — architecture
  decision record
- [ADR-061](../adr/ADR-061-decoupled-error-message-disclosure.md) — decoupled
  `disclose_messages_for` message-disclosure policy
