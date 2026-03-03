# ADR-018: Telemetry Coalescing Groups

## Status

Accepted **Date:** 2026-03-03

## Context

In cosalette's execution model, each `@app.telemetry` handler runs as an independent
`asyncio.Task` with its own sleep/execute/publish loop. When multiple handlers share a
physical resource — such as a serial bus, SPI interface, or rate-limited API — and
use the same or harmonically-related polling intervals, each handler opens a **separate**
resource session at roughly the same wall-clock moment.

This causes several problems:

- **Session overhead** — repeated handshake/teardown costs. For example, six P300 serial
  sessions at t=300 s spend ~1.2 s on handshakes instead of ~0.2 s in a single session.
- **Bus contention** — rapid session cycling stresses the downstream device or bus
  controller.
- **Timing drift** — independent sleep loops drift apart because each handler sleeps
  *after* its own (varying-length) execution, not from a shared epoch.
- **Scalability** — adding signal groups linearly increases session count.

The solution must work at the cosalette framework level so that projects with shared
adapter resources benefit from a single, well-tested mechanism.

### Key Requirements

1. At t=0 (startup), all grouped handlers fire in a single shared batch.
2. At coinciding ticks (e.g. t=3600 where both 300 s and 3600 s intervals fire),
   handlers share one batch.
3. Arbitrary intervals (300, 400, 550) must coalesce whenever they coincide.
4. The mechanism must be a first-class framework feature.
5. The user-facing API must be explicit and readable — an opt-in `group=` parameter.

## Decision

Add **coalescing groups** to cosalette's telemetry API — a new optional `group`
parameter on `@app.telemetry()` and `app.add_telemetry()` that declares which handlers
should share execution windows when their intervals coincide.

Handlers in the same coalescing group are managed by a shared **tick-aligned
scheduler** that:

- Uses a **heapq priority queue** of `(fire_time_ms, handler_index)` entries to
  compute a global timeline of fire events.
- Groups all handlers due at the same tick into a sequential **batch**.
- Executes the batch in registration order within a single execution window —
  enabling adapter session sharing for resources like serial buses.
- Uses **integer-millisecond tick arithmetic** (`_to_ms()`) to avoid floating-point
  accumulation errors (e.g. `300 * 12 == 3600` exactly).
- Preserves per-handler publish strategies, error isolation, persistence policies,
  and init functions.

Handlers without a `group` parameter (or in different groups) run independently,
preserving full backward compatibility.

### User-Facing API

```python
# Decorator form
@app.telemetry(name="outdoor", interval=300, group="optolink")
async def poll_outdoor(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["outdoor_temp"])

# Imperative form
app.add_telemetry(
    name="outdoor",
    func=handler,
    interval=300,
    group="optolink",
)
```

### Scheduler Algorithm

```text
1. INIT phase
   - Run each handler's init function (if any)
   - Exclude handlers whose init raises (error isolation)
   - Build a min-heap with (0, i) for each active handler i

2. MAIN LOOP
   a. Peek at the next fire time from the heap
   b. Sleep until that time (shutdown-aware)
   c. Pop all entries with the same fire time → batch[]
   d. Execute batch sequentially in registration order
   e. Reschedule each handler: push (fire_time + interval_ms, i)
   f. Repeat until shutdown or heap empty

3. CLEANUP
   - Save device stores for all active handlers
```

## Decision Drivers

- Minimize resource sessions for slow shared interfaces (serial, SPI, rate-limited APIs)
- Deterministic tick-aligned timing eliminates drift between grouped handlers
- Explicit `group=` parameter makes coalescing visible and intentional
- Framework-level solution benefits all cosalette projects, not just one application
- Full backward compatibility — ungrouped handlers work identically
- Per-handler semantics (publish strategy, error isolation, persistence) remain intact

## Considered Options

### Option A: Global Tick-Aligned Scheduler (Implicit)

Replace all independent telemetry loops with a single global scheduler. All handlers
are automatically coalesced regardless of whether they share resources.

- *Advantages:* Maximum coalescing. Simple mental model — one scheduler for everything.
  Deterministic timing.
- *Disadvantages:* Implicitly changes execution model for all handlers. Reduces backward
  compatibility. Hides the coalescing intent from the reader. Handlers that don't share
  resources gain nothing but lose independent timing.

### Option B: Adapter Keep-Alive

Keep independent handler tasks. Make the adapter smart enough to hold sessions open
between rapid calls using idle timeouts.

- *Advantages:* No framework changes. Works with existing code. Adapter controls its
  own lifecycle.
- *Disadvantages:* Relies on timing heuristics (idle timeouts) with no guarantee of
  coalescing. No session sharing at t=0 startup. Doesn't solve timing drift. Pushes
  framework-level concerns into adapter implementations.

### Option C: Coalescing Groups (Explicit `group=` Parameter) — Chosen

Users declare which handlers share execution windows via an opt-in `group` parameter.
A per-group tick-aligned scheduler manages batching.

- *Advantages:* Explicit and self-documenting. Deterministic timing. Full backward
  compatibility. Per-handler semantics preserved. Works for any shared resource type.
- *Disadvantages:* New concept for users to learn (mitigated: opt-in, single parameter).
  Sequential batch execution is suboptimal for independent resources (mitigated: only
  affects handlers that explicitly opted in).

## Decision Matrix

| Criterion                       | A: Global Scheduler | B: Keep-Alive | C: Coalescing Groups |
| ------------------------------- | :-----------------: | :-----------: | :------------------: |
| Satisfies all 5 requirements    |          4          |       2       |          5           |
| Framework generalizability      |          5          |       1       |          4           |
| API clarity and readability     |          3          |       5       |          5           |
| Implementation complexity       |          3          |       3       |          3           |
| Deterministic timing (no drift) |          5          |       2       |          5           |
| Backward compatibility          |          3          |       5       |          5           |
| Handles arbitrary intervals     |          5          |       3       |          5           |
| Session sharing at t=0          |          5          |       2       |          5           |
| **Total**                       |        **33**       |     **23**    |        **37**        |

*Scale: 1 (poor) to 5 (excellent)*

Option C scores highest because it combines deterministic tick-aligned scheduling with
explicit user intent. The `group=` parameter makes the coalescing behavior readable and
intentional — developers can see at a glance which handlers share resources.

Option A was close but penalised for implicitly changing the execution model for all
handlers and for hiding the coalescing intent from the reader.

Option B was rejected because it relies on timing heuristics that provide no guarantee
of coalescing, especially at startup.

## Consequences

### Positive

- Resource sessions reduced from N (one per handler) to 1 per coinciding tick
- Deterministic tick alignment eliminates timing drift between grouped handlers
- Explicit `group=` parameter is self-documenting and immediately visible in
  registration code
- Full backward compatibility — existing ungrouped handlers work identically
- Per-handler semantics preserved: each handler retains its own publish strategy,
  error recovery, persistence policy, and init function
- Other cosalette projects can use coalescing groups for SPI buses, rate-limited APIs,
  or any shared-resource scenario

### Negative

- New framework concept for users to learn (mitigated by being opt-in with a clear,
  single-parameter API)
- Scheduler adds code complexity to cosalette's core execution path
- Within a batch, handlers execute sequentially — for adapters with independent
  resources this is suboptimal (mitigated: only affects handlers that explicitly opted
  into the same group)
- Integer-millisecond tick arithmetic requires sub-millisecond intervals to be rounded
  (mitigated: telemetry intervals are typically seconds to minutes)

_2026-03-03_
