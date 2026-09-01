---
icon: material/thermometer-chevron-up
---

# Advanced Telemetry Techniques

Advanced features for cosalette telemetry devices: triggerable on-demand reads,
coalescing groups for shared-bus coordination, cron-based scheduling, and
retry/backoff resilience. Start with [Build a Telemetry Device](telemetry-device.md)
before exploring these topics.

## Triggerable Telemetry

By default, telemetry devices are **poll-only** — the framework calls them on a
fixed interval. `triggerable=` declares a **trigger source**: something that can
run the handler out of cycle, immediately, through the identical publish cycle a
scheduled tick uses. The regular interval-based polling continues alongside
triggers.

| `triggerable=` | Arming path | Use it when |
|---|---|---|
| `False` (default) | none — poll only | Nothing outside the interval needs to publish |
| `True` / `"mqtt"` | inbound MQTT on `{prefix}/{device}/set` | A user or automation asks for a refresh |
| `"local"` | in-process [`EntityNotifier`](#local-in-process-triggers) | The hardware pushes to you (UDP, serial, BLE callback) |
| `"both"` | either of the above | Both a remote refresh button and a hardware push |

`True` is an alias for `"mqtt"` and keeps its original meaning, so existing
apps need no change.

/// admonition | `interval=` is a heartbeat, not the publish path
    type: tip

With a trigger source declared, `interval=` stops being *the* way state reaches
the broker and becomes a **fallback**: it refreshes the retained state topic even
if the device never pushes or nobody ever triggers, and it detects a dead push
subscription. Long intervals (minutes) are normal for `"local"` devices.
///

### Basic Usage

```python title="app.py"
@app.telemetry("sensor", interval=300, triggerable=True)  # (1)!
async def sensor() -> dict[str, object]:
    """Read sensor — every 5 min, or immediately on trigger."""
    return {"temperature": await read_sensor()}
```

1. The framework subscribes to `myapp/sensor/set`. Any message on that topic
   fires the handler immediately. The 300-second interval continues in parallel.

### Accessing the Trigger Payload

When a handler needs to know **whether** it was triggered or access the
**MQTT payload** that caused the trigger, declare a `TriggerPayload` parameter:

```python title="app.py"
from cosalette import TriggerPayload

@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor(trigger: TriggerPayload) -> dict[str, object]:  # (1)!
    days = trigger.get("days", 7) if trigger.is_triggered else 7  # (2)!
    return {"temperature": await read_sensor(days=days)}
```

1. `TriggerPayload` is injected automatically via DI — no `init=` needed.
2. On scheduled runs, `trigger.is_triggered` is `False` and `get()` returns
   the default. On triggered runs, `trigger.data` contains the parsed JSON
   payload (if valid), and `trigger.raw` holds the raw MQTT string.
   A bare `/set` publish with an empty or whitespace-only body is treated as
   an empty JSON object: `trigger.data` is `{}` (so `get()` returns your
   defaults) and `trigger.raw` preserves the literal string sent.

### Typed Trigger Payload

Declare `Annotated[Model | None, Payload()]` to receive the trigger payload as a
parsed Pydantic model. On scheduled runs the parameter is bound to `None`; on
triggered runs it holds the validated model:

```python title="app.py"
from __future__ import annotations
from typing import Annotated
from pydantic import BaseModel
from cosalette.mqtt import Payload

class RefreshCommand(BaseModel):
    days: int = 7

@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor(
    cmd: Annotated[RefreshCommand | None, Payload()],
) -> dict[str, object]:
    days = cmd.days if cmd is not None else 7
    return {"data": await read_sensor(days=days)}
```

The raw `TriggerPayload` approach (see above) remains available when you only
need `is_triggered` / `raw` / `data` without a full Pydantic model.

### Local (In-Process) Triggers

When the *device* pushes — a bulb announcing a state change over UDP, a serial
bridge decoding a frame — there is no MQTT message to wait for and no reason to
wait for the next tick. Declare `triggerable="local"` and inject
`EntityNotifier`: calling it with an entity's name wakes that entity's handler.

```python title="app.py"
import cosalette

@app.state
def shared(notify: cosalette.EntityNotifier) -> SharedState:  # (1)!
    return SharedState(notify=notify)

@app.telemetry(
    name=_bulb_names,          # (2)!
    interval=60,
    triggerable="local",
    publish=cosalette.OnChange(),
)
async def bulb(ctx: cosalette.DeviceContext, state: SharedState) -> dict[str, object]:
    return state.snapshot(ctx.name)
```

1. Store the handle — do **not** call it from the factory body. Trigger slots do
   not exist yet at that point and calling early raises `NotifierNotReadyError`.
2. Local triggers are per **expanded** name: `notify("bulb-kitchen")` wakes only
   that entity, never its siblings.

```python title="adapter.py"
class WizBulbAdapter:
    def _on_push(self, ip: str) -> None:   # may run on a UDP thread
        self._cache[ip] = _parse(ip)
        self._notify(self._name_for(ip))   # safe from any thread
```

The woken run is an ordinary run: `publish=`, `state_model=` validation,
availability, persistence and error publication all behave exactly as they do on
a tick. Inside the handler, `TriggerPayload.source` tells the three apart —
`"scheduled"`, `"mqtt"` or `"local"`.

/// admonition | The notifier fails loudly
    type: warning

`EntityNotifier` never silently does nothing:

- an unknown name — a typo, or an entity that did not declare `"local"` /
  `"both"` — raises `UnknownEntityError`, listing the names that are notifiable;
- calling it before the framework has built the trigger slots (i.e. from inside
  an `@app.state` or adapter factory body) raises `NotifierNotReadyError`.

Both derive from `EntityNotifierError`. The name is validated in the calling
thread, so a bad name raises where you called it.
///

Repeated calls **coalesce**: a burst of pushes arriving before the handler runs
results in a single out-of-cycle run, not one run per push. There is no
minimum-interval throttle yet — if a device pushes far faster than the handler
can run, add your own rate limit before notifying.

### Constraints

/// admonition | Root devices need a local source
    type: warning

An MQTT trigger source (`True`, `"mqtt"`, `"both"`) requires a **named** device —
root (unnamed) devices have no topic segment to subscribe to. Attempting
`@app.telemetry(interval=60, triggerable=True)` raises `ValueError`. Use
`triggerable="local"` instead: a local wake needs no topic.
///

/// admonition | Coalescing groups are incompatible
    type: warning

`triggerable=` (any source, `"local"` included) and `group=` cannot be
combined. Coalescing groups use a shared tick-aligned scheduler that is
incompatible with on-demand triggers.
///

### Coalescing Behaviour

If multiple MQTT messages arrive before the handler finishes its current
execution, the trigger coalesces — only the **latest** payload is used.
The handler runs once with the most recent `TriggerPayload`, not once per
message. This prevents thundering-herd scenarios when a burst of triggers
arrives.

---

## Coalescing Groups

When multiple telemetry handlers share a physical resource (e.g. a serial bus),
use the `group=` parameter to coalesce them into a shared execution window:

```python
@app.telemetry(name="outdoor", interval=300, group="optolink")
async def outdoor(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["outdoor_temp"])

@app.telemetry(name="hotwater", interval=300, group="optolink")
async def hotwater(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["hot_water_temp"])
```

Handlers in the same group execute sequentially within a batch when their
intervals coincide. At t=0 all grouped handlers fire together; at subsequent
ticks only those whose interval divides evenly into the elapsed time fire.

Each handler retains its own publish strategy, error isolation, persistence
policy, and init function. The `group=` parameter is purely an execution
scheduling hint.

For architectural context see [Coalescing Groups](../concepts/telemetry.md#coalescing-groups)
and [ADR-018](../adr/ADR-018-coalescing-groups.md).

---

## Cron-Based Scheduling

When your device needs time-of-day-aligned polling — daily at 06:00, twice a day,
or on specific weekdays — use the `schedule=` parameter instead of `interval=`:

```python
from cosalette import CronSchedule

@app.telemetry("calendar", schedule="0 0 6,18 * * ?")  # (1)!
async def calendar() -> dict[str, object]:
    events = await fetch_calendar_events()
    return {"events": events}
```

1. Quartz cron format: `second minute hour day-of-month month day-of-week`.
   This fires at 06:00 and 18:00 daily. The first execution runs immediately on
   startup, then waits for the next scheduled time.

For the field diagram and expression examples, see
[Cron Syntax Reference](../reference/telemetry.md#cron-syntax-reference).

### `schedule=` vs `interval=`

- **Mutually exclusive** — providing both raises `ValueError`
- **One is required** — every telemetry registration needs either `schedule=` or `interval=`
- `schedule=` accepts a cron string, a pre-parsed `CronSchedule` instance, or a
  `CronSpec` callable for per-device schedules (see [Per-Device Schedules](#per-device-schedules) below)
- `schedule=` cannot combine with `group=` (coalescing groups require `interval=`)
- All other telemetry features (`publish=`, `persist=`, `retry=`, `init=`) work with
  both `schedule=` and `interval=`

### Per-Device Schedules

When `name=` is a callable (dict-name multi-device registration), `schedule=` can
also be a **callable** — a `CronSpec` — that receives the per-device config and
returns a cron string or `CronSchedule` instance. This lets each device run on its
own wall-clock schedule:

```python
from dataclasses import dataclass
from cosalette import App, DeviceContext

@dataclass
class SensorConfig:
    mac: str
    cron_expr: str = "0 0 * * * ?"  # default: every hour

app = App(name="sensors", version="1.0.0")

@app.telemetry(
    name=lambda s: {
        "morning_sensor": SensorConfig(mac="AA:...:01", cron_expr="0 0 6 * * ?"),
        "evening_sensor": SensorConfig(mac="AA:...:02", cron_expr="0 0 18 * * ?"),
    },
    schedule=lambda cfg: cfg.cron_expr,  # (1)!
)
async def sensor(
    ctx: DeviceContext, config: SensorConfig,
) -> dict[str, object]:
    return {"value": await read_ble(config.mac)}
```

1. The `schedule=` callable receives the per-device config object (not `Settings`).
   `morning_sensor` fires at 06:00; `evening_sensor` fires at 18:00.

!!! warning "Constraints"

    - Requires `name=` to be a callable (dict-name form). Static names raise `ValueError`.
    - Cannot combine with `group=` (coalescing groups require a shared `interval=`).

### When to Use `@app.device` + `ctx.sleep_until()` Instead

For devices managed via `@app.device` that need time-of-day alignment
without the `@app.telemetry` polling model, use `ctx.sleep_until()`:

```python
import cosalette
from datetime import time

@app.device("calendar")
async def calendar(ctx: cosalette.DeviceContext):
    while not ctx.shutdown_requested:
        events = await fetch_calendar_events()
        await ctx.publish_state({"events": events})
        yield
        await ctx.sleep_until(time(6, 0))  # (1)!
```

1. Sleeps until the next 06:00 (local timezone by default).
   Pass `tz=datetime.timezone.utc` for UTC, or
   `tz=ZoneInfo("Europe/Berlin")` for an explicit timezone.

`ctx.sleep_until()` also accepts a sequence of times — it sleeps until the
nearest upcoming one:

```python
await ctx.sleep_until([time(6, 0), time(18, 0)])  # next 06:00 or 18:00
```

---

## Retry / Backoff

By default, a failed telemetry poll publishes an error and waits for the next
interval. When polling a flaky transport (BLE, serial, HTTP), you often want
the framework to retry the handler a few times before giving up. The `retry=`
parameter adds exactly that — a configurable retry loop with backoff delays,
all shutdown-aware.

### Basic Usage

```python title="app.py"
import cosalette

app = cosalette.App(name="ble2mqtt", version="1.0.0")


@app.telemetry("sensor", interval=30, retry=3)  # (1)!
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read a BLE sensor that sometimes times out."""
    adapter = ctx.adapter(BLESensorPort)
    return {"temperature": await adapter.read_temperature()}


app.run()
```

1. Up to 3 retry attempts on failure. Defaults to retrying on `OSError`
   with `ExponentialBackoff(base=2.0, max_delay=60.0)` — delays of
   ~2 s, ~4 s, ~8 s (with ±20 % jitter).

### How It Works

1. The framework calls your handler.
2. If it raises an exception matching `retry_on`, the attempt is logged at
   WARNING level (not published to MQTT).
3. The framework sleeps for the backoff delay using `ctx.sleep()` — if a
   shutdown signal arrives during the wait, the sleep is aborted immediately.
4. Steps 1–3 repeat up to `retry` times.
5. If the handler still fails after all retries, the exception falls through
   to the normal error path: logged at ERROR, published to the error topic,
   and state-transition deduplication applies.
6. On success, the cumulative retry counter resets to zero.

!!! info "Cumulative counter"

    The retry counter is **not** reset between poll cycles. If the handler
    fails twice in cycle N and once more in cycle N+1, that counts as
    three total attempts. The counter only resets when a poll succeeds.

### Custom Backoff Strategies

The default `ExponentialBackoff` works well for most transports. For
different patterns, choose an alternative or write your own:

```python title="app.py"
from cosalette import LinearBackoff, FixedBackoff

# Linear: 1s, 2s, 3s, ... capped at 30s
@app.telemetry("serial", interval=60, retry=5, backoff=LinearBackoff(step=1.0, max_delay=30.0))
async def serial_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await read_serial(ctx)}

# Fixed: always wait exactly 2s between attempts
@app.telemetry("http", interval=120, retry=3, backoff=FixedBackoff(delay=2.0))
async def http_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await fetch_api(ctx)}
```

For fully custom logic, implement the `BackoffStrategy` protocol — a single
method `delay(attempt: int) -> float`:

```python title="app.py"
class SlowStart:
    """No delay on first retry, then exponential."""

    def delay(self, attempt: int) -> float:
        if attempt <= 1:
            return 0.0
        return min(2.0 ** (attempt - 1), 30.0)


@app.telemetry("sensor", interval=30, retry=4, backoff=SlowStart())
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"temperature": await read_ble(ctx)}
```

For a comparison of the built-in strategies, see
[Retry and Backoff Strategies](../reference/telemetry.md#retry-and-backoff-strategies).

### Circuit Breaker

When a backend is down for an extended period, retrying every poll cycle
wastes resources and floods logs. A `CircuitBreaker` short-circuits the
retry loop after repeated failures:

```python title="app.py"
from cosalette import CircuitBreaker, ExponentialBackoff

@app.telemetry(
    "inverter",
    interval=60,
    retry=3,
    backoff=ExponentialBackoff(base=2.0, max_delay=30.0),
    circuit_breaker=CircuitBreaker(threshold=5),  # (1)!
)
async def inverter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    adapter = ctx.adapter(ModbusPort)
    return {"power_w": await adapter.read_register(0x0006)}
```

1. After 5 consecutive failures (across poll cycles), the circuit **opens** —
   the handler is skipped entirely until a half-open probe succeeds.

The circuit breaker uses a three-state machine; see
[Circuit Breaker States](../reference/telemetry.md#circuit-breaker-states)
for the state transition table.

### Combining with Other Features

Retry composes naturally with publish strategies, persistence, and
coalescing groups. Each feature operates at its own layer:

```python title="app.py"
from cosalette import (
    CircuitBreaker,
    DeviceStore,
    ExponentialBackoff,
    OnChange,
    SaveOnPublish,
)

@app.telemetry(
    "boiler",
    interval=30,
    publish=OnChange(threshold=0.5),
    persist=SaveOnPublish(),
    retry=3,
    backoff=ExponentialBackoff(base=2.0, max_delay=30.0),
    circuit_breaker=CircuitBreaker(threshold=5),
    group="optolink",  # (1)!
)
async def boiler(
    ctx: cosalette.DeviceContext,
    store: DeviceStore,
) -> dict[str, object]:
    adapter = ctx.adapter(OptolinkPort)
    data = await adapter.read_signals(["boiler_temp", "burner_hours"])
    store["last_reading"] = data
    return data
```

1. Within a coalescing group, each handler has its own independent retry
   state. If `boiler` retries while `hotwater` succeeds, only `boiler`
   counts failures.

!!! warning "Constraints"

    - **`retry_on` defaults to `(OSError,)`** when `retry > 0` and no
      explicit `retry_on` is provided. Non-matching exceptions bypass
      retry entirely and go straight to the error path.
    - **Cumulative counter** — retries accumulate across poll cycles
      and only reset on success.
    - **Telemetry only** — `retry=` is not available on `@app.command`
      or `@app.device`. Those archetypes have different execution models.

For the timeout backstop (handlers that hang without raising), see
[Timeout Backstop](../reference/telemetry.md#timeout-backstop).

---

## See Also

- [Build a Telemetry Device](telemetry-device.md) — the core guide
- [Telemetry Reference](../reference/telemetry.md) — interval guidelines, cron syntax,
  backoff strategy tables, timeout backstop
- [Coalescing Groups](../concepts/telemetry.md#coalescing-groups) — concept background
- [ADR-018](../adr/ADR-018-coalescing-groups.md) — coalescing design rationale
- [ADR-024](../adr/ADR-024-telemetry-retry-backoff.md) — retry/backoff design rationale
- [ADR-032](../adr/ADR-032-sleep-until-wall-clock-scheduling.md) — cron scheduling
  design rationale
