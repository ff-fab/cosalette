# Change Request: Mockable Sleep for Test Determinism

**Target:** cosalette framework
**Status:** Proposal (deferred)
**Created:** 2026-03-06
**Revisit when:** Integration test count exceeds ~50, or flakiness appears in CI

## Problem

Consumer integration tests that exercise the full `App._run_async()` lifecycle must use
real `asyncio.sleep()` waits because the framework's polling loop uses real sleeps
internally:

```python
# cosalette/_context.py — DeviceContext.sleep()
async def sleep(self, seconds: float) -> None:
    sleep_task = asyncio.ensure_future(asyncio.sleep(seconds))
    shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())
    done, pending = await asyncio.wait(
        {sleep_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    # ...
```

`FakeClock` controls `now()` (time-reading) but not `sleep()` (time-waiting). This
means:

- Tests must wait real wall-clock time for poll ticks to fire
- Faster-than-realtime testing is impossible
- Test durations scale linearly with the number of poll cycles needed

Current impact is low (18 tests, ~2–3 s total), but this becomes a bottleneck as the
integration test suite grows.

## Proposed Change

Make `DeviceContext.sleep()` delegate to a **clock-controlled sleep** so that
`FakeClock` can resolve sleeps instantly (or on-demand).

### Option A: Clock-Based Sleep (Recommended)

Extend `ClockPort` with an async `sleep()` method:

```python
# cosalette/_clock.py
class ClockPort(Protocol):
    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...

class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
```

Then `FakeClock` resolves sleeps instantly:

```python
# cosalette/testing/_clock.py
class FakeClock:
    _time: float = 0.0

    def now(self) -> float:
        return self._time

    async def sleep(self, seconds: float) -> None:
        self._time += seconds  # advance time, return immediately
```

`DeviceContext.sleep()` becomes:

```python
async def sleep(self, seconds: float) -> None:
    sleep_task = asyncio.ensure_future(self._clock.sleep(seconds))
    shutdown_task = asyncio.ensure_future(self._shutdown_event.wait())
    done, pending = await asyncio.wait(
        {sleep_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
```

**Advantages:**

- Zero wall-clock delay in tests — all sleeps resolve instantly
- `FakeClock._time` advances correctly, so time-dependent logic (coalescing groups,
  publish strategies) remains consistent
- Fully backward-compatible — `SystemClock` behaviour is unchanged
- Single Responsibility: clock owns both time-reading and time-waiting

**Disadvantages:**

- Requires a minor protocol extension (`sleep()` added to `ClockPort`)
- Existing consumers need `FakeClock` upgrade (but it's non-breaking — old
  `FakeClock` usage without `sleep()` would fail loudly at the protocol level)

### Option B: Event-Driven MockMqttClient (Consumer-Side Workaround)

Subclass `MockMqttClient` to fire an `asyncio.Event` on each publish, letting tests
`await mqtt.wait_for_publishes(n)` instead of sleeping.

**Why this is worse:**

- Framework still sleeps between ticks — you still wait for real time
- Requires knowing expected publish counts upfront (fragile coupling)
- Doesn't help time-dependent logic (strategies, coalescing)

## Impact on vito2mqtt

With Option A, vito2mqtt integration tests would change from:

```python
# Before: real wall-clock waits
await run_app_briefly(app, mqtt, settings, wait=0.3)
```

To:

```python
# After: instant — FakeClock.sleep() advances time and returns
harness = AppHarness.create(store=MemoryStore(), ...)
# register devices...
await harness.run()  # completes in ~milliseconds
```

The `AppHarness` already wires `FakeClock` — this change would make it usable for
full-lifecycle tests without real waits.

## Recommendation

**Defer until cosalette is ready for a minor release**, then implement Option A. In the
meantime, vito2mqtt's current `asyncio.sleep()` approach is adequate:

- 18 integration tests, ~2–3 s total runtime
- 6× safety margin on polling intervals
- No reported flakiness
- Tests are filterable via `@pytest.mark.slow`
