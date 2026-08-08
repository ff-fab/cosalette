---
icon: material/remote
---

# Command & Control Devices

`@app.command` is the **recommended** decorator for devices that receive MQTT
commands and publish state back. The framework calls your handler on every inbound
message — no main loop, no lifecycle management on your part.

## Handler anatomy

```python
@app.command("blind")  # (1)!
async def handle_blind(
    payload: str, ctx: cosalette.DeviceContext  # (2)!
) -> dict[str, object]:  # (3)!
    driver = ctx.adapter(VeluxPort)
    position = int(payload)
    await driver.set_position(position)
    return {"position": position}  # (4)!
```

1. `@app.command` registers a handler for `{prefix}/blind/set` messages.
2. `payload` is optional and injected by name from the MQTT message; `ctx` is
   injected by type annotation. Declare only what you need.
3. Returning a `dict` auto-publishes to `{prefix}/blind/state`.
4. No closure, no main loop, no `nonlocal` — just a function.

## Command routing

When a message arrives on `{prefix}/blind/set`, the framework's `TopicRouter`
extracts the device name and dispatches to the registered handler (`@app.command`),
the command queue (`ctx.commands()`), or callback (`@ctx.on_command`). Sub-topic
commands (`{prefix}/blind/calibrate/set`) are routed to their specific handler.
See [MQTT Topics](mqtt-topics.md) for the full topic layout.

## When to use `@app.device` instead

For devices that need full lifecycle control — periodic hardware polling, custom
event loops, state machines, or combined command + telemetry behaviour — use
[`@app.device`](device.md). The handler must be an **async generator**: `yield`
after each unit of work marks the reaction boundary.

```python
@app.device("blind")  # (1)!
async def blind(ctx: cosalette.DeviceContext):  # (2)!
    driver = ctx.adapter(VeluxPort)

    async for cmd in ctx.commands(timeout=30):  # (3)!
        if cmd is None:
            status = await driver.poll_status()
            await ctx.publish_state(status)
        else:  # (4)!
            position = int(cmd.payload)
            await driver.set_position(position)
            await ctx.publish_state({"position": position})
        yield  # (5)!
```

1. `@app.device` registers the function as a long-running async generator task.
2. Return annotation is omitted — async generators do not return a value.
3. `ctx.commands(timeout=30)` drives the loop — yields `None` every 30 seconds for
   periodic work, or a `Command` when one arrives on `{prefix}/blind/set`.
4. Commands carry `payload`, `topic`, `sub_topic`, and `timestamp` fields.
5. `yield` is the **reaction boundary**. The framework dispatches any registered
   `@app.react` reactors for state objects here, before the next loop iteration.

!!! info "Async generator ownership"
    The framework creates one `asyncio.Task` per `@app.device`. The generator runs
    concurrently alongside other devices. When shutdown is signalled, the framework
    cancels the task; cancellation does **not** trigger reactor dispatch.

## See also

- [Device Archetypes](device-archetypes.md) — comparison hub and decision tree
- [`@app.device`](device.md) — the composite device archetype
- [MQTT Topics](mqtt-topics.md) — topic layout for state and commands
- [Command & Control Device guide](../guides/command-device.md) — sub-topics, retains,
  and testing
- [ADR-010](../adr/ADR-010-device-archetypes.md) — device archetype decision record
