---
icon: material/wifi-off
---

# Transport Availability Signaling

When an adapter wraps a fallible transport — SSH, BLE, serial, HTTP — the device
availability topic should reflect whether the transport is currently reachable.
cosalette provides first-class support for this via `unavailable_on` on
`@app.command`, `@app.telemetry` and `@app.device`, and `ctx.mark_unavailable()`.

!!! tip "Telemetry and device entities are automatic"
    Since ADR-077, a **named** `@app.telemetry` or `@app.device` entity publishes
    availability with no parameter at all: retained `"offline"` once a poll fails
    for good — immediately with the default `retry=0` or a non-retryable error,
    otherwise once its retries are exhausted — and `"online"` on the next
    successful poll.  Use
    `unavailable_on=` to *narrow* which exceptions count, or `unavailable_on=None`
    to switch it off.  `@app.command` stays opt-in — a command runs on demand, so
    a failed command says nothing about whether the device is reachable.

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
    result = await ssh.read()  # (2)!
    return {"brightness": result.brightness}  # (3)!


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
    if not await client.is_reachable():  # (1)!
        await ctx.mark_unavailable()  # (2)!
        return {}

    data = await client.read()
    return {"value": data.value}  # (3)!
```

1. Pre-flight reachability check — no exception needed.
2. `mark_unavailable()` publishes `"offline"` to the availability topic.
3. Next successful invocation triggers auto-recovery — for every archetype; see
   [Defaults and Recovery by Archetype](#defaults-and-recovery-by-archetype).

---

## Dynamic Form — `ctx.mark_available()`

`ctx.mark_available()` is the symmetric counterpart to `ctx.mark_unavailable()`:
it publishes `"online"` (retained, QoS 1) to the same availability topic and
clears the internal `_is_unavailable` flag.

```python title="app.py"
@app.telemetry("sensor", interval=30)
async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await client.is_reachable():
        await ctx.mark_unavailable()  # (1)!
        return {}

    if ctx._is_unavailable:
        await ctx.mark_available()  # (2)!

    data = await client.read()
    return {"value": data.value}
```

1. Publishes `"offline"`, same as the command form.
2. Explicitly signals a recovery your handler detects itself. Declarative
    `unavailable_on=` failures on telemetry and device handlers otherwise
    recover automatically at their next successful work boundary.

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

## Defaults and Recovery by Archetype

| Archetype | Publishes offline by default? | Recovery |
|-----------|-------------------------------|----------|
| `@app.telemetry` (named) | **Yes** — on a failed poll (after any retries) | Automatic on the next successful poll |
| `@app.device` (named) | **Yes** — on retry exhaustion | Automatic on the next successful poll |
| `@app.command` | No — declare `unavailable_on=` | Automatic after any successful invocation |
| Any root entity (`name=None`) | No — declare `unavailable_on=` | Automatic once opted in |
| `@app.telemetry` gone stale (no fresh cycle for `stale_after`) | **Yes** — named entities, derived bound | Automatic on the next fresh cycle |

A successful poll is a genuine recovery signal for telemetry, which is why these
archetypes now auto-recover (ADR-077, narrowing ADR-047's command-only scoping).
`ctx.mark_available()` remains available for recovery your handler detects itself.

```python title="Telemetry — nothing to declare"
@app.telemetry("sensor", interval=30, retry=2)
async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": (await client.read()).value}
```

Retries exhausted publishes `"offline"`; the next successful poll publishes
`"online"`.  Without `retry=` — or for an exception outside `retry_on` — there is
nothing to exhaust, so the first failed poll publishes `"offline"`.  Narrow it when a handler bug should not claim the device is
unreachable:

```python title="Telemetry — narrowed to transport failures"
@app.telemetry("sensor", interval=30, unavailable_on=(BleakError, TimeoutError))
async def read_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    payload = await client.read()
    return {"value": payload["v"]}  # a KeyError here does NOT mark it offline
