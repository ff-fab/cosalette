---
icon: material/alert-outline
---

# Handling Errors

This guide covers two topics: **fixing framework errors** when cosalette raises a
registration or runtime exception, and **customizing error classification** using
`build_error_payload()` with domain-specific exceptions.

For the complete error catalog (message text, location, cause), see
[Error Taxonomy](../reference/errors.md).

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## Fixing Framework Errors

### Decorator Parentheses

Always use parentheses on `@app.device` and `@app.command`, even with no arguments:

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

### Async `init` Callback

The `init=` callback runs during synchronous bootstrap — it cannot be `async`:

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

### `init` Result Shadows Injectable

Wrap the return value in a domain-specific type instead of returning a
framework type (`AppContext`, `MqttPort`, etc.) directly.

### Bool Parameters (Type Guard)

Pass a numeric literal, not a boolean:

```python
# Wrong
Pt1Filter(tau=True, dt=0.1)   # TypeError

# Correct
Pt1Filter(tau=1.0, dt=0.1)
```

Similarly, `MedianFilter(window=5)` — pass an integer, not `True`/`False`.

### Handler Annotations

Every handler parameter must have a concrete type annotation:

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

### `Depends()` — Async Dependency

Dependencies must be synchronous — the framework cannot `await` them while
building handler kwargs:

```python
# Wrong — async dependency
async def get_client() -> Client: ...

# Wrong — async __call__
class GetClient:
    async def __call__(self) -> Client: ...

# Correct — sync dependency
def get_client(ctx: DeviceContext) -> Client:
    return ctx.adapter(ClientPort)
```

### Adapter `__aenter__` Not Callable

Ensure the adapter is a proper async context manager with a callable
`__aenter__` method.

### Negative or Zero Intervals

Pass a positive value:

```python
# Wrong
app = App(heartbeat_interval=-5)   # ValueError
app = App(heartbeat_interval=0)    # ValueError

# Correct
app = App(heartbeat_interval=30)
```

### Duplicate Registration

Use distinct names for each device. Register only one adapter per port type:

```python
@app.device(name="temperature")
async def temp_device(ctx: DeviceContext) -> dict[str, object]:
    ...

@app.device(name="humidity")   # Different name
async def humidity_device(ctx: DeviceContext) -> dict[str, object]:
    ...
```

### Invalid Adapter Tuple

Pass either a single adapter instance or a `(impl, dry_run)` 2-tuple:

```python
# Single adapter
app = App(adapters={MqttPort: my_mqtt_client})

# Adapter + dry-run pair
app = App(adapters={MqttPort: (my_mqtt_client, null_mqtt_client)})
```

### Empty Group Name

Pass a non-empty string for the `group` parameter on `@app.device` or
`@app.command`.

### Persist Without Store

Either remove `store=None` (to use the auto-resolved default store), or pass
a store backend explicitly:

```python
from cosalette import App, MemoryStore, SaveOnPublish

# Option A: use the auto-resolved default store (omit store=)
app = App(name="myapp", version="1.0.0")

# Option B: pass an explicit store
app = App(name="myapp", version="1.0.0", store=MemoryStore())

@app.telemetry("sensor", interval=60, persist=SaveOnPublish())
async def sensor() -> dict[str, object]:
    return {"value": 42}
```

### `Every()` — Mutual Exclusion

Specify exactly one of `seconds` or `n`:

```python
# Wrong
Every(seconds=5, n=10)  # ValueError — both specified
Every()                  # ValueError — neither specified

# Correct
Every(seconds=5)
Every(n=10)
```

### Composite Policy and Strategy Children

`AnySavePolicy`, `AllSavePolicy`, `AnyStrategy`, and `AllStrategy` all require
at least one child argument. Pass at least one child policy or strategy.

### Import Path Format

Use the `module.path:attr_name` colon-separated format:
`"mypackage.module:MyClass"`.

### Unresolved Provider (Runtime)

Register an implementation for the port before the app starts:

```python
app.adapter(SensorPort, Bme280Adapter)
```

### Awaitable Dependency Result (Runtime)

