---
icon: material/timer-sand-empty
---

# Staleness

An entity is **stale** when it has delivered no fresh data for longer than its
`stale_after=` bound. Staleness is not failure. A handler that raises is
*failing*, and the failure paths report it
([Error Handling](error-handling.md),
[Transport Availability](../guides/transport-availability.md)). A stale entity
may never raise at all: its task died, a read hangs without a `timeout`, an
`init=` keeps failing, a health check passes while the adapter delivers nothing,
or `unavailable_on` deliberately ignores the error. Without staleness the last
reading would sit in Home Assistant as if it were current.

This page is the one place that describes staleness: what can go stale, what
the framework does about it, and how the options combine
([ADR-080](../adr/ADR-080-telemetry-freshness-tracking-and-a-stale-device-status.md),
[ADR-083](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md),
[ADR-084](../adr/ADR-084-adapter-reset-restart-protocol-and-opt-in-restart-on-stale-telemetry.md)).

## What Can Go Stale

Only entities that are *expected* to deliver data on their own can go stale.

| Entity | Can go stale? | Why |
|--------|---------------|-----|
| `@app.telemetry` (named) | **Yes, by default** | It runs on a schedule, so the framework knows how often data should arrive and derives the bound |
| `@app.telemetry` (root, `name=None`) | Only with an explicit `stale_after=` | A root entity publishes the app-wide `{prefix}/availability`, so it opts in, as for `unavailable_on` |
| `@app.stream` | Only with an explicit `stale_after=` | A stream yields when items arrive; only you know how quiet a healthy stream can be |
| `@app.command` | No | It runs when a message arrives. Silence means nobody sent a command |
| `@app.device` | No | The handler owns its own loop; the framework cannot tell idle from stuck |
| `@app.periodic` | No | It publishes nothing an entity could be fresh about |
| Discovery, state and attributes topics | No | They are outputs of the entities above, not entities of their own |

## What Counts as Fresh

| Entity | Fresh event |
|--------|-------------|
| Telemetry | A **fresh cycle**: a successful poll, including one whose value a `PublishStrategy` suppressed as unchanged |
| Stream | Every item the handler yields |

The entity is stale once its last fresh event is older than `stale_after`, and
fresh again at the next fresh event.

## The `stale_after=` Bound

| `stale_after=` | Telemetry | Stream |
|----------------|-----------|--------|
| omitted (named) | Derived: `2 × period + timeout × (retry + 1) + allowance × retry` | Disabled |
| omitted (root) | Disabled | Disabled |
| `float` | Explicit bound in seconds | Explicit bound in seconds |
| callable / `SettingRef` | Resolved from settings at startup | Resolved from settings at startup |
| callable with a dict `name=` callable | Called with each device's config, like `timeout=` | — |
| `None` | Disabled for this entity | Disabled (the default) |

```python
@app.telemetry("radon", interval=300, stale_after=1800)  # offline after 30 min
async def read_radon(ctx: cosalette.DeviceContext) -> dict[str, float]: ...


@app.stream("ble-feed", stale_after=120)  # offline after 2 min without an item
async def ble_feed(stream: Stream[Advertisement], ctx: cosalette.DeviceContext): ...
```

### How the telemetry bound is derived

*period* is the `interval`, or the longest gap between a cron `schedule`'s next
fire times; a disabled `timeout` counts as `0`. The per-retry *allowance* is the
backoff's `max_delay`, including its maximum positive jitter, but never less
than 60 s. Built-in constructor caps are before jitter; their read-only
`max_delay` properties include the +20% bound. The default backoff therefore
allows 72 s per retry. The bound is deterministic, independent of sampled
jitter. For `interval=300, retry=2` and the default timeout (one interval), the
bound is `600 + 300 × 3 + 144 = 1644` s; for `interval=60` with no retries it is
180 s.

With `timeout=None`, handler execution has no finite upper bound. The derived
default budgets intervals and backoff sleeps but cannot guarantee completion of
an arbitrarily slow handler; set an explicit `stale_after` for the freshness
window your application needs.

!!! tip "Custom backoff strategies"
    A built-in backoff with a cap above 60 s widens the derived bound
    automatically — `ExponentialBackoff(max_delay=300)` with `retry=3` allows
    1080 s for the backoff sleeps. A custom `BackoffStrategy` is credited with
    60 s per retry unless it exposes a finite numeric `max_delay` attribute
    including any jitter; otherwise set `stale_after=` explicitly.

!!! tip "Tolerating a period without data"
    To tolerate a quiet spell instead of single failures, disable the failure
    mark with `unavailable_on=None` and set an explicit `stale_after`, for
    example `stale_after=180`. This is elapsed-time tolerance, not a
    consecutive-failure count (ADR-077); see
    [Transport Availability](../guides/transport-availability.md).

## What Happens When an Entity Goes Stale

The freshness watchdog checks every
`min(heartbeat_interval, 60 s, smallest stale_after)`. At the **stale
transition** — the first check that finds the bound exceeded — it does the
following, once per stale episode:

| Signal | Effect | Cleared by |
|--------|--------|------------|
| Availability | A named entity publishes retained `"offline"` under the `freshness` source | The next fresh event |
| Heartbeat | The entity's `{prefix}/status` entry reports `"stale"`, which outranks `"error"` | The next fresh event |
| Log | One WARNING naming the entity | — |
| Health file | The `"stale"` status fails the probes' default `--fail-on stale` | The next write after a fresh event |

