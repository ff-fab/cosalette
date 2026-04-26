# Plan: cos-0zk — `@app.periodic` Background Interval Tasks

**Status:** Draft — awaiting approval
**Beads task:** cos-0zk (PR3 of the current release train)

---

## Overview

Add `@app.periodic` — a decorator that registers a coroutine as a
background task that runs on a fixed interval. Periodic tasks are
**fire-and-forget**: they have no MQTT presence and no return value is
published. They exist for side-effects: cache warming, watchdog pings,
database sync, LED flashing, etc.

Key distinctions from `@app.telemetry`:

| Concern | `@app.telemetry` | `@app.periodic` |
|---|---|---|
| MQTT publish | Yes (state/events) | No |
| Return value | `dict \| None` | `None` (or ignored) |
| Retry / circuit breaker | Yes | No (log + continue) |
| Coalescing groups | Yes | No |
| Cron schedule | Yes | No (interval only) |
| per-device expansion | Yes | No (single handler) |

---

## Implementation Phases

### Phase 1 — Core data model (`_periodic.py` new)

**File:** `packages/src/cosalette/_periodic.py` (new)

Contents:
1. `_PeriodicRegistration` frozen dataclass with slots:
   - `name: str`
   - `func: Callable[..., Awaitable[None]]`
   - `injection_plan: list[tuple[str, type]]`
   - `interval: IntervalSpec` (= `float | Callable[..., float]`; `SettingRef` is
     already callable so it is covered for free)
   - `enabled_spec: EnabledSpec = True`
   - `init: Callable[..., Any] | None = None`
   - `init_injection_plan: list[tuple[str, type]] | None = None`
   - Contract metadata: `summary: str | None = None`, `behavior: list[str] | None = None`

2. `run_periodic()` async loop:
   ```python
   async def run_periodic(
       reg: _PeriodicRegistration,
       providers: dict[type, Any],
   ) -> None:
       """Sleep → invoke → repeat. Exceptions are logged, not propagated."""
       kwargs = resolve_kwargs(reg.injection_plan, providers)
       while True:
           await asyncio.sleep(reg.interval)   # interval is float at this point
           try:
               await reg.func(**kwargs)
           except asyncio.CancelledError:
               raise
           except Exception as exc:
               logger.error("Periodic '%s' error: %s", reg.name, exc)
   ```
   > **Design note (exception isolation):** Unlike `run_device()` which
   > reports errors via `ErrorPublisher`, periodic tasks have no MQTT
   > error channel. The framework logs at ERROR level and continues the
   > loop — matching `asyncio.TaskGroup` convention for background workers.

**Update `_registration.py`:** extend `_AnyRegistration` type alias to
include `_PeriodicRegistration`.

---

### Phase 2 — App registration (`_app.py`)

1. **Init:** add `self._periodic: list[_PeriodicRegistration] = []`

2. **Property:** `periodic_registrations -> Sequence[_PeriodicRegistration]`

3. **`registered_names()`:** add `self._periodic` to the comprehension so
   names collide with devices/telemetry/commands.

4. **Decorator `@app.periodic(name=None, *, interval, enabled=True, init=None,
   summary=None, behavior=None)`**

   - `name`: optional `str` (default: function `__name__`)
   - `interval`: required `IntervalSpec` — positive float or callable
   - `enabled`: `EnabledSpec` — same deferred-callable pattern as
     `@app.telemetry` (ADR-038)
   - Eagerness rules:
     - Literal `enabled=False` → skip registration silently (same as
       other decorators)
     - Callable `enabled=` → store spec; resolved at bootstrap
       (like ADR-038)
     - Literal `interval` ≤ 0 → raise `ValueError` immediately
     - Callable `interval` → stored; resolved at bootstrap (like ADR-020)
   - Name collision → `ValueError` (same check as other decorators)
   - Builds `injection_plan` via `build_injection_plan(func)` — same DI
     rules as `@app.device`
   - Appends `_PeriodicRegistration` to `self._periodic`

   > **Why same DI as `@app.device`, not `@app.telemetry`?**
   > Periodic tasks inject Settings, adapter ports, Logger, ClockPort, and
   > `@app.state` instances — exactly the same as device handlers. They
   > never inject `DeviceContext` because there is no MQTT lifecycle.

---

### Phase 3 — Wiring (`_wiring.py`)

**`resolve_intervals_periodic(periodic, settings)`** — parallel to the
existing `resolve_intervals(telemetry, settings)`:
```python
def resolve_intervals_periodic(
    periodic_list: list[_PeriodicRegistration],
    settings: Settings,
) -> None: ...
```
Mutates in-place, same validation (positive float).

**`resolve_enabled` extension:** add `periodic_list` parameter; extend
`_resolve_list_enabled` call to cover `self._periodic` (no deferred
validation needed — periodic tasks have no `persist=`/`triggerable=`
constraints).

**`start_periodic_tasks(periodic, providers)`** — new function:
```python
def start_periodic_tasks(
    periodic: list[_PeriodicRegistration],
    providers: dict[type, Any],
) -> list[asyncio.Task[None]]:
    tasks = []
    for reg in periodic:
        task = asyncio.create_task(
            run_periodic(reg, providers),
            name=f"periodic:{reg.name}",
        )
        tasks.append(task)
    return tasks
```

**`run_lifespan_and_devices()`:** receive and start periodic tasks;
add them to the `_cancel_phase_tasks` call.

