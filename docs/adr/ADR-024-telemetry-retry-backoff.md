# ADR-024: Telemetry Retry with Configurable Backoff

## Status

Accepted **Date:** 2026-03-31

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
- **Introspection endpoint:** retry config, current attempt count, circuit state,
  and consecutive failure count included in the registry snapshot.
- **Logging:** state transitions logged at WARNING level.

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

For grouped handlers (`group=`), retry applies **per handler within the group tick**.
Handlers in a batch execute sequentially (matching the existing scheduler design),
so a retrying handler's backoff delays affect subsequent handlers in the same tick.
If a handler's retries extend beyond the group tick interval, the next tick starts
normally and the stale retry is abandoned.

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
| `RetryPolicy` (aggregates retry config) | `_retry.py` (new) |
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

_2026-03-31_
