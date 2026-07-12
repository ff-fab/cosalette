---
status: Accepted
date: 2026-03-31
impact: moderate
tags: [telemetry, error-handling]
---

# ADR-024: Telemetry Retry with Configurable Backoff

## Status

Accepted **Date:** 2026-03-31 | Amended **Date:** 2026-07-12

## Context

When a telemetry read fails (BLE timeout, CalDAV unreachable, serial error), the
framework logs the error, publishes to the error topic, and waits for the next full
poll interval. For long-interval apps this causes disproportionate data gaps:

| App | Interval | Gap from one failure |
| --- | -------- | -------------------- |
| airthings2mqtt | 25 min | 25 min (BLE flaky) |
| caldates2mqtt | 2 h | 2 h (CalDAV unreachable) |
| vito2mqtt | 60 s | 60 s (serial timeout) |
| gas2mqtt | 30 s | 30 s (meter read failure) |

No app has implemented custom retry — they all accept the gap. A framework-level
solution closes this gap transparently.

### Current error flow (no retry)

```text
run_telemetry loop:
  while not shutdown:
    try:
      result = handler(**kwargs)
      → publish / persist / clear-error
    except Exception:
      → log + publish error + set health "error"
    await ctx.sleep(interval)          ← always full interval after failure
```

### Constraints

- **ADR-011 (Error Handling):** fire-and-forget error publishing, error deduplication
  by exception type, recovery resets health to "ok".
- **ADR-012 (Health Reporting):** per-device status in heartbeat payload, string-typed
  status values.
- **ADR-013 (Publish Strategies):** retry is transparent to publish strategies. The
  strategy only sees the final successful result, never intermediate retry failures.
- **Existing MQTT backoff model** (`_mqtt_client.py`): exponential backoff with ±20%
  jitter, `delay = min(delay * 2, max_interval)`, reset on success.

## Decision

Add configurable retry with backoff strategies and optional circuit breaker to
`@app.telemetry`. Retry logic lives in `_telemetry_runner.py` and wraps the handler
invocation, transparent to the publish strategy layer.

### API surface

New parameters on `@app.telemetry` and `app.add_telemetry()`:

```python
@app.telemetry(
    interval=300,
    retry=3,                              # max retry attempts (0 = no retry)
    retry_on=(OSError,),                  # exception types to retry on
    backoff=ExponentialBackoff(            # backoff strategy (optional)
        base=2.0,
        max_delay=60.0,
    ),
    circuit_breaker=CircuitBreaker(       # optional circuit breaker
        threshold=5,
    ),
)
async def read_sensor(adapter: BlePort) -> dict[str, object]:
    ...
```

### Decision 1: `retry_on` default — `(OSError,)`

The default is `(OSError,)`, which covers all I/O and OS-level failures:

- `ConnectionError` (`ConnectionRefusedError`, `ConnectionResetError`,
  `BrokenPipeError`)
- `TimeoutError` (subclass of `OSError` since Python 3.3, PEP 3151)
- `FileNotFoundError`, `PermissionError`

`ValueError` is **excluded** from the default because it conflates two scenarios:
garbled sensor data (transient, retryable) and programming bugs (permanent, should
fail fast). Users who handle garbled data opt in explicitly:

```python
@app.telemetry(retry=3, retry_on=(OSError, ValueError))
```

When `retry > 0` and `retry_on` is empty (explicitly passed `retry_on=()`), the
framework raises `ValueError` at registration time — this is almost certainly a
configuration error.

### Decision 2: Configurable backoff strategies

Backoff is configurable from the start, using a `BackoffStrategy` protocol with
built-in implementations:

```python
class BackoffStrategy(Protocol):
    def delay(self, attempt: int) -> float:
        """Return delay in seconds for the given attempt number (1-based)."""
        ...
```

**Built-in strategies:**

| Strategy | Formula | Use case |
| -------- | ------- | -------- |
| `ExponentialBackoff(base=2.0, max_delay=60.0)` | `min(base × 2^(attempt-1), max_delay)` | Default. Most transient failures. |
| `LinearBackoff(step=2.0, max_delay=60.0)` | `min(step × attempt, max_delay)` | Predictable delay growth. |
| `FixedBackoff(delay=5.0)` | `delay` | Constant wait between retries. |

All strategies enforce a `max_delay` ceiling to prevent unbounded waits.

**Default:** When `backoff=` is omitted and `retry > 0`, the framework uses
`ExponentialBackoff(base=2.0, max_delay=60.0)`.

**Jitter:** All built-in strategies apply ±20% jitter (matching the MQTT reconnect
model in `_mqtt_client.py`) to prevent thundering-herd effects when multiple devices
retry simultaneously.

