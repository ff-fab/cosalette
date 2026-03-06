# ADR-020: Deferred Interval Resolution

## Status

Accepted **Date:** 2026-03-04 | Amended **Date:** 2026-03-06

## Context

Cosalette's `add_telemetry()` and `@app.telemetry()` require an `interval: float`
parameter that controls the polling frequency. The interval is stored in a frozen
`_TelemetryRegistration` dataclass and used verbatim by `TelemetryRunner.run_telemetry()` and
`TelemetryRunner.run_telemetry_group()` (in `_telemetry_runner.py`) to control `ctx.sleep()` durations.

Applications naturally want to derive polling intervals from configuration:

```python
app = App(name="myapp", settings_class=MySettings)

for group in GROUPS:
    app.add_telemetry(
        name=group,
        func=make_handler(group),
        interval=app.settings.get_interval(group),  # reads from settings
    )
```

This pattern has two problems:

1. **`--help`/`--version` crash.** When required environment variables are absent,
   `App.__init__` catches the `ValidationError` and stores `_settings = None`
   (intentionally lenient). But `app.settings` then raises `RuntimeError`, crashing
   the process before the CLI can handle `--help`/`--version` — flags that don't
   need valid settings at all.

2. **Stale intervals.** The CLI callback (`build_cli`) re-creates settings with
   `--env-file` support and passes them to `_run_async()`. But the intervals were
   already baked into frozen `_TelemetryRegistration` objects during registration.
   The CLI-rebuilt settings never update the scheduler intervals — only settings
   accessed via DI inside handler functions reflect the `--env-file` values.

Both issues were discovered during the vito2mqtt early-adopter project (workspace-4zb),
where 7 telemetry groups derived their intervals from a `Vito2MqttSettings` subclass.

## Decision

Introduce `IntervalSpec = float | Callable[[Settings], float]` as the accepted type
for the `interval` parameter. When a callable is provided, it is resolved to a concrete
`float` in `_run_async()` — via `_wiring.resolve_intervals()` — after settings are
resolved, before any device tasks start.

```python
# Application usage — callable interval (deferred)
app.add_telemetry(
    name="outdoor",
    func=handler,
    interval=lambda s: s.polling_outdoor,  # resolved at runtime
)

# Still works — concrete float (immediate)
app.add_telemetry(name="sensor", func=handler, interval=30.0)
```

Resolution happens once, via `_wiring.resolve_intervals(settings)` called at the
top of `_run_async()`. After resolution, all `_TelemetryRegistration.interval` values
are concrete floats — downstream code (`TelemetryRunner.run_telemetry`,
`TelemetryRunner.run_telemetry_group`, `_init_group_handlers`) never sees callables.

### Validation

- **Float intervals:** Validated eagerly at registration time (`<= 0` raises
  `ValueError`). This is unchanged.
- **Callable intervals:** Validation is deferred to `_wiring.resolve_intervals()`. The
  callable is invoked with the resolved `Settings` instance, and the returned value
  is checked for `<= 0`. This is a necessary tradeoff — the callable's return value
  isn't known until settings exist.

### Type narrowing

Since `_TelemetryRegistration.interval` is typed as `IntervalSpec` (a union), downstream
code uses `cast(float, reg.interval)` for type narrowing. This is safe because
`_wiring.resolve_intervals()` guarantees all callables are resolved before downstream code
runs. `cast()` was chosen over `assert isinstance()` because:

- It's zero-cost at runtime (no per-sleep-cycle overhead)
- The invariant is established by a dedicated resolution step, not runtime checking
- `assert isinstance(x, float)` would reject `int` intervals (e.g. `interval=10`)

## Decision Drivers

- **First early-adopter failure.** The vito2mqtt project is the first real application
  built on cosalette. A crash on `--help` is a critical UX failure that blocks
  adoption.
- **Backward compatibility.** The framework is at 0.1.x with published adopters.
  Changing the registration pattern (e.g. an `on_configure` hook) would be too
  disruptive.
- **Resolve-at-boundary principle.** Callable intervals are resolved once at the
  `_run_async` boundary (delegated to `_wiring.resolve_intervals()`), converting the
  union to a concrete type. Downstream code operates on `float` only — no union
  handling needed in hot paths.

## Considered Options

### Option A: `IntervalSpec` — callable or float (Chosen)

Widen the `interval` parameter type to `float | Callable[[Settings], float]`. Resolve
callables in `_run_async()` after settings are available.

- *Advantages:* Backward-compatible. Module-level registration pattern preserved.
  Solves both the crash and the stale-interval problem. Minimal API surface change.
- *Disadvantages:* Validation deferred for callables. Union type adds `cast()` in
  downstream code.

### Option B: `on_configure` lifecycle hook

Add an `@app.on_configure` hook that receives resolved settings. Applications register
telemetry inside the hook instead of at module level.

- *Advantages:* Clean phase separation. `interval` stays `float`.
- *Disadvantages:* Breaking change to the registration pattern. Modules no longer
  export a fully-configured app. Harder to test. Would require rethinking the
  framework's ergonomic model.

### Option C: `interval_key` string mapping

Add an `interval_key: str` parameter that names a settings field. The framework
reads `getattr(settings, key)` at runtime to resolve the interval.

- *Advantages:* Registration works without settings. Simple.
- *Disadvantages:* Couples framework to settings field naming. Fragile (typos fail
  at runtime). Inflexible — can't express computed intervals.

## Decision Matrix

| Criterion | A: IntervalSpec | B: on_configure | C: interval_key |
| --------- | :-------------: | :-------------: | :-------------: |
| Backward compatibility | 5 | 2 | 4 |
| Registration ergonomics | 5 | 3 | 4 |
| Type safety | 4 | 5 | 2 |
| Implementation complexity | 4 | 3 | 3 |
| Solves stale-interval gap | 5 | 5 | 4 |
| **Total** | **23** | **18** | **17** |

*Scale: 1 (poor) to 5 (excellent)*

## Consequences

### Positive

- Applications can derive telemetry intervals from settings without crashing on
  `--help`/`--version`
- CLI-rebuilt settings (including `--env-file`) now influence scheduler intervals,
  closing the stale-interval gap
- Fully backward-compatible — existing `interval=5.0` code works identically
- Module-level registration pattern preserved — no architectural change required
- `IntervalSpec` type alias is exported as part of the public API, making the pattern
  discoverable

### Negative

- Callable intervals defer validation to `_run_async()` — errors surface later than
  they would for float intervals (mitigated: `_run_async` runs early in the lifecycle,
  before any device tasks start)
- Three downstream `cast(float, ...)` sites add a small maintenance burden
  (mitigated: the resolution step is tested and the pattern is documented)
- Lambda typing requires explicit annotation for type-checkers to know the settings
  subclass: `lambda s: s.my_field` treats `s` as `Settings`, not `MySettings`
  (mitigated: the base `Settings` type is sufficient for most use cases)

!!! note "Editorial note (2026-03-06)"
    Since this ADR was written, `_app.py` was decomposed into focused modules.
    Interval resolution now lives in `_wiring.resolve_intervals()` and the
    telemetry polling loop is implemented by `TelemetryRunner` in
    `_telemetry_runner.py`. The decision and its semantics are unchanged.

_2026-03-04_