```

!!! tip "Tolerating temporary failures"
    `retry=` tolerates transient failures *within* a cycle. To tolerate a period
    without fresh data, disable the failure mark with `unavailable_on=None` and
    choose an explicit [freshness](#freshness-stale_after) duration, such as
    `stale_after=180`. The entity goes `"offline"` after 180 seconds without a
    fresh cycle. This is elapsed-time tolerance, not a consecutive-failure count:
    handler execution, timeout, retries and backoff add to each post-cycle
    `interval`. There is no separate `unavailable_after=` count (ADR-077).

!!! warning "Root entities are excluded from the default"
    A root entity publishes to the flat `{app}/availability`, so one failed read
    would declare the **whole app** unavailable — in Home Assistant that can take
    down every entity the app owns.  Root entities therefore opt in explicitly
    with `unavailable_on=(ExcType, ...)`.

!!! note "Why the default is every exception"
    cosalette has no dependency on `bleak`, `paramiko` or `pyserial`, so it cannot
    name `BleakError`, `SSHException` or `serial.SerialException` in a default.  A
    stdlib-only default such as `(OSError, TimeoutError)` would silently never fire
    for exactly the adapters this feature exists for.  Narrowing is therefore the
    app author's call — only they can name their adapter's exception types.

---

## Freshness — `stale_after`

Failure-driven availability only fires when a poll *fails*.  It cannot see a
telemetry task that has died, a poll that hangs without a `timeout`, an `init=`
that keeps failing, or an error that `unavailable_on` deliberately ignores.  The
reading then sits in Home Assistant as if it were current.

ADR-080 adds a freshness watchdog.  Every named telemetry entity records when it
last completed a **fresh cycle** — a successful poll, including one whose value a
`PublishStrategy` suppressed as unchanged.  Once the last fresh cycle is older
than `stale_after`, the entity publishes retained `"offline"` and logs one
WARNING; the next fresh cycle publishes `"online"` again.

```python title="Freshness — explicit bound"
@app.telemetry("radon", interval=300, stale_after=1800)  # offline after 30 min
async def read_radon(ctx: cosalette.DeviceContext) -> dict[str, float]: ...
```

| `stale_after=` | Meaning |
|----------------|---------|
| omitted (named entity) | Derived: `2 × period + timeout × (retry + 1) + allowance × retry` |
| omitted (root entity) | Disabled — root entities opt in explicitly (as for `unavailable_on`) |
| `float` | Explicit bound in seconds |
| callable / `SettingRef` | Resolved from settings at startup |
| callable with a dict `name=` callable | Called with each device's config, like `timeout=` |
| `None` | Disabled for this entity |

*period* is the `interval`, or the longest gap between a cron `schedule`'s next
fire times; a disabled `timeout` counts as `0`.  The per-retry *allowance* is the
backoff's `max_delay`, including its maximum positive jitter, but never less
than 60 s. Built-in constructor caps are before jitter; their read-only
`max_delay` properties include the +20% bound. The default backoff therefore
allows 72 s per retry. The bound is deterministic, independent of sampled jitter. For
`interval=300, retry=2` and the default timeout (one interval), the bound is
`600 + 300 × 3 + 144 = 1644` s; for `interval=60` with no retries it is 180 s.

With `timeout=None`, handler execution has no finite upper bound. The derived
default budgets intervals and backoff sleeps but cannot guarantee completion
of an arbitrarily slow handler; set an explicit `stale_after` for the freshness
window your application needs.

The freshness mark is a separate availability source: clearing it never brings
an entity online while a failure mark (`unavailable_on`, `ctx.mark_unavailable()`)
still holds it offline, and vice versa.

The `{prefix}/status` heartbeat reports a stale entity as `"stale"` (it outranks
`"error"`) and adds freshness fields to every telemetry entry:

```json
"radon": {"status": "stale", "last_success_at": "2026-10-02T08:15:00+00:00", "consecutive_failures": 7, "last_error": "BleakError", "failing_since": "2026-10-02T08:20:00+00:00"}
```

`last_success_at` is `null` until the first fresh cycle; `last_error` and
`failing_since` are `null` while the entity is not failing (ADR-082).  A poll that could not
publish because the broker was down does not count as a failure.

!!! tip "Custom backoff strategies"
    A built-in backoff with a cap above 60 s widens the derived bound
    automatically — `ExponentialBackoff(max_delay=300)` with `retry=3` allows
    1080 s for the backoff sleeps.  A custom `BackoffStrategy` is credited with
    60 s per retry unless it exposes a finite numeric `max_delay` attribute
    including any jitter; otherwise set `stale_after=` explicitly.

---

## Scope — Device-Level

Availability state is **device-scoped**: all handlers that share the same device
name share one availability state.  If a device has multiple
commands (e.g. via `sub=`), a single failure on any one of them marks the whole
device offline.

---

## Which Form to Use

| Situation | Recommended form |
|-----------|-----------------|
| Specific exception type = transport failure | `unavailable_on=(ExcType, ...)` |
| Reachability check before attempting I/O | `ctx.mark_unavailable()` |
| Exception + pre-flight check combined | Both together |
| Telemetry/device, any read failure counts | Nothing — it is the default |
| Never mark this entity offline | `unavailable_on=None` |
| Never mark this entity stale | `stale_after=None` |
| Root entity should participate | `unavailable_on=(ExcType, ...)` |
| Signal recovery your handler detects itself | `ctx.mark_available()` |
| Signal recovery from `@app.command` outside auto-recovery timing | `ctx.mark_available()` (optional — auto-recovery also applies) |

### Using Both Together

```python
@app.command("display", unavailable_on=(SSHError,))
async def handle_display(ctx: cosalette.DeviceContext) -> dict[str, object]:
    if not await ssh.ping():
        await ctx.mark_unavailable()  # proactive check
        return {}
    return {"brightness": await ssh.read_brightness()}  # SSHError auto-handled
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
| `ctx.mark_available()` | Explicit call in handler body | Signaling a recovery the handler detects itself |

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