### Decision 3: Cumulative retry counter with optional circuit breaker

The retry counter **persists across poll cycles**. This ensures that exponential
backoff functions correctly — if the counter reset every cycle, a 25-minute interval
with `retry=3` would always start at attempt 1, never back off meaningfully.

The counter resets to zero on a successful read.

**Circuit breaker (optional):**

When provided, the circuit breaker tracks consecutive failed cycles (cycles where
all retry attempts were exhausted). After `threshold` consecutive failures, the
circuit opens:

```python
@app.telemetry(
    retry=3,
    circuit_breaker=CircuitBreaker(threshold=5),
)
```

| State | Behavior |
| ----- | -------- |
| **closed** | Normal operation. Handler runs, retries on failure. |
| **open** | Handler is **skipped**. Device status set to `"circuit_open"`. The framework logs at WARNING each skipped cycle. |
| **half-open** | After one full interval in open state, the framework makes a single probe attempt. On success → closed. On failure → open. |

Circuit breaker state is exposed via:

- **Health reporter:** `set_device_status(name, "circuit_open")` — visible in
  heartbeat payload at `{app}/status`.
- **Introspection endpoint:** retry configuration (retry count, retry_on types,
  backoff strategy, circuit breaker threshold) is included in the registry
  snapshot.  Runtime state (current attempt count, circuit state, consecutive
  failure count) is not currently exposed.
- **Logging:** WARNING logs are emitted when the handler is skipped due to an
  open circuit and when retry attempts are exhausted.

### Decision 4: Retry transparent to publish strategies

Retry wraps the handler invocation **inside** the telemetry runner, before the result
reaches `_handle_telemetry_outcome()`:

```text
run_telemetry loop:
  while not shutdown:
    if circuit_breaker.is_open:
      → probe on half-open transition, skip otherwise
    else:
      for attempt in range(1, retry + 1):
        try:
          result = handler(**kwargs)
          → reset retry counter + circuit breaker
          → _handle_telemetry_outcome (publish / persist / clear-error)
          break
        except retry_on_exceptions:
          → log WARNING with attempt number
          if attempt < retry:
            await ctx.sleep(backoff.delay(attempt))
          else:
            → retry exhausted: _handle_telemetry_error as today
            → increment circuit breaker consecutive failures
    await ctx.sleep(interval)
```

The publish strategy only sees successful results. `OnChange` deduplication, `Every`
immediate publish — all work unchanged. A retry that eventually succeeds produces a
result that flows through the normal pipeline. A retry that exhausts all attempts flows
through `_handle_telemetry_error()` as today.

### Decision 5: No retry on `@app.command`

Commands have fire-once semantics — user-initiated, expecting immediate response.
Retry is restricted to `@app.telemetry` for v1. If a command handler needs retry, the
app implements it manually.

### Retry during shutdown

Retry attempts use `ctx.sleep()` (shutdown-aware). If shutdown is requested during a
backoff wait, the sleep completes immediately and the retry loop exits cleanly — no
further attempts are made.

### Retry and coalescing groups

For grouped handlers (`group=`), retry applies **per handler invocation within the
group tick**.  Handlers in a batch execute sequentially (matching the existing
scheduler design), so a retrying handler's backoff delays affect subsequent
handlers in the same tick.

The group runner executes retries inline and does **not** currently preempt or
abandon in-flight retries when the nominal group tick interval is exceeded.  If a
handler's retries run long, the current batch completes first and the next group
tick is effectively delayed; subsequent ticks are scheduled after the batch
finishes.

### Error deduplication interaction

Retry attempts are **not** published as individual errors. Only the final failure
(after all retries exhausted) flows through `_handle_telemetry_error()` and the
existing deduplication logic. This prevents flooding the error topic with transient
failures that may self-resolve.

### Implementation location

| Component | File |
| --------- | ---- |
| `BackoffStrategy` protocol + implementations | `_retry.py` (new) |
| `CircuitBreaker` class | `_retry.py` (new) |
| Retry loop integration | `_telemetry_runner.py` |
| New registration fields | `_registration.py` |
| Decorator parameters | `_app.py` |
| Public API exports | `__init__.py` |
| Introspection updates | `_introspect.py` |

## Decision Drivers

- **Disproportionate data gaps.** A single transient failure at 25-minute intervals
  causes a 25-minute gap. Retry with 2s→4s→8s backoff recovers within seconds.
- **No app has solved this.** Four apps accept the gap — framework-level solution
  benefits all.
- **Existing backoff precedent.** `_mqtt_client.py` already implements exponential
  backoff with jitter. The telemetry retry should follow the same pattern.
