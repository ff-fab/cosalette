# Cosalette: Deferred Settings Access During Registration

**Date:** 2026-03-04
**Cosalette version:** 0.1.7
**Trigger:** vito2mqtt workspace-4zb — `--help`/`--version` crash without env vars

## Problem Statement

Applications that use `app.settings` during `add_telemetry()` / `add_command()`
registration (e.g. to read polling intervals from configuration) crash when the user
runs `vito2mqtt --help` or `vito2mqtt --version` without setting required environment
variables.

The `--help` and `--version` flags are handled by Typer *inside* the CLI callback,
which means they never reach the code that needs valid settings. But the crash happens
earlier — at module import time — because registration functions access `app.settings`
to provide a concrete `interval: float` to `add_telemetry()`.

## Timeline of Execution

```text
1. Entry point imports module           → module-level code runs
2. App() constructed                    → settings_class() tried eagerly
                                          ValidationError caught → _settings = None ✅
3. register_telemetry(app) called       → accesses app.settings for intervals
                                          _settings is None → RuntimeError 💥
   ──── crash before CLI starts ────
4. app.cli() would build Typer CLI      → --help/--version handled here (never reached)
5. CLI callback would build settings    → _settings_class(_env_file=...) (never reached)
6. _run_async(settings=...) would start → resolved settings override _settings
```

## Root Cause Analysis

### What works well

- **`App.__init__`** is intentionally lenient — it wraps `settings_class()` in
  `try/except ValidationError` and stores `None` on failure. This is correct
  fail-gracefully behaviour.

- **`build_cli()`** re-instantiates settings properly in the callback body, with
  `--env-file` support. `--version` and `--help` are handled before that point via
  Typer's `is_eager=True` mechanism.

- **`_run_async(settings=...)`** accepts a settings override via `_resolve_settings()`,
  so the CLI-built settings take precedence over the eagerly-constructed ones.

### The gap: registration needs data that isn't available yet

The core tension is a **chicken-and-egg problem** between two phases:

| Phase | Needs | Has |
|-------|-------|-----|
| Registration (`add_telemetry`) | `interval: float` from settings | No valid settings yet |
| CLI callback (`main()`) | Registrations already done | Valid settings from env/file |

`add_telemetry(interval=float)` requires a **concrete value at registration time**.
The interval is stored in a frozen `_TelemetryRegistration` dataclass and used verbatim
by `_run_telemetry()` / `_run_telemetry_group()` to control `ctx.sleep()` durations.
There is no mechanism to resolve the interval later.

### Secondary gap: CLI-rebuilt settings don't update intervals

Even when the CLI successfully builds settings (step 5), those settings are passed to
`_run_async()` but **never used to update the already-registered intervals**. The
intervals were baked into frozen `_TelemetryRegistration` objects during step 3. This
means:

- If an application hard-codes default intervals at registration time (as a workaround),
  those defaults stick even when the user provides different values via env vars.
- The `--env-file` override only affects settings used *inside* handler functions
  (via DI), not the scheduler intervals.

## Impact on Applications

Any cosalette application that follows the natural pattern of reading intervals from
settings at registration time hits this issue:

```python
# main.py — the natural pattern (currently broken for --help)
app = App(name="myapp", settings_class=MySettings)

# This accesses app.settings → RuntimeError if env vars missing
for group in GROUPS:
    app.add_telemetry(
        name=group,
        func=make_handler(group),
        interval=app.settings.get_interval(group),  # 💥
    )

cli = app.cli  # entry point
```

## Proposed Solutions

### Option A: `interval` accepts `float | Callable[[Settings], float]`

Allow `add_telemetry(interval=...)` to accept a callable that receives the resolved
`Settings` instance. The callable is invoked once when `_run_async()` starts
(after settings are properly built):

```python
# In _run_telemetry / _run_telemetry_group:
effective_interval = (
    reg.interval(resolved_settings) if callable(reg.interval)
    else reg.interval
)
```

**Application usage:**

```python
app.add_telemetry(
    name="outdoor",
    func=handler,
    interval=lambda s: s.polling_outdoor,  # resolved at runtime
)
```

