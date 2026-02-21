---
icon: material/remote
---

# Build a Command & Control Device

Command & control devices are bidirectional — they receive commands via MQTT _and_
publish state back. The `@app.command()` decorator is the recommended way to build
command devices: you write a simple function, the framework handles subscription,
dispatch, error isolation, and state publication.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## How `@app.command` Works

The `@app.command` decorator registers a **standalone async function** that:

1. **Receives MQTT message values by name** — parameters named `topic` and
   `payload` are **optional** and injected only if declared in the handler
   signature. Declare only what you need.
2. **Receives dependencies by type annotation** — all other parameters (e.g.
   `ctx: DeviceContext`, adapters) are injected by matching their type.
3. **Auto-publishes state** — if the handler returns a `dict`, the framework
   JSON-serialises it and publishes to `{prefix}/{name}/state` with `retain=True`
   and `qos=1`. Return `None` to skip auto-publishing.
4. **Is error-isolated** — if the handler raises an exception, the framework
   catches it, logs at ERROR level, and publishes a structured error payload to
   the error topics. Other devices are unaffected.

The framework subscribes to `{prefix}/{name}/set` and dispatches inbound messages
to your handler function.

!!! info "Which pattern to use"

    | Pattern                     | Use when                                                    |
    | --------------------------- | ----------------------------------------------------------- |
    | `@app.command(name)`        | Device reacts to MQTT commands. Simplest, most common.      |
    | `@app.telemetry(name, interval=N)` | Device polls/streams data on an interval.            |
    | `@app.device(name)`        | Complex lifecycle — custom loops, state machines. Escape hatch. |

    See [Device Archetypes](../concepts/device-archetypes.md) for the full picture.

!!! tip "Why command devices always use DeviceContext"

    Unlike telemetry handlers (which can be zero-arg), command devices typically
    request `ctx: DeviceContext` because they need `ctx.publish_state()`,
    `ctx.on_command`, and `ctx.sleep()`. Other injectable types (`Settings`,
    `logging.Logger`, `ClockPort`, adapter ports) are also available via
    signature-based injection — but `DeviceContext` bundles them all for the
    bidirectional use case.

## A Minimal Command Device

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.command("valve")  # (1)!
async def handle_valve(payload: str) -> dict[str, object]:  # (2)!
    """Handle valve commands."""
    return {"state": payload}  # (3)!


app.run()
```

1. `"valve"` is the device name — the framework subscribes to
   `gas2mqtt/valve/set` for inbound commands.
2. `payload` is injected by name from the MQTT message. The return type is
   `dict[str, object] | None`. Returning a dict triggers auto-publishing.
3. The returned dict is published as `{"state": "open"}` (or whatever
   `payload` contains) to `gas2mqtt/valve/state` with `retain=True`, `qos=1`.

!!! tip "Declare only the MQTT params you need"

    The handler above only declares `payload`. You can also declare `topic: str`
    to receive the full MQTT topic string, add `ctx: cosalette.DeviceContext`
    for framework services, or omit both MQTT params entirely:

    ```python
    # payload only (most common)
    async def handle(payload: str) -> dict[str, object]: ...

    # payload + topic
    async def handle(topic: str, payload: str) -> dict[str, object]: ...

    # payload + context
    async def handle(payload: str, ctx: cosalette.DeviceContext) -> dict[str, object]: ...

    # no MQTT params — side-effect only, uses adapter
    async def handle(ctx: cosalette.DeviceContext) -> dict[str, object]: ...
    ```

When you run this, the framework:

- Connects to the MQTT broker.
- Subscribes to `gas2mqtt/valve/set`.
- Dispatches each inbound message to `handle_valve()`.
- Publishes the returned dict as JSON to `gas2mqtt/valve/state`.
- Keeps running until `SIGTERM` or `SIGINT`.

## Single-Device Apps (Root Device)

For apps with a single command device, omit the name to publish at the
root level:

```python title="app.py"
import cosalette

app = cosalette.App(name="relay2mqtt", version="1.0.0")


@app.command()  # (1)!
async def handle(payload: str) -> dict[str, object]:
    """Control the relay."""
    return {"state": payload}