- **Configurable from day one.** Backoff needs vary: BLE adapters benefit from
  exponential, CalDAV from fixed delays, serial from linear. Making it pluggable
  via a protocol avoids redesign later.
- **Circuit breaker prevents futile retries.** When an adapter is truly down (not
  transient), retrying every cycle wastes resources and floods logs.

## Considered Options

### Option A: Flat decorator parameters, per-cycle reset

`retry=3`, `retry_on=(OSError,)`, `backoff=2.0` as flat float parameters. Retry
counter resets each cycle. No circuit breaker.

- *Advantages:* Minimal API surface. Simple implementation.
- *Disadvantages:* Fixed exponential only — no strategy choice. Per-cycle reset means
  backoff never grows beyond a single cycle, limiting effectiveness. No protection
  against prolonged outages.

### Option B: `RetryPolicy` object, cumulative counter, circuit breaker (chosen)

Backoff strategies via protocol. Cumulative retry counter with reset on success.
Optional circuit breaker with open/half-open/closed states.

- *Advantages:* Pluggable strategies. Meaningful exponential backoff across cycles.
  Circuit breaker prevents futile retries. Full observability via health reporter.
- *Disadvantages:* More API surface. Circuit breaker adds state machine complexity.
  Cumulative counter requires careful interaction with error deduplication.

### Option C: Tenacity library integration