Return a plain value from a `Depends()` callable and await inside the handler
instead.

### Circular Dependency (Runtime)

Break the cycle — extract the shared value into a third dependency that neither
side depends on.

### Topic Marker and Message Type (Runtime) {#request-context-fixes}

`Topic()` and `Message` require an active MQTT request context:

- Use `Topic()` only in `@app.command` handlers or triggered telemetry that is
  certain to have an incoming message.
- Use `Message` only in `@app.command` handlers where an inbound MQTT message
  is guaranteed.

For scheduled (non-triggered) telemetry, guard with a conditional or restructure
to pass the topic via a different mechanism.

### Settings Unavailable (Runtime)

Set the required environment variables before running the app, or use
`app.cli()` with `--env-file` to load them from a file.

### aiomqtt Not Installed (Runtime)

Install the MQTT extra:

```bash
pip install cosalette[mqtt]
# or
uv add cosalette[mqtt]
```

### Adapter Not Found (Runtime)

Register the required adapter when constructing the app:

```python
app = App(adapters={MqttPort: my_mqtt_client})
```

## Customizing Error Classification

The rest of this guide covers how to use `build_error_payload()` to create
domain-specific error types for your own exception handling code. The framework's
built-in error isolation always publishes with `error_type="error"` — custom
classification lets you distinguish error categories in downstream subscribers.