**Advantages:**
- No change needed to the registration pattern — factory remains at module level
- Settings are resolved exactly once, at the right point in the lifecycle
- Type-safe: `Callable[[Settings], float]` is clear intent
- Backward compatible: `float` literals still work

**Disadvantages:**
- `_TelemetryRegistration.interval` type changes from `float` to `float | Callable`
- Must be resolved before the scheduler loop, adding a resolution step
- Validation (`interval > 0`) can't happen at registration time for callables

### Option B: `on_configure` lifecycle hook

Add a `@app.on_configure` hook that receives the resolved `Settings` and runs after
settings are built but before the scheduler starts.  Applications register telemetry
inside this hook:

```python
@app.on_configure
def setup(settings: MySettings) -> None:
    for group in GROUPS:
        app.add_telemetry(
            name=group,
            func=make_handler(group),
            interval=settings.get_interval(group),
        )
```

**Advantages:**
- Clean separation of "declare app" from "configure with settings"
- `interval: float` stays a concrete value
- Full validation at hook invocation time

**Disadvantages:**
- Changes the application's registration pattern fundamentally
- Breaks the current module-level registration style that works well otherwise
- `add_telemetry` would need to be callable after `__init__` but before `run`
  (currently possible, just not a documented pattern)
- Harder to test — modules no longer export a fully-configured `app`

### Option C: Resolve intervals from settings in `_run_async`

After `_resolve_settings()` in `_run_async`, walk `self._telemetry` and update
intervals from settings. Requires either:
- A convention (settings field names match device names), or
- An `interval_key: str` parameter on `add_telemetry` that names the settings field

```python
app.add_telemetry(
    name="outdoor",
    func=handler,
    interval=300.0,           # default / placeholder
    interval_key="polling_outdoor",  # resolved from settings at runtime
)
```

**Advantages:**
- Registration works without settings
- Clear mapping from registration to settings field

**Disadvantages:**
- Frozen `_TelemetryRegistration` needs to become non-frozen (or replaced)
- Couples the framework to settings field naming conventions
- Less flexible than Option A's callable approach

## Recommendation

**Option A** is the cleanest solution with the best developer ergonomics. It:

1. Solves the immediate `--help`/`--version` crash
2. Solves the secondary gap (CLI-rebuilt settings updating intervals)
3. Maintains the existing module-level registration pattern
4. Is fully backward-compatible
5. Requires minimal framework changes (type widening + one resolution step)

The resolution point should be in `_run_async()` (or its sub-methods), immediately after
`_resolve_settings()` returns. Pseudocode:

```python
# In _run_async, after resolved_settings = self._resolve_settings(settings):
for i, reg in enumerate(self._telemetry):
    if callable(reg.interval):
        resolved = reg.interval(resolved_settings)
        if resolved <= 0:
            raise ValueError(f"interval for {reg.name!r} must be positive, got {resolved}")
        # Replace frozen dataclass instance
        self._telemetry[i] = dataclasses.replace(reg, interval=resolved)
```

## Workaround (vito2mqtt-side, until framework is updated)

Guard `app.settings` access in registration functions and fall back to pydantic model
field defaults:

```python
def register_telemetry(app: App) -> None:
    try:
        settings = app.settings
    except RuntimeError:
        settings = None

    for group_name in SIGNAL_GROUPS:
        interval = (
            _get_interval(settings, group_name)
            if settings is not None
            else Vito2MqttSettings.model_fields[_INTERVAL_ATTR[group_name]].default
        )
        app.add_telemetry(name=group_name, func=..., interval=interval, ...)
```

This is safe because:
- `--help`/`--version` never reach `_run_async()`, so placeholder intervals don't matter
- Real invocations always have valid settings (CLI validates before `_run_async`)
- The intervals happen to match the defaults, so behaviour is correct when settings exist

## Related

- vito2mqtt ADR-005: Configuration & Settings
- vito2mqtt task workspace-4zb
- cosalette `_TelemetryRegistration` (frozen dataclass, `interval: float`)
- cosalette `build_cli` → CLI callback rebuilds settings
