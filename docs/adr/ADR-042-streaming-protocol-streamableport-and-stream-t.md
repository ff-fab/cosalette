---
status: Accepted
date: 2026-04-26
impact: moderate
tags: [architecture, lifecycle, di, devices]
---

# ADR-042: Streaming Protocol: StreamablePort and Stream[T]

## Status

Accepted **Date:** 2026-04-26

## Context

IoT hardware devices — BLE peripherals, serial adapters, HID sensors — deliver data via push callbacks rather than the pull-oriented async generator or polling loop that @app.telemetry assumes. There is no existing primitive in cosalette for bridging sync hardware callbacks into async iteration. Adapters working with such devices must currently roll their own Queue+Event patterns, duplicating boilerplate and risking inconsistent shutdown semantics.

## Decision

Add StreamablePort[T_co] (runtime-checkable Protocol) and Stream[T] (concrete AsyncIterator backed by asyncio.Queue + asyncio.Event) to packages/src/cosalette/_stream.py. StreamablePort defines the five-method lifecycle contract (open, close, start_scan, stop_scan, register_callback). Stream bridges sync push callbacks into async for loops by racing asyncio.Queue.get() against a shutdown asyncio.Event in __anext__ using asyncio.wait with FIRST_COMPLETED — no timeout polling, no busy-wait. Both types are exported from the top-level cosalette namespace. This file is also the first in the codebase to use PEP 695 type-parameter syntax (class Foo[T]) rather than classic TypeVar; a follow-up task (cos-rpp) will migrate the rest of the codebase for consistency.

```python
stream: Stream[SensorReading] = Stream()
port.register_callback(stream.put)
port.open()
port.start_scan()
try:
    async for reading in stream:
        ctx.publish(reading)
finally:
    port.stop_scan()
    port.close()
```

## Decision Drivers

- Hardware push adapters (BLE, serial, HID) cannot use async generators or polling — they fire callbacks on their own schedule
- Shutdown must be deterministic and latency-free: a shutdown event race eliminates polling delays and busy-waits
- The bridge pattern (Queue + Event) should be a first-class framework primitive, not repeated per-adapter boilerplate
- Protocol covariance (T_co) allows port adapters of subtypes to satisfy the StreamablePort[BaseType] contract

## Considered Options

### Option 1: asyncio.Queue + asyncio.Event race (chosen) (chosen)

Stream[T] holds an asyncio.Queue[T] and asyncio.Event. __anext__ creates two tasks (queue.get and event.wait) and races them with asyncio.wait(FIRST_COMPLETED). Shutdown wins over queued data, cancels the pending task, and raises StopAsyncIteration.

- *Advantages:* Zero polling latency: shutdown is detected in the same event loop tick; No timeout parameter needed — no tuning required; Fully deterministic in tests: advance queue or set event to control iterator; put() is sync put_nowait — safe to call from hardware callbacks without await
- *Disadvantages:* Creates two asyncio Tasks per __anext__ call — minor overhead compared to timeout polling; Task cancellation requires careful handling to avoid resource leaks

### Option 2: asyncio.wait_for with timeout polling

Stream.__anext__ calls asyncio.wait_for(queue.get(), timeout=0.1) in a loop, checking a shutdown flag on TimeoutError.

- *Advantages:* Simple implementation with no task management; Familiar pattern to developers who know asyncio.wait_for
- *Disadvantages:* Up to 100 ms shutdown latency by default — unacceptable for responsive shutdown; Timeout value must be tuned per deployment — adds configuration burden; Busy-waits during idle periods, consuming event loop ticks

### Option 3: Async generator adapter function

Provide an async generator function stream_from_port(port) that internally creates a Queue, registers the callback, and yields from the queue with event-based shutdown.

- *Advantages:* Async generator syntax is familiar and idiomatic; No class instantiation required — functional style
- *Disadvantages:* Cannot separate put() from the iteration context — callback registration is coupled to the generator lifetime; Harder to test in isolation: no Stream.put() to call directly in tests; Does not cleanly support reuse of the same stream across multiple consumers

## Decision Matrix

| Criterion | asyncio.Queue + asyncio.Event race (chosen) | asyncio.wait_for with timeout polling | Async generator adapter function |
| --- | --- | --- | --- |
| Shutdown latency | 5 | 2 | 4 |
| Test simplicity | 5 | 3 | 3 |
| Thread safety of put() | 5 | 5 | 3 |
| No tuning parameters required | 5 | 1 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Adapter authors get a zero-boilerplate bridge: create Stream(), call register_callback(stream.put), iterate with async for
- Shutdown semantics are explicit and consistent across all streaming adapters
- StreamablePort Protocol enables isinstance checks and DI injection for port adapters
- Sets the reference implementation pattern for PEP 695 type-parameter syntax in this codebase

### Negative

- Creates two asyncio Tasks per __anext__ invocation — negligible in IoT contexts but worth noting
- Items remaining in the queue at shutdown are silently dropped — callers must handle any required drain before calling shutdown()

_2026-04-26_
