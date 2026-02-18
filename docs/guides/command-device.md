---
icon: material/remote
---

# Build a Command & Control Device

Command & control devices are bidirectional — they receive commands via MQTT _and_
publish state back. Unlike telemetry devices (which return a dict and let the framework
publish), command devices own their main loop and publish manually.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## How Command Devices Work

The `@app.device` decorator registers a **long-running coroutine** that:

1. Receives a `DeviceContext` with full publish/subscribe capabilities.
2. Registers a command handler via `@ctx.on_command`.
3. Runs a `while not ctx.shutdown_requested` loop, publishing state as needed.
4. Is **error-isolated** — if the coroutine crashes, the framework catches the
   exception, publishes an error payload, and the task ends gracefully without
   taking down other devices.

The framework subscribes to `{prefix}/{name}/set` and routes inbound messages to
your `@ctx.on_command` handler.

!!! info "Device vs Telemetry — when to use which"

    | Feature           | `@app.telemetry`               | `@app.device`                    |
    | ----------------- | ------------------------------ | -------------------------------- |
    | Main loop         | Framework-managed              | You write it                     |
    | Publication       | Return a dict                  | Call `ctx.publish_state()`       |
    | Inbound commands  | Not supported                  | `@ctx.on_command`                |
    | Use case          | Read-only sensors              | Actuators, bidirectional devices |

    See [Device Archetypes](../concepts/device-archetypes.md) for the full picture.

## A Minimal Command Device

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.device("valve")  # (1)!
async def valve(ctx: cosalette.DeviceContext) -> None:
    state = "closed"

    @ctx.on_command  # (2)!
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        state = payload  # (3)!
        await ctx.publish_state({"state": state})  # (4)!

    await ctx.publish_state({"state": state})  # (5)!

    while not ctx.shutdown_requested:  # (6)!
        await ctx.sleep(30)


app.run()
```

1. `"valve"` is the device name. The framework subscribes to `gas2mqtt/valve/set`
   for inbound commands.
2. `@ctx.on_command` registers the handler. Only one handler per device — a second
   call raises `RuntimeError`.
3. The `payload` is the raw MQTT payload string. Parse/validate it as needed.
4. `ctx.publish_state()` publishes to `gas2mqtt/valve/state` with `retain=True`,
   `qos=1`.
5. Publish initial state so Home Assistant (or any consumer) knows the current
   value immediately.
6. The sleep loop keeps the coroutine alive. `ctx.sleep()` is shutdown-aware — it
   returns early when a shutdown signal arrives.

## The Command Handler

The `@ctx.on_command` decorator registers a callback with the signature
`async def(topic: str, payload: str) -> None`:

- **`topic`** — the full MQTT topic (e.g. `gas2mqtt/valve/set`).
- **`payload`** — the raw message payload as a string.

```python title="Handler with validation"
@ctx.on_command
async def handle(topic: str, payload: str) -> None:
    valid_commands = {"open", "close", "toggle"}

    if payload not in valid_commands:
        raise ValueError(f"Unknown command: {payload!r}")  # (1)!

    if payload == "toggle":
        nonlocal state
        state = "open" if state == "closed" else "closed"
    else:
        state = payload

    await ctx.publish_state({"state": state})
```

1. Raising inside the command handler is safe. The framework's `TopicRouter` invokes
   the handler — exceptions are logged at ERROR level but don't crash the device loop.

!!! warning "One handler per device"

    Each device can register exactly **one** command handler. Calling `@ctx.on_command`
    a second time raises `RuntimeError`. If you need to handle multiple command types,
    dispatch on the payload inside a single handler.

## DeviceContext API

Command devices use the full `DeviceContext` surface:

| Property / Method           | Description                                        |
| --------------------------- | -------------------------------------------------- |
| `ctx.name`                  | Device name as registered (`"valve"`)              |
| `ctx.settings`              | Application `Settings` instance                    |
| `ctx.clock`                 | Monotonic clock port                               |
| `ctx.shutdown_requested`    | `True` when shutdown signal received               |
| `ctx.publish_state(dict)`   | Publish to `{prefix}/{name}/state` (retained)      |
| `ctx.publish(channel, str)` | Publish to `{prefix}/{name}/{channel}` (arbitrary) |
| `ctx.sleep(seconds)`        | Shutdown-aware sleep                               |
| `ctx.on_command`            | Register inbound command handler                   |
| `ctx.adapter(PortType)`     | Resolve a registered adapter                       |

## Stateful Device Pattern

Most command devices track internal state. The pattern uses a `nonlocal` variable
in the command handler closure:

```python title="app.py"
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    state = "closed"  # (1)!

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state  # (2)!

        match payload:
            case "open":
                state = "open"
            case "close":
                state = "closed"
            case "toggle":
                state = "open" if state == "closed" else "closed"
            case _:
                raise ValueError(f"Unknown command: {payload!r}")

        await ctx.publish_state({"state": state})

    # Publish initial state
    await ctx.publish_state({"state": state})

    while not ctx.shutdown_requested:
        await ctx.sleep(30)