**Shutdown grace (5 s):** wrap periodic-task cancellation in
`asyncio.wait_for(..., timeout=5.0)`:
```python
async def cancel_periodic_tasks(
    tasks: list[asyncio.Task[None]],
) -> None:
    for task in tasks:
        task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "%d periodic task(s) did not finish within 5 s grace period",
            sum(1 for t in tasks if not t.done()),
        )
```

**`_run_async` change:** pass `run_periodic: bool = True` parameter;
when `False`, skip `start_periodic_tasks()` and pass empty list.

---

### Phase 4 — AppHarness (`testing/_harness.py`)

1. **`create()` parameter:** `run_periodic: bool = False` — stored on
   `AppHarness` instance. When `False`, periodic tasks are not spawned
   during `harness.run()`. This keeps existing tests unaffected by any
   periodic registrations. Opt-in with `run_periodic=True`.

2. **`tick_periodic(name: str)`** — directly invoke one cycle:
   ```python
   async def tick_periodic(self, name: str) -> None:
       """Invoke one cycle of the named periodic handler (bypasses interval)."""
       from cosalette._periodic import _PeriodicRegistration
       reg = next(
           r for r in self.app._periodic if r.name == name
       )
       providers = _build_test_providers(self)   # shared helper
       kwargs = resolve_kwargs(reg.injection_plan, providers)
       await reg.func(**kwargs)
   ```
   This skips the sleep and runs the handler once — ideal for unit tests.

---

### Phase 5 — Tests (`packages/tests/unit/test_app_periodic.py`)

Test class groups:

| Class | Covers |
|---|---|
| `TestPeriodicRegistration` | Decorator stores registration; name collision; interval validation; DI plan built |
| `TestPeriodicEnabled` | Literal `False` skips; callable `enabled=` deferred; resolved at bootstrap |
| `TestPeriodicIntervalResolution` | Callable `interval=`; `SettingRef`; zero/negative raises |
| `TestPeriodicRuntime` | `run_periodic()` loop runs; exception logged, loop continues; `CancelledError` stops |
| `TestAppHarnessPeriodic` | `tick_periodic(name)` invokes handler; `run_periodic=False` skips spawning |

Technique: same patterns as `test_app_telemetry.py` — use `AppHarness`,
`FakeClock`, `MockMqttClient`.

---

### Phase 6 — ADR + Docs

**ADR-041** (`docs/adr/ADR-041-periodic-background-tasks.md`):
- Decision: new `@app.periodic` decorator as a separate, MQTT-free
  background task primitive
- Options: extend `@app.telemetry` vs. new type (chosen) vs. generic
  `@app.background`
- References ADR-020 (IntervalSpec), ADR-038 (EnabledSpec), ADR-001
  (declarative main.py goal)

**`docs/guides/periodic-tasks.md`** (new):
- Quick start, full example, DI notes, error handling note, testing with
  `tick_periodic`, `SettingRef` for interval

**`docs/concepts/device-archetypes.md`** (update):
- Add "periodic companion" pattern section: a `@app.periodic` running
  alongside a `@app.telemetry` to periodically flush a buffer

**`docs/concepts/lifecycle.md`** (update):
- Add periodic tasks to the phase diagram (spawned alongside device tasks,
  cancelled with grace period)

**`docs/reference/api.md`** (update):
- Document `App.periodic()` signature, `AppHarness.tick_periodic()`

---

## Open Questions

1. **`timedelta` support?** The task description mentions `timedelta` in
   the interval type. Should `interval=timedelta(seconds=30)` be accepted
   and converted to `30.0` at registration time? This is a minor ergonomic
   convenience. **Recommendation: yes** — accept `timedelta` and normalise
   to `float` in the decorator, before validation.

2. **`init=` parameter?** The task description doesn't explicitly call it
   out for periodic, but `@app.device` has `init=` for one-shot async
   setup. Given the DI-parity goal, including it keeps the API consistent.
   **Recommendation: yes, include it.**

3. **`_introspect.py` update?** The `ai introspect` tool lists registrations.
   Should periodic registrations appear there? **Recommendation: yes**
   (minor addition, good DX for downstream adopters).

---

## File Change Summary

| File | Change |
|---|---|
| `packages/src/cosalette/_periodic.py` | **New** — dataclass + runner |
| `packages/src/cosalette/_registration.py` | Extend `_AnyRegistration` |
| `packages/src/cosalette/_app.py` | `@app.periodic`, `self._periodic`, `registered_names`, `_run_async` |
| `packages/src/cosalette/_wiring.py` | `resolve_intervals_periodic`, `resolve_enabled`, `start_periodic_tasks`, `cancel_periodic_tasks`, `run_lifespan_and_devices` |
| `packages/src/cosalette/_injection.py` | Likely no change (DI rules already cover periodic) |
| `packages/src/cosalette/__init__.py` | No new public export needed (decorator is on `App`, not standalone) |
| `packages/src/cosalette/testing/_harness.py` | `run_periodic=`, `tick_periodic()` |
| `packages/tests/unit/test_app_periodic.py` | **New** — test suite |
| `docs/adr/ADR-041-periodic-background-tasks.md` | **New** |
| `docs/guides/periodic-tasks.md` | **New** |
| `docs/concepts/device-archetypes.md` | Update |
| `docs/concepts/lifecycle.md` | Update |
| `docs/reference/api.md` | Update |

---

*Next: awaiting approval to proceed with implementation.*
