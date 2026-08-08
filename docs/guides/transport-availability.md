---
icon: material/wifi-off
---

# Transport Availability Signaling

When an adapter wraps a fallible transport — SSH, BLE, serial, HTTP — the device
availability topic should reflect whether the transport is currently reachable.
cosalette provides first-class support for this via `unavailable_on` on
`@app.command` and `ctx.mark_unavailable()`.

!!! note "Topic convention"
    The framework publishes `"online"` or `"offline"` to
    `{app}/{device}/availability` (retained, QoS 1).  Home Assistant MQTT
    integration reads this topic to mark entities as available or unavailable
    without requiring custom state payloads.

---

## The Problem Without Framework Support

Without framework support, each adapter catches transport exceptions and manually
constructs an "unavailable" state payload:

```python title="Without framework support — boilerplate in every handler"
@app.command("display")
async def handle_display(ctx: DeviceContext) -> dict[str, object]:
    try:
        result = await ssh.read()
        return {"available": True, "brightness": result.brightness}
    except SSHError:
        return {"available": False, "brightness": None}  # manual unavailable payload
```

This pattern is repeated across every adapter with a fallible transport, has no
standard MQTT availability topic, and doesn't integrate with Home Assistant's
entity availability mechanism.

---

## Static Form — `unavailable_on`

Declare which exception types represent transport failures directly on the decorator:

```python title="app.py"
import cosalette

app = cosalette.App(name="wallpanel", version="1.0.0")


class SSHError(Exception):
    """Raised when the SSH connection to the device fails."""


@app.command("display", unavailable_on=(SSHError, TimeoutError))  # (1)!
async def handle_display(ctx: cosalette.DeviceContext) -> dict[str, object]:
    result = await ssh.read()                          # (2)!
    return {"brightness": result.brightness}           # (3)!


app.run()
```

1. `unavailable_on` declares the exception tuple. Any exception in this tuple
   that escapes the handler is **suppressed** — it does not propagate.
2. If `SSHError` or `TimeoutError` is raised here, the framework intercepts it.
3. On success: the framework publishes the returned dict as device state.

**What the framework does when a matching exception is raised:**

1. Suppresses the exception (does not re-raise).
2. Publishes `"offline"` to `wallpanel/display/availability` (retained, QoS 1).
3. Logs a structured error payload to `wallpanel/display/error`.
4. Sets an internal `_is_unavailable` flag on the device context.

!!! tip "Non-matching exceptions go to the error topic"
    Only exceptions in the `unavailable_on` tuple are suppressed.  Any other
    exception is caught by the framework, logged, and published to the error
    topic — the device availability state is unchanged.

---

## Dynamic Form — `ctx.mark_unavailable()`

For conditional unavailability — where you check reachability before attempting
the operation — call `ctx.mark_unavailable()` directly from the handler body:

```python title="app.py"
@app.command("sensor")
async def handle_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await client.is_reachable():          # (1)!
        await ctx.mark_unavailable()             # (2)!
        return {}

    data = await client.read()
    return {"value": data.value}                 # (3)!
```