```

1. State starts as a local variable before the handler is defined.
2. `nonlocal` lets the inner function mutate the enclosing scope's `state`.

!!! tip "Why `nonlocal` instead of a class?"

    The closure pattern keeps everything in one function scope — no extra class,
    no `self` parameter, no mutable state objects. For simple devices this is
    idiomatic and readable. For complex state machines, consider extracting a
    dataclass or a small state class.

## Periodic State Refresh

Some devices need to periodically re-read hardware state, not just respond to
commands. Combine the command handler with a polling loop:

```python title="app.py"
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    controller = ctx.adapter(ValveControllerPort)
    state = controller.read_state()  # Read initial hardware state

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        match payload:
            case "open":
                controller.actuate("open")
            case "close":
                controller.actuate("close")
            case _:
                raise ValueError(f"Unknown command: {payload!r}")
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


@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    """Control a gas valve relay via MQTT commands."""
    relay = ctx.adapter(RelayPort)
    state = "on" if relay.is_on() else "off"

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        match payload:
            case "on":
                relay.turn_on()
                state = "on"
            case "off":
                relay.turn_off()
                state = "off"
            case "toggle":
                if state == "on":
                    relay.turn_off()
                    state = "off"
                else:
                    relay.turn_on()
                    state = "on"
            case _:
                raise ValueError(
                    f"Unknown command: {payload!r}. "
                    f"Valid: on, off, toggle"
                )

        await ctx.publish_state({"state": state})

    # Publish initial state on startup
    await ctx.publish_state({"state": state})

    # Keep alive — re-read hardware periodically
    while not ctx.shutdown_requested:
        await ctx.sleep(30)
        actual = "on" if relay.is_on() else "off"
        if actual != state:
            state = actual
            await ctx.publish_state({"state": state})


app.run()
```

**MQTT interaction:**

=== "Command → State"

    ```text
    → gas2mqtt/valve/set       "on"
    ← gas2mqtt/valve/state     {"state": "on"}
    ```

=== "Toggle"

    ```text
    → gas2mqtt/valve/set       "toggle"
    ← gas2mqtt/valve/state     {"state": "off"}
    ```

=== "Invalid Command"

    ```text
    → gas2mqtt/valve/set       "blink"
    ← gas2mqtt/error           {"error_type": "error", "message": "Unknown command: 'blink'..."}
    ← gas2mqtt/valve/error     {"error_type": "error", "message": "Unknown command: 'blink'..."}
    ```

## Error Handling in Command Devices

Errors can occur in two places:

1. **In the command handler** — raised when processing an inbound command. The
   `TopicRouter` catches exceptions and publishes structured error payloads. The
   device loop continues.
2. **In the main loop** — if the device coroutine itself crashes (e.g. hardware
   failure during periodic polling), the framework catches the exception, logs it,
   and publishes an error. The device task ends, but other devices continue.

!!! tip "Validate early"

    Check command payloads at the top of your handler and raise with a descriptive
    message. This gives consumers clear error feedback via the MQTT error topic.

---

## See Also

- [Device Archetypes](../concepts/device-archetypes.md) — telemetry vs command
  archetypes
- [MQTT Topics](../concepts/mqtt-topics.md) — topic layout for commands and state
- [Error Handling](../concepts/error-handling.md) — how the framework isolates errors
- [ADR-010](../adr/ADR-010-device-archetypes.md) — the decision behind device
  archetypes
- [ADR-002](../adr/ADR-002-mqtt-topic-conventions.md) — MQTT topic conventions