!!! warning "Labeling an exception type also discloses its message (legacy default)"

    Registering an exception type in `error_type_map` (via `App(error_type_map=...)`)
    both assigns its `error_type` label **and** — by default — opts its raw
    `str(error)` into publication on broker-visible error topics. If you want a
    readable label without disclosing the message, pass `App(disclose_messages_for=...)`
    to decouple the two decisions (F-DP1, [ADR-061](../adr/ADR-061-decoupled-error-message-disclosure.md)).
    See [Message Disclosure](../concepts/error-handling.md#message-disclosure) for details.

## How Error Publication Works

When a device function (telemetry or command) raises an exception:

1. The framework catches it (except `CancelledError`).
2. Builds a structured `ErrorPayload`.
3. Publishes to **two** MQTT topics:
    - `{prefix}/error` — global error topic (all errors from all devices)
    - `{prefix}/{device}/error` — per-device error topic
4. **Continues running** — the error is fire-and-forget. Publication failures are
   logged but never propagated.

```text
Exception raised in "counter" device
    ↓
Framework catches it, builds ErrorPayload (error_type="error")
    ↓
Publish to gas2mqtt/error         (not retained, QoS 1)
Publish to gas2mqtt/counter/error (not retained, QoS 1)
    ↓
Device loop continues
```

!!! info "Framework vs manual error building"

    The framework's automatic error isolation always publishes with
    `error_type="error"`. To get domain-specific types like `"sensor_timeout"`,
    use `build_error_payload()` in your own try/except blocks and publish
    manually via `ctx.publish("error", payload.to_json(), retain=False)`.

## The ErrorPayload Structure

Every error is published as a JSON object with this schema:

```json title="ErrorPayload example"
{
    "error_type": "sensor_timeout",
    "message": "Serial read timed out after 5s",
    "device": "counter",
    "timestamp": "2026-02-18T10:30:00+00:00",
    "details": {}
}
```

| Field        | Type             | Description                                       |
| ------------ | ---------------- | ------------------------------------------------- |
| `error_type` | `str`            | Machine-readable error classification              |
| `message`    | `str`            | Human-readable description (`str(exception)`)      |
| `device`     | `str | null`     | Device name, or `null` for non-device errors       |
| `timestamp`  | `str` (ISO 8601) | When the error occurred                            |
| `details`    | `dict`           | Additional context (empty by default)              |

## Step 1: Define Domain Exceptions

Create exception classes for your domain errors:

```python title="errors.py"
"""Domain exceptions for gas2mqtt."""


class SensorTimeoutError(Exception):
    """Raised when the gas meter sensor doesn't respond in time."""


class InvalidReadingError(Exception):
    """Raised when a sensor reading is outside valid bounds."""


class CalibrationError(Exception):
    """Raised when the sensor reports calibration failure."""
```

!!! tip "Exception design"

    Keep exceptions **specific and descriptive**. Each class should represent one
    category of failure. Use the exception message for the instance-specific details
    (e.g. which reading failed, what the timeout was).

## Step 2: Build the Error Type Map

The error type map is a dict mapping exception classes to machine-readable strings:

```python title="errors.py"
error_type_map: dict[type[Exception], str] = {
    SensorTimeoutError: "sensor_timeout",
    InvalidReadingError: "invalid_reading",
    CalibrationError: "calibration_error",
}
```

!!! warning "Exact class match — no subclass matching"

    The error type map uses **exact class match** (`type(error)` lookup, not
    `isinstance()`). If you raise `SensorTimeoutError` and the map contains
    `SensorTimeoutError`, it matches. But if you raise a _subclass_ of
    `SensorTimeoutError`, it falls back to the default `"error"` type.

    This is intentional — it keeps the mapping simple and explicit
    ([ADR-011](../adr/ADR-011-error-handling-and-publishing.md)).

## Step 3: Use build_error_payload()

The `build_error_payload()` function converts an exception into an `ErrorPayload`:

```python title="Usage example"
from cosalette import build_error_payload

error = SensorTimeoutError("Serial read timed out after 5s")

payload = build_error_payload(
    error,
    error_type_map=error_type_map,
    device="counter",
)

print(payload.error_type)  # "sensor_timeout"
print(payload.message)     # "Serial read timed out after 5s"
print(payload.device)      # "counter"
print(payload.to_json())   # Full JSON string
```

### Function Signature

```python
def build_error_payload(
    error: Exception,
    *,
    error_type_map: dict[type[Exception], str] | None = None,
    device: str | None = None,
    details: dict[str, object] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ErrorPayload: ...
```

| Parameter        | Description                                                     |
| ---------------- | --------------------------------------------------------------- |
| `error`          | The exception to convert                                        |
| `error_type_map` | Mapping from exception types to type strings                    |
| `device`         | Device name to include in the payload                           |
| `details`        | Additional context dict                                         |
| `clock`          | Optional callable for deterministic timestamps (testing)        |

## Default Fallback

Unmapped exceptions get the generic `"error"` type:

```python title="Fallback behaviour"
payload = build_error_payload(
    RuntimeError("something broke"),
    error_type_map=error_type_map,
    device="counter",
)

print(payload.error_type)  # "error" — not in the map, so falls back
```

This ensures every exception produces a valid payload — no exception is ever
silently dropped.

## Dual Publication

The framework publishes each error to **two** topics:

| Topic                           | Purpose                              | Retained |
| ------------------------------- | ------------------------------------ | -------- |
| `{prefix}/error`                | Global — all errors from all devices | No       |
| `{prefix}/{device}/error`       | Per-device — filtered by source      | No       |

Errors are published with `retain=False` and `qos=1`:

- **Not retained** because errors are events, not last-known state. You don't want
  a stale error payload lingering as the retained message.
- **QoS 1** (at-least-once) for reliability — error reports should reach subscribers.

## Fire-and-Forget Semantics

Error publication never crashes the daemon. If publishing itself fails (e.g. MQTT
broker is down), the failure is logged but **not propagated**:

```text
# If the MQTT publish fails:
ERROR    Failed to publish error to gas2mqtt/error
# But the device loop continues — the daemon stays up
```

This is a deliberate design choice
([ADR-011](../adr/ADR-011-error-handling-and-publishing.md)): error _reporting_ must
never be the cause of a daemon crash. The framework wraps the entire build → serialise
→ publish pipeline in a try/except.

## Practical Example: Gas Meter Error Types

A complete example with domain exceptions for a gas meter bridge:

```python title="errors.py"
"""Domain exceptions and error type map for gas2mqtt."""


class SensorTimeoutError(Exception):
    """Gas meter sensor didn't respond within the timeout period."""


class InvalidReadingError(Exception):
    """Sensor returned a reading outside valid physical bounds."""


class ConnectionLostError(Exception):
    """Serial connection to the gas meter was lost."""


# Machine-readable error classification
error_type_map: dict[type[Exception], str] = {
    SensorTimeoutError: "sensor_timeout",
    InvalidReadingError: "invalid_reading",
    ConnectionLostError: "connection_lost",
}
```

```python title="app.py"
"""gas2mqtt — telemetry device with custom error types."""

import cosalette
from gas2mqtt.errors import InvalidReadingError, SensorTimeoutError
from gas2mqtt.ports import GasMeterPort

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)

    try:
        impulses = meter.read_impulses()
    except TimeoutError as exc:
        raise SensorTimeoutError(f"Read timed out: {exc}") from exc  # (1)!

    if impulses < 0:
        raise InvalidReadingError(  # (2)!
            f"Negative impulse count: {impulses}"
        )

    return {"impulses": impulses}


app.run()
```

1. Wrap low-level exceptions in domain exceptions. When the framework catches
   these, it publishes with the generic `"error"` type — but the domain exception
   class name appears in the `message` field for debugging.
2. Validate readings and raise domain exceptions for invalid data. The framework
   catches these, publishes the error, and continues the polling loop.

The framework's automatic error publication produces payloads like:

```json title="Framework auto-published error (generic type)"
{
    "error_type": "error",
    "message": "Read timed out: Serial read timeout",
    "device": "counter",
    "timestamp": "2026-02-18T10:30:00+00:00",
    "details": {}
}
```

To get domain-specific `error_type` values, use `build_error_payload()` with your
error type map in a manual try/except — see Step 3 above for the API.

=== "With error_type_map (manual)"

    ```json
    {
        "error_type": "sensor_timeout",
        "message": "Read timed out: Serial read timeout",
        "device": "counter",
        "timestamp": "2026-02-18T10:30:00+00:00",
        "details": {}
    }
    ```

=== "Framework auto-published"

    ```json
    {
        "error_type": "error",
        "message": "Read timed out: Serial read timeout",
        "device": "counter",
        "timestamp": "2026-02-18T10:30:00+00:00",
        "details": {}
    }
    ```

=== "Unmapped (manual)"

    ```json
    {
        "error_type": "error",
        "message": "something unexpected",
        "device": "counter",
        "timestamp": "2026-02-18T10:30:10+00:00",
        "details": {}
    }
    ```

## Testing Error Payloads

Test your error type map with plain unit tests:

```python title="tests/unit/test_errors.py"
"""Unit tests for gas2mqtt error types.

Test Techniques Used:
- Decision Table: Exception class → error_type string mapping.
- Specification-based: Verify ErrorPayload structure.
"""

from cosalette import build_error_payload
from gas2mqtt.errors import (
    InvalidReadingError,
    SensorTimeoutError,
    error_type_map,
)


def test_sensor_timeout_maps_correctly():
    """SensorTimeoutError maps to 'sensor_timeout'."""
    payload = build_error_payload(
        SensorTimeoutError("timed out"),
        error_type_map=error_type_map,
        device="counter",
    )

    assert payload.error_type == "sensor_timeout"
    assert payload.device == "counter"
    assert "timed out" in payload.message


def test_unmapped_exception_falls_back():
    """Unmapped exceptions get the default 'error' type."""
    payload = build_error_payload(
        RuntimeError("unexpected"),
        error_type_map=error_type_map,
    )

    assert payload.error_type == "error"


def test_error_payload_serialises_to_json():
    """ErrorPayload.to_json() produces valid JSON."""
    import json

    payload = build_error_payload(
        InvalidReadingError("bad value"),
        error_type_map=error_type_map,
        device="counter",
    )

    data = json.loads(payload.to_json())
    assert data["error_type"] == "invalid_reading"
    assert data["device"] == "counter"
```

---

## See Also

- [Error Taxonomy](../reference/errors.md) — complete catalog of all framework errors (message text, location, cause)
- [Error Handling](../concepts/error-handling.md) — conceptual overview of the error
  publication system
- [MQTT Topics](../concepts/mqtt-topics.md) — topic layout for error channels
- [ADR-011](../adr/ADR-011-error-handling-and-publishing.md) — error handling and
  publishing decisions
