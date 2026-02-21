---
icon: material/alert-outline
---

# Map Custom Error Types

When your cosalette app encounters an error, the framework publishes a structured JSON
payload to MQTT error topics. The framework's built-in error isolation always uses the
generic `"error"` type. This guide shows you how to use `build_error_payload()` to
create domain-specific error classifications for your own error handling code.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

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

- [Error Handling](../concepts/error-handling.md) — conceptual overview of the error
  publication system
- [MQTT Topics](../concepts/mqtt-topics.md) — topic layout for error channels
- [ADR-011](../adr/ADR-011-error-handling-and-publishing.md) — error handling and
  publishing decisions