Wrap handler calls with [tenacity](https://github.com/jd/tenacity) retry decorators.

- *Advantages:* Battle-tested. Rich retry features out of the box.
- *Disadvantages:* New dependency. Doesn't integrate with `ctx.sleep()` for
  shutdown-awareness. Leaks third-party API into public surface. Can't expose
  circuit breaker state in health reporter.

## Decision Matrix

| Criterion | A: Flat/per-cycle | B: Policy/cumulative | C: Tenacity |
| --------- | :---------------: | :------------------: | :---------: |
| Backoff flexibility | 2 | 5 | 5 |
| Shutdown awareness | 4 | 5 | 2 |
| Observability | 2 | 5 | 2 |
| API simplicity | 5 | 3 | 3 |
| No new dependencies | 5 | 5 | 2 |
| Covers long outages | 2 | 5 | 4 |
| **Total** | **20** | **28** | **18** |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Transient failures recover in seconds instead of waiting a full poll interval.
  For airthings2mqtt (25 min), this reduces worst-case data gaps from 25 minutes
  to ~14 seconds (3 retries with exponential backoff).
- Backoff strategy protocol is open for extension — users can implement custom
  strategies without framework changes (Open/Closed Principle).
- Circuit breaker prevents infinite retry churn during prolonged outages, with
  clear state visibility in health heartbeats.
- Retry is transparent to publish strategies — no changes needed to `OnChange`,
  `Every`, or future strategies.
- Follows the existing MQTT reconnect backoff model, maintaining consistency
  within the framework.
- Cumulative counter across cycles means exponential backoff is meaningful even
  for short-interval telemetry.

### Negative

- Three new public types (`ExponentialBackoff`, `LinearBackoff`, `FixedBackoff`,
  `CircuitBreaker`) increase the API surface.
- Circuit breaker state machine (closed/open/half-open) adds complexity to
  `_telemetry_runner.py` — careful testing of state transitions is essential.
- Cumulative retry counter interacts with error deduplication — must ensure
  only the final exhausted failure publishes an error, not intermediate attempts.
- Retry within coalescing groups may cause handler execution to extend beyond the
  group tick — the "abandon stale retry" rule must be clearly documented.

## Amendment (2026-07-12) — Additive

**Rationale:** Production incident (airthings2mqtt, framework finding F-3): a handler hanging mid-await permanently wedged the device poll task — no log, no error publish, no retry, and the {topic}/set re-read trigger permanently blocked. ADR-024 retry only engages when a handler raises; a hung handler that never raises makes the retry machinery unreachable. This amendment adds an optional per-handler invocation timeout backstop that composes with the existing retry/backoff pipeline with zero new runner branches.

### Additional Sub-Decision: Decision 6 (Amendment): Handler invocation timeout backstop

### Problem

ADR-024's retry only engages when the handler *raises*. A handler that *hangs*
mid-`await` — e.g. a BLE/serial/HTTP call with no internal timeout — never raises,
so the bare `await reg.func(**kwargs)` in `_telemetry_runner.py` (`_try_invoke()`)
blocks the device's single persistent poll task permanently. There is no log entry,
no error publish, no retry, and the `{topic}/set` re-read trigger is never reached
again.

**Production evidence (F-3):** airthings2mqtt's retained state topic went silent for
approximately 10 days with `RestartCount 0` and zero log lines. The BLE adapter had
no internal timeout on the characteristic read, so the asyncio event loop was blocked
indefinitely.

### Decision

Add an optional `timeout` parameter to `@app.telemetry`, `app.add_telemetry()`, and
`Router.telemetry` that bounds each handler invocation.

```python
@app.telemetry(
    interval=300,
    timeout=60.0,          # backstop: raise TimeoutError if handler runs > 60 s
    retry=3,               # TimeoutError ⊂ OSError → auto-retried (Decision 1)
)
async def read_sensor(adapter: BlePort) -> dict[str, object]:
    ...

# Explicitly disable backstop for a legitimately long-running handler:
@app.telemetry(interval=3600, timeout=None)
async def daily_sync(...): ...
```

**Type:** `TimeoutSpec = float | Callable[..., float]` — a deferred spec mirroring
`IntervalSpec` (ADR-020). The value may be a literal `float` (seconds), a
Settings-derived callable, or (with `name=callable` multi-device) a per-device
callable, resolved at bootstrap exactly like `interval`.

**Semantics (four cases):**

| `timeout=` value | Resolved backstop |
|-----------------|-------------------|
| omitted | auto-default: resolved `interval` (via named constant `_DEFAULT_TIMEOUT_FACTOR = 1.0`) |
| `None` | disabled — no backstop; matches Python ecosystem convention (`asyncio.wait_for(coro, None)`) |
| explicit `float` | used as-is |
| `Callable` | deferred-resolved at bootstrap, exactly like `interval` (ADR-020) |

Cron-scheduled telemetry (`schedule=`) receives **no auto-default** — a daily cron
period would be a useless hang bound. Cron handlers opt in with an explicit `timeout=`.

**Enforcement in `_telemetry_runner.py`:**

When `reg.timeout is not None`, the runner wraps the handler await:

```python
# _telemetry_runner.py — _try_invoke()
if reg.timeout is not None:
    result = await asyncio.wait_for(reg.func(**kwargs), reg.timeout)
else:
    result = await reg.func(**kwargs)
```

`asyncio.CancelledError` (shutdown signal) is not caught here — it propagates
unchanged, as today.

**Composition with retry (zero new branches — cross-reference Decision 1 and Decision 4):**

`asyncio.wait_for` raises `TimeoutError`, which is a subclass of `OSError` since
PEP 3151 / Python 3.3. Because the existing default `retry_on=(OSError,)` already
covers `TimeoutError`, a timed-out handler automatically flows through the full
existing pipeline:

1. Logged at WARNING (attempt number, exception type)
2. Error-published to `{topic}/error` with ADR-011 deduplication by exception type
3. Device health set to `"error"` (ADR-012)
4. Retried with backoff when `retry > 0` (Decision 4)

No special-case timeout branch is needed anywhere in the runner. This is the key
elegance: adding a timeout backstop strictly *reuses* the retry/error/health
machinery — it is a new input to the existing flow, not a parallel flow.

**Introspection:**

`timeout` is added to the telemetry registry snapshot in `_mcp/_introspect.py`
(`_describe_telemetry`), for parity with `retry`/`interval`. It is **not** surfaced
in the published AsyncAPI `_meta/registry` (parity with retry — resilience knobs are
introspection-only).


### Additional Positive Consequences

- Fixes the F-3 permanent-wedge class of bug for every @app.telemetry app, not just BLE adapters — the framework cannot know whether a given adapter call has its own internal timeout, so a universal backstop is the only reliable defence.
- Composes with existing retry/backoff/error-publishing/health machinery with zero new runner branching: TimeoutError is a subclass of OSError (PEP 3151, Decision 1), so it is automatically covered by the default retry_on=(OSError,) with no special-case code path.
- Consistent mental model: timeout is deferred-resolved exactly like interval (ADR-020), including Settings and per-device callable forms, so users learn one resolution pattern and apply it to both parameters.

### Additional Negative Consequences

- Behavior change (breaking-ish): existing interval-based handlers now receive an implicit timeout equal to their poll interval unless they opt out with timeout=None. Because the runner sleeps interval *after* each cycle (interval is an inter-cycle gap, not a fixed-rate deadline), a handler that legitimately runs longer than its poll interval could be cut off unexpectedly. Mitigations: the _DEFAULT_TIMEOUT_FACTOR constant is trivially tunable, users can set an explicit larger timeout=, or disable entirely with timeout=None. Migration and what's-new notes must document this.
- asyncio.wait_for cancels the inner coroutine on timeout and awaits its cleanup, so wall-clock elapsed time may slightly exceed the nominal timeout value — standard asyncio semantics. This is still finite versus the current unbounded hang.
- Adds a resolution step and API surface parallel to interval (more code and more tests), justified by consistency with the deferred-resolution model already used for interval (ADR-020).