The `freshness` source is independent of the other availability sources.
Clearing it never brings an entity online while a failure mark
(`unavailable_on`, `ctx.mark_unavailable()`), a failed adapter health check or a
stream's `feeds=` still holds it offline, and the reverse. Root telemetry with
an explicit `stale_after=` publishes freshness transitions to the app-wide
`{prefix}/availability` topic. Root streams are heartbeat-only: their staleness
appears in the heartbeat and health file without an MQTT availability publish.
Telemetry heartbeat entries also carry `last_success_at`
([Payloads](../reference/payloads.md)).

A stream's `feeds=` entities follow it: while the stream is stale, each fed
entity is held `"offline"` under the `stream:{name}` source
([Streaming](streaming.md#availability-and-health)).

## Responding to Staleness

By default staleness is only *reported*. Two `App` options act on it. Both count
every entity that can go stale: telemetry, and streams with `stale_after=`.

### Restart the adapter in place — `restart_on_stale`

A health check can pass while the adapter delivers no data, for example when a
BLE client is connected but every read times out. The entity goes stale, but the
health check failure threshold never fires. With `App(restart_on_stale=True)`,
the stale transition requests a restart of every restartable adapter the entity
depends on:

```python
app = App("airthings2mqtt", health_check_interval=60.0, restart_on_stale=True)
```

- For telemetry, the adapters are those it injects. For a stream, they are the
  `StreamablePort[T]` behind its `Stream[T]` parameter plus any adapter it
  injects. The restart cancels and re-creates the dependent tasks, which polls
  telemetry right away and reopens the stream.
- The request skips `restart_after_failures` but counts toward `max_restarts`
  and uses `restart_cooldown`. An adapter whose budget is spent is not
  restarted.
- It fires once per stale episode. The entity has to deliver fresh data and go
  stale again before it requests another restart.
- It waits for a running health check round, so the two paths never restart the
  same adapter at once.
- It needs the health check runner: `health_check_interval` must be set and the
  adapter must be `HealthCheckable` and restartable. A missing runner or empty
  stale-adapter map produces a startup WARNING that the option has no effect.
  An adapter without a restart protocol produces a startup WARNING, while
  `restartable = False` opts out with INFO. When a runner exists, requests for
  unknown or non-restartable adapters are ignored at DEBUG. A stale entity
  absent from the adapter map is also logged at DEBUG; nothing is restarted.

The restart mechanics — `reset()`, re-entering the context manager, cooldown
and budget — are described under
[Auto-Restart](health-reporting.md#auto-restart).

### Restart the process — `exit_after_stale`

```python
app = App("myapp", exit_after_stale=1800)  # exit 5 after 30 min stale
```

Once an entity has been stale for `exit_after_stale` seconds, the app logs the
decision at CRITICAL, shuts down cleanly and raises
[`StaleTelemetryError`](../reference/errors.md#staletelemetryerror). The CLI
exits with code `5`, so a container restart policy starts it again; see
[Supervised restart](../guides/deployment.md#supervised-restart).

### Using both

`exit_after_stale` counts from the same stale transition as `restart_on_stale`.
If it is shorter than the in-place recovery, the app exits before the restart
can help. Keep:

```text
exit_after_stale > check_interval + restart_time + first_cycle_time
```

- `check_interval` is how late the watchdog notices the transition and requests
  the restart: `min(heartbeat_interval, 60 s, smallest stale_after)`.
- `restart_time` covers waiting for a running health check round, then
  `restart_cooldown`, `reset()` or the context manager exit and entry, and the
  health check that follows. With several restartable adapters behind the
  entity, add up their restarts.
- `first_cycle_time` is how long the re-created task takes to deliver fresh
  data: the first successful telemetry cycle, including retries, or the first
  item the reopened stream yields.

Each stale episode requests only one restart, so if that restart fails or no
fresh data follows, `exit_after_stale` is the remaining fallback. As a rule of
thumb, set `exit_after_stale` to at least `2 × (60 s + restart_cooldown + the
longest interval of the affected telemetry or stale_after of the affected
stream)`. For a 300 s interval and the default 5 s cooldown, that is 730 s, so
`1800` leaves room.

## Choosing the Options

| Goal | Setting |
|------|---------|
| Never mark this entity stale | `stale_after=None` (telemetry) or omit it (stream) |
| Mark a quiet stream offline | `@app.stream(..., stale_after=...)` |
| Let a container probe catch staleness | `COSALETTE_HEALTH_FILE` and the [health probe](../reference/health-file.md) |
| Recover a stuck adapter without restarting the process | `restart_on_stale=True` with `health_check_interval` |
| Restart the process as the last resort | `exit_after_stale=...` and a restart policy |

---

## See Also

- [Health & Availability](health-reporting.md) — heartbeat, availability and adapter health checks
- [Transport Availability](../guides/transport-availability.md) — failure-driven availability
- [Streaming](streaming.md#availability-and-health) — stream availability sources and `feeds=`
- [Deployment](../guides/deployment.md#supervised-restart) — exit codes and restart policies
- [Health File](../reference/health-file.md) — the probe contract
- [ADR-080 — Telemetry Freshness Tracking](../adr/ADR-080-telemetry-freshness-tracking-and-a-stale-device-status.md)
- [ADR-083 — Health File and Probe](../adr/ADR-083-opt-in-health-file-and-a-health-cli-probe-for-container-liveness.md)
- [ADR-084 — Adapter reset() Restart Protocol and Restart on Stale](../adr/ADR-084-adapter-reset-restart-protocol-and-opt-in-restart-on-stale-telemetry.md)