1. Pre-flight reachability check — no exception needed.
2. `mark_unavailable()` publishes `"offline"` to the availability topic.
3. Next successful invocation triggers auto-recovery (see below) — but only
   for `@app.command` handlers; see
   [Recovery by Archetype](#recovery-by-archetype).

---

## Dynamic Form — `ctx.mark_available()`

`ctx.mark_available()` is the symmetric counterpart to `ctx.mark_unavailable()`:
it publishes `"online"` (retained, QoS 1) to the same availability topic and
clears the internal `_is_unavailable` flag.

```python title="app.py"
@app.telemetry("sensor", interval=30)
async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await client.is_reachable():
        await ctx.mark_unavailable()             # (1)!
        return {}

    if ctx._is_unavailable:
        await ctx.mark_available()                # (2)!

    data = await client.read()
    return {"value": data.value}
```

1. Publishes `"offline"`, same as the command form.
2. Explicitly signals recovery — required for `@app.telemetry` / `@app.device`
   handlers, which do **not** auto-recover (see below).

`mark_available()` is a no-op when no `HealthReporter` is injected (e.g. in
unit tests that construct a bare `DeviceContext`), mirroring
`mark_unavailable()`.

---

## Auto-Recovery

`@app.command` handlers share an auto-recovery mechanism between the static and
dynamic forms. After any **successful** command handler invocation — where no
`unavailable_on` exception was raised and no early return without a matching
exception — the framework:

1. Checks whether the internal `_is_unavailable` flag is set.
2. If yes: publishes `"online"` to the availability topic.
3. Resets the flag to `False`.

No explicit "come back online" call is needed in the handler.

```
MQTT events for two consecutive calls:

  Call 1: SSHError raised
    → wallpanel/display/availability  "offline"  (retained)
    → wallpanel/display/error         {...}

  Call 2: succeeds
    → wallpanel/display/state         {"brightness": 80}
    → wallpanel/display/availability  "online"   (retained)
```

---

## Recovery by Archetype

Auto-recovery is **command-only** (ADR-047). `@app.telemetry` and `@app.device`
handlers do not auto-recover after a successful invocation — a telemetry
handler legitimately returning a value, or a device loop completing an
iteration, does not by itself mean the underlying transport has healed. These
archetypes must call `ctx.mark_available()` explicitly to signal recovery.

| Archetype | Auto-recovers? | Recovery mechanism |
|-----------|-----------------|---------------------|
| `@app.command` | Yes — after any successful invocation | Automatic, or explicit `ctx.mark_available()` |
| `@app.telemetry` | No | Explicit `ctx.mark_available()` only |
| `@app.device` | No | Explicit `ctx.mark_available()` only |

```python title="Telemetry — explicit recovery required"
@app.telemetry("sensor", interval=30)
async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await client.is_reachable():
        await ctx.mark_unavailable()
        return {}
    # A return here does NOT auto-recover — mark_available() is required.
    if ctx._is_unavailable:
        await ctx.mark_available()
    return {"value": (await client.read()).value}
```

---

## Scope — Device-Level

Availability state is **device-scoped**: all `@app.command` handlers that share
the same device name share one availability state.  If a device has multiple
commands (e.g. via `sub=`), a single failure on any one of them marks the whole
device offline.

---

## Which Form to Use

| Situation | Recommended form |
|-----------|-----------------|
| Specific exception type = transport failure | `unavailable_on=(ExcType, ...)` |
| Reachability check before attempting I/O | `ctx.mark_unavailable()` |
| Exception + pre-flight check combined | Both together |
| Signal recovery from `@app.telemetry` / `@app.device` | `ctx.mark_available()` (explicit — no auto-recovery) |
| Signal recovery from `@app.command` outside auto-recovery timing | `ctx.mark_available()` (optional — auto-recovery also applies) |

### Using Both Together

```python
@app.command("display", unavailable_on=(SSHError,))
async def handle_display(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await ssh.ping():
        await ctx.mark_unavailable()   # proactive check
        return {}
    return {"brightness": await ssh.read_brightness()}   # SSHError auto-handled
```

---

## Home Assistant Integration

The `{app}/{device}/availability` topic is the standard MQTT availability topic
expected by Home Assistant's [MQTT integration][ha-mqtt-availability].  Configure
it in your HA device configuration:

```yaml title="configuration.yaml (example)"
mqtt:
  sensor:
    - name: "Display Brightness"
      state_topic: "wallpanel/display/state"
      value_template: "{{ value_json.brightness }}"
      availability_topic: "wallpanel/display/availability"
      payload_available: "online"
      payload_not_available: "offline"
```

When the transport is unreachable, HA marks the entity as **Unavailable** instead
of showing a stale value.

[ha-mqtt-availability]: https://www.home-assistant.io/integrations/mqtt/

---

## Relationship to HealthCheckRunner

[`HealthCheckRunner`](../concepts/health-reporting.md#adapter-health-checks) monitors adapter health on
a **polling schedule** — it calls `health_check()` at a configurable interval and
flips availability if the probe fails.

Transport availability signaling fires **per command invocation** — it reacts to
real transport errors as they occur.

Both publish to the same `{app}/{device}/availability` topic and are fully
complementary:

| Mechanism | Trigger | Best for |
|-----------|---------|----------|
| `HealthCheckRunner` | Scheduled health probe | Detecting silent transport loss |
| `unavailable_on` / `ctx.mark_unavailable()` | Command handler failure | Reacting to transport errors on demand |
| `ctx.mark_available()` | Explicit call in handler body | Signaling recovery for `@app.telemetry` / `@app.device`, which do not auto-recover |

---

## Testing

Use `AppHarness` to assert availability topic messages in integration tests:

```python title="tests/integration/test_my_device.py"
import asyncio
import pytest
from cosalette import DeviceContext
from cosalette.testing import AppHarness


class TransportError(Exception):
    pass


@pytest.mark.asyncio
async def test_device_goes_offline_on_transport_error():
    harness = AppHarness.create(name="myapp")
    handler_called = asyncio.Event()

    @harness.app.command("sensor", unavailable_on=(TransportError,))
    async def handle(ctx: DeviceContext) -> None:
        handler_called.set()
        raise TransportError("unreachable")

    async def simulate() -> None:
        await asyncio.sleep(0.05)
        await harness.inject_command("sensor", "")
        await handler_called.wait()
        await asyncio.sleep(0.05)
        harness.trigger_shutdown()

    asyncio.create_task(simulate())
    await asyncio.wait_for(harness.run(), timeout=5.0)

    msgs = harness.messages_for("myapp/sensor/availability")
    assert "offline" in [m[0] for m in msgs]
```

!!! note
    Use the full `AppHarness.create()` + `harness.run()` lifecycle for availability
    assertions — `call_command()` bypasses the `HealthReporter` wiring needed to
    publish availability topics.

See [Testing](testing.md) for the full testing guide.