app.run()
```

1. No device name — subscribes to `relay2mqtt/set` and publishes state
   to `relay2mqtt/state`.

The same root device rules apply as for telemetry — see
[Single-Device Apps](telemetry-device.md#single-device-apps-root-device)
for details on naming, topics, and constraints.

## The Command Handler

An `@app.command` handler is a plain `async def` function with two kinds of
parameters:

- **`topic`** and **`payload`** (by **name**, both **optional**) — the full
  MQTT topic string (e.g. `gas2mqtt/valve/set`) and the raw message payload
  string. Declare only the ones your handler needs — the framework inspects the
  function signature at registration time and injects only what is declared.
- **Everything else** (by **type annotation**) — injected automatically.
  `ctx: cosalette.DeviceContext` is the most common, but adapters work too.

```python title="Handler with validation"
@app.command("valve")
async def handle_valve(payload: str) -> dict[str, object] | None:
    valid_commands = {"open", "close", "toggle"}

    if payload not in valid_commands:
        raise ValueError(f"Unknown command: {payload!r}")  # (1)!

    return {"state": payload}
```

1. Raising inside the command handler is safe. The framework catches handler
   exceptions, logs them at ERROR level, and publishes a structured error
   payload. Other devices and subsequent commands continue normally.

### Return Value Contract

| Return value  | Framework behaviour                                               |
| ------------- | ----------------------------------------------------------------- |
| `dict`        | JSON-serialised and published to `{prefix}/{name}/state`          |
| `None`        | No state publication — use when you publish manually or conditionally |

## Using DeviceContext

The `DeviceContext` gives you access to shared infrastructure without globals:

```python title="app.py"
@app.command("valve")
async def handle_valve(
    payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    settings = ctx.settings          # (1)!
    device_name = ctx.name           # (2)!
    clock_value = ctx.clock.now()    # (3)!

    return {"state": payload, "updated_at": clock_value}
```

1. Access the application `Settings` instance (or your custom subclass).
2. The device name as registered — `"valve"` in this case.
3. The monotonic clock port — useful for timing calculations. In tests, this is a
   `FakeClock` you control directly.

!!! warning "DeviceContext vs AppContext"

    Command and device functions receive `DeviceContext`, which has publish, sleep,
    and adapter capabilities. Lifecycle hooks receive `AppContext`, which only has
    `.settings` and `.adapter()`. Don't mix them up — see
    [Lifespan](lifespan.md) for details.

### DeviceContext API

`@app.command` handlers can use a subset of the `DeviceContext` surface (the parts
relevant to per-message handling):

| Property / Method           | Description                                        |
| --------------------------- | -------------------------------------------------- |
| `ctx.name`                  | Device name as registered (`"valve"`)              |
| `ctx.settings`              | Application `Settings` instance                    |
| `ctx.clock`                 | Monotonic clock port                               |
| `ctx.adapter(PortType)`     | Resolve a registered adapter                       |
| `ctx.publish_state(dict)`   | Publish to `{prefix}/{name}/state` (retained) — manual override |
| `ctx.publish(channel, str)` | Publish to `{prefix}/{name}/{channel}` (arbitrary) |

!!! tip "Auto-publish vs manual publish"

    For most command handlers, simply return a dict and let the framework publish.
    Use `ctx.publish_state()` directly only when you need side-effect publishing
    (e.g. publishing to multiple channels) _and_ return `None` to skip the
    auto-publish.

## Resolving Adapters

When your command device needs hardware access, use the adapter pattern:

```python title="app.py"
from gas2mqtt.ports import RelayPort

@app.command("valve")
async def handle_valve(
    payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    relay = ctx.adapter(RelayPort)  # (1)!

    match payload:
        case "on":
            relay.turn_on()
        case "off":
            relay.turn_off()
        case _:
            raise ValueError(f"Unknown command: {payload!r}")

    return {"state": payload}
```

1. Resolves the adapter registered for `RelayPort`. Raises `LookupError` if no
   adapter is registered. See [Hardware Adapters](adapters.md) for registration.

## Stateful Command Handlers

For most command handlers, the return-dict pattern is sufficient — state is
derived from the payload and returned directly. When you need to track state
across multiple commands (e.g. toggle), use a module-level variable or a small
state class:

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")

_valve_state = "closed"  # (1)!


@app.command("valve")
async def handle_valve(
    payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    global _valve_state  # (2)!

    match payload:
        case "open":
            _valve_state = "open"
        case "close":
            _valve_state = "closed"
        case "toggle":
            _valve_state = "open" if _valve_state == "closed" else "closed"
        case _:
            raise ValueError(f"Unknown command: {payload!r}")

    return {"state": _valve_state}


app.run()
```

1. Module-level state variable — simple and visible.
2. `global` lets the handler mutate module-level state.

!!! tip "When state gets complex"

    For devices with many state variables or complex transitions, extract a
    dataclass or a small state class. For devices that need background loops,
    periodic state refresh, or combined command + telemetry behaviour, use
    `@app.device` — see [Advanced: When to Use `@app.device`](#advanced-when-to-use-appdevice)
    below.

## Practical Example: WiFi Smart Plug

A complete command device for a WiFi relay (smart plug) with on/off/toggle support:

```python title="app.py"
"""gas2mqtt — Smart plug (relay) command device."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import cosalette


@runtime_checkable
class RelayPort(Protocol):
    """Hardware abstraction for a relay switch."""

    def turn_on(self) -> None: ...
    def turn_off(self) -> None: ...
    def is_on(self) -> bool: ...


app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.command("plug")  # (1)!
async def handle_plug(
    payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    """Control a smart plug relay via MQTT commands."""
    relay = ctx.adapter(RelayPort)

    match payload:
        case "on":
            relay.turn_on()
        case "off":
            relay.turn_off()
        case "toggle":
            if relay.is_on():
                relay.turn_off()
            else:
                relay.turn_on()
        case _:
            raise ValueError(
                f"Unknown command: {payload!r}. "
                f"Valid: on, off, toggle"
            )

    state = "on" if relay.is_on() else "off"
    return {"state": state}  # (2)!


app.run()
```

1. `@app.command("plug")` — no closure, no main loop, no `nonlocal`. Just a
   function that receives a command and returns state.
2. The returned dict is auto-published to `gas2mqtt/plug/state`.

**MQTT interaction:**

=== "Command → State"

    ```text
    → gas2mqtt/plug/set       "on"
    ← gas2mqtt/plug/state     {"state": "on"}
    ```

=== "Toggle"

    ```text
    → gas2mqtt/plug/set       "toggle"
    ← gas2mqtt/plug/state     {"state": "off"}
    ```

=== "Invalid Command"

    ```text
    → gas2mqtt/plug/set       "blink"
    ← gas2mqtt/error           {"error_type": "error", "message": "Unknown command: 'blink'..."}
    ← gas2mqtt/plug/error     {"error_type": "error", "message": "Unknown command: 'blink'..."}
    ```

## Error Behaviour

When an `@app.command` handler raises an exception:

1. The framework catches it (except `CancelledError`).
2. Logs the error at `ERROR` level.
3. Publishes a structured error payload to `{prefix}/error` and
   `{prefix}/{name}/error`.
4. **Continues normally** — subsequent commands are dispatched as usual.
   Other devices are unaffected.

```python title="Example error flow"
@app.command("valve")
async def handle_valve(payload: str) -> dict[str, object]:
    if payload not in {"open", "close"}:
        raise ValueError(f"Invalid command: {payload!r}")  # (1)!
    return {"state": payload}
```

1. The framework catches `ValueError`, publishes the error, and continues
   listening for the next command. No crash, no restart needed.

!!! tip "Custom error types"

    For machine-readable error classification, define an `error_type_map`. See
    [Custom Error Types](error-types.md) for details.

!!! tip "Validate early"

    Check command payloads at the top of your handler and raise with a descriptive
    message. This gives consumers clear error feedback via the MQTT error topic.

## Advanced: When to Use `@app.device`

`@app.command` covers most command device use cases. Reach for `@app.device` when
you need capabilities that a per-message handler cannot provide:

| Need                               | Use                        |
| ---------------------------------- | -------------------------- |
| Simple command → state             | `@app.command` ✓           |
| Command with hardware adapter      | `@app.command` ✓           |
| Periodic state refresh (hardware polling) | `@app.device` — needs a `while` loop |
| Custom event loops or state machines | `@app.device` — owns the coroutine |
| Combined command + telemetry in one device | `@app.device` — manual control |
| Background work between commands   | `@app.device` — long-running task   |

### `@app.device` + `@ctx.on_command` Pattern

The `@app.device` decorator registers a **long-running coroutine** that owns its
main loop. Use `@ctx.on_command` inside the closure to handle inbound commands:

```python title="app.py"
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    state = "closed"

    @ctx.on_command  # (1)!
    async def handle(topic: str, payload: str) -> None:
        nonlocal state  # (2)!
        state = payload
        await ctx.publish_state({"state": state})  # (3)!

    await ctx.publish_state({"state": state})  # (4)!

    while not ctx.shutdown_requested:  # (5)!
        await ctx.sleep(30)
```

1. `@ctx.on_command` registers the handler. Only one handler per device.
2. `nonlocal` lets the inner function mutate the enclosing scope's `state`.
3. Manual publish — `@app.device` does not auto-publish.
4. Publish initial state immediately.
5. The sleep loop keeps the coroutine alive.

!!! warning "One handler per device"

    Each `@app.device` can register exactly **one** command handler via
    `@ctx.on_command`. A second call raises `RuntimeError`. Dispatch on the
    payload inside a single handler if you need multiple command types.

### Periodic State Refresh with `@app.device`

When a device needs to poll hardware between commands:

```python title="app.py"
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    controller = ctx.adapter(ValveControllerPort)
    state = controller.read_state()

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        controller.actuate(payload)
        state = controller.read_state()
        await ctx.publish_state({"state": state})

    await ctx.publish_state({"state": state})

    while not ctx.shutdown_requested:
        await ctx.sleep(10)  # (1)!
        state = controller.read_state()
        await ctx.publish_state({"state": state})
```

1. Every 10 seconds, re-read hardware and publish updated state. This catches
   out-of-band changes (e.g. someone pressing a physical button on the valve).

### Error Handling in `@app.device`

Errors can occur in two places:

1. **In the command handler** — The framework's command proxy catches the exception
   and publishes a structured error payload via `ErrorPublisher` (fire-and-forget).
   The device loop continues unaffected.
2. **In the main loop** — if the device coroutine crashes, the framework catches
   the exception, logs it, and publishes an error. The device task ends, but other
   devices continue.

## Migration: `@ctx.on_command` → `@app.command()`

If you have existing devices using `@app.device` + `@ctx.on_command`, migrating
to `@app.command` is straightforward:

=== "Before (`@app.device`)"

    ```python
    @app.device("valve")
    async def valve(ctx: cosalette.DeviceContext) -> None:
        state = "closed"

        @ctx.on_command
        async def handle(topic: str, payload: str) -> None:
            nonlocal state
            state = payload
            await ctx.publish_state({"state": state})

        await ctx.publish_state({"state": state})
        while not ctx.shutdown_requested:
            await ctx.sleep(30)
    ```

=== "After (`@app.command`)"

    ```python
    @app.command("valve")
    async def handle_valve(payload: str) -> dict[str, object]:
        return {"state": payload}
    ```

**What changes:**

1. Replace `@app.device("valve")` with `@app.command("valve")`.
2. Declare only the MQTT params you need (`payload`, `topic`, or both) as
   function parameters — they are optional.
3. Add any injected dependencies (like `ctx`) as additional parameters with
   type annotations, if needed.
4. Return a `dict` instead of calling `ctx.publish_state()` — the framework
   publishes automatically.
5. Remove the `while` loop, `nonlocal`, and `@ctx.on_command` — they are no
   longer needed.

**What stays the same:**

- Device name and MQTT topics are unchanged.
- Error isolation behaviour is identical.
- The `@app.device` + `@ctx.on_command` pattern continues to work — backward
  compatibility is maintained.

---

## See Also

- [Device Archetypes](../concepts/device-archetypes.md) — command vs telemetry
  vs device archetypes
- [Telemetry Device](telemetry-device.md) — deep dive into `@app.telemetry`
- [MQTT Topics](../concepts/mqtt-topics.md) — topic layout for commands and state
- [Error Handling](../concepts/error-handling.md) — how the framework isolates errors
- [ADR-010](../adr/ADR-010-device-archetypes.md) — the decision behind device
  archetypes
- [ADR-002](../adr/ADR-002-mqtt-topic-conventions.md) — MQTT topic conventions
