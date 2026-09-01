---
icon: material/cog
---

# @app.device — Composite Devices

`@app.device` is the **escape hatch** for devices that need lifecycle control beyond
what `@app.telemetry` and `@app.command` offer. The handler is an **async generator**
that runs as a long-lived `asyncio.Task`, driving its own loop. The `yield` statement
marks the reaction boundary between iterations.

## When to use `@app.device`

Use `@app.device` when you need:

- Combined command + telemetry behaviour in one handler
- Custom event loops or state machines
- Adaptive intervals or backoff logic
- Direct `ctx.publish_state()` control (no auto-publish on return)

For straightforward polling, prefer [`@app.telemetry`](telemetry.md). For
command-only devices, prefer [`@app.command`](command.md). See the
[Device Archetypes hub](device-archetypes.md#choosing-an-archetype) for the full
decision tree.

## Handler anatomy

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

## Adaptive polling with backoff

For sensors requiring complex polling logic — backoff, adaptive intervals, or
multi-step reads — use `@app.device` with a manual loop:

```python
@app.device("complex_sensor")
async def complex_sensor(ctx: cosalette.DeviceContext):
    adapter = ctx.adapter(SensorPort)
    interval = 10.0

    while not ctx.shutdown_requested:
        try:
            data = await adapter.read()
            await ctx.publish_state(data)
            interval = 10.0  # reset on success
        except SensorTimeoutError:
            interval = min(interval * 2, 300)  # exponential backoff
        yield
        await ctx.sleep(interval)
```

!!! tip "Prefer @app.telemetry for standard polling"
    Use `@app.telemetry` for straightforward read-and-return sensors — it handles
    error isolation and publish strategies automatically. Use `@app.device` only
    when you need adaptive intervals, state machines, or combined command and
    telemetry behaviour.

## Publish strategies vs manual publishing

Unlike `@app.telemetry`, `@app.device` publishes state **manually** via
`ctx.publish_state()`. There is no automatic deduplication or strategy layer —
your loop logic controls when and what is published.

For the strategy-based approach (probe often, publish selectively), see
[Publish Strategies](publish-strategies.md) for the concepts, then consider
whether `@app.telemetry` with a publish strategy would serve your use case.

## Waking the loop from in-process code

A device loop that must react to a hardware push — a serial frame, a UDP
packet — should not busy-poll. Declare `triggerable="local"` and inject a
`DeviceTrigger`; `await trigger.wait(timeout=...)` blocks until an
`EntityNotifier` call names this device, or the timeout expires:

```python
@app.device("gateway", triggerable="local")
async def gateway(
    ctx: cosalette.DeviceContext,
    trigger: cosalette.DeviceTrigger,
) -> AsyncIterator[None]:
    while True:
        await trigger.wait(timeout=1.0)
        await ctx.publish_state(drain_frames())
        yield
```

Devices accept `"local"` only — `{prefix}/{name}/set` is already the device
command topic. See
[Local Triggers on `@app.device`](../guides/telemetry-advanced.md#local-triggers-on-app-device).

Add `min_interval=<seconds>` when the push source is chatty: `trigger.wait()`
then holds a wake that lands inside the window and releases exactly one — the
most recent — when the window reopens. A `timeout=` that expires first still
returns `TriggerPayload.scheduled()` **with that wake still pending**, so treat
`"scheduled"` as "the heartbeat fired", not as "nothing arrived". See
[Throttling a Trigger Storm](../guides/telemetry-advanced.md#throttling-a-trigger-storm).

## See also

- [Device Archetypes](device-archetypes.md) — comparison hub and decision tree
- [`@app.telemetry`](telemetry.md) — for standard polling
- [`@app.command`](command.md) — for pure command handling
- [Publish Strategies](publish-strategies.md) — probing/publishing frequency control
- [Local Triggers on `@app.device`](../guides/telemetry-advanced.md#local-triggers-on-app-device)
  — waking a device loop from in-process code
- [ADR-010](../adr/ADR-010-device-archetypes.md) — device archetype decision record
