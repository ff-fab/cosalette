# ADR-023: `on_configure` Lifecycle Phase and Dict-Name Device Registration

## Status

Accepted **Date:** 2026-03-31

## Context

Three early-adopter apps (caldates2mqtt, velux2mqtt, gas2mqtt) need to register
devices dynamically based on parsed settings. Today they use `app.settings` at module
level to iterate config and call `app.add_device()` / `app.add_telemetry()`:

```python
app = App(name="myapp", settings_class=MySettings)
_settings = MySettings()  # ← crashes --help/--version if env vars missing

for cal in _settings.calendars:
    app.add_telemetry(name=cal.name, func=handler, interval=cal.interval)
```

This pattern has three problems:

1. **`--help`/`--version` crash.** Settings construction fails when required env vars
   are absent, before the CLI can handle help flags.
2. **Double construction.** Settings are constructed once at module level and again by
   the CLI with `--env-file` support. The module-level instance is stale.
3. **No DI access.** Module-level code cannot access adapters or other framework
   services — they don't exist yet.

ADR-020 (Deferred Interval Resolution) solved the interval aspect with
`IntervalSpec = float | Callable[[Settings], float]`, but explicitly noted that an
`on_configure` hook was "too disruptive" at v0.1.x. Now at v0.2.x, the framework is
ready for this lifecycle addition.

## Decision

### Part A: `@app.on_configure` Lifecycle Hook

Add an `@app.on_configure` decorator that registers a hook function. The hook runs
**after adapter construction but before adapter `__aenter__`** (Option B), receiving
resolved `Settings` and adapter instances via DI.

```python
app = App(name="myapp", settings_class=MySettings)

@app.on_configure
def setup_devices(settings: MySettings) -> None:
    for cal in settings.calendars:
        app.add_telemetry(name=cal.name, func=handler, interval=cal.interval)
```

#### Lifecycle insertion point

The hook runs between adapter resolution (step 4) and `enter_lifecycle_adapters`
(step 10) in the current lifecycle:

```text
Settings resolved
    ↓
Adapters constructed (DI: Settings + adapter instances)
    ↓
on_configure hooks executed   ← NEW
    ↓
resolve_intervals()           ← moved after hooks (was step 2)
    ↓
Enter lifecycle adapters (__aenter__)
    ↓
Wire devices
    ↓
Lifespan
    ↓
Device tasks
```

`resolve_intervals()` must move after the hooks because hooks may register new
telemetry with callable intervals.

#### Hook semantics

- **Multiple hooks allowed**, executed in registration order.
- **Both sync and async hooks supported.** The framework detects async via
  `inspect.iscoroutinefunction()` and `await`s accordingly.
- **DI-injectable parameters:** `Settings` (and subclass), adapter port types,
  `Logger`, `ClockPort`. `DeviceContext` is not available (devices don't exist yet).
- **Hooks cannot register adapters.** Adapters must be registered via `App()`
  constructor or `app.adapter()` before `run()`. The hook is for
  device/telemetry/command registration only.
- **Exceptions are fatal.** An exception in a hook propagates out of `_run_async()`
  and crashes the process with a clear message — the app cannot start without its
  devices.

### Part B: Dict-Name Device Registration

As a convenience shorthand for the common multi-device pattern, the `name=` parameter
on `@app.telemetry`, `@app.device`, and `@app.command` accepts a callable that
returns either `dict[str, T]` or `list[str]`:

```python
@app.telemetry(
    name=lambda s: {cal.name: cal for cal in s.calendars},
    interval=lambda cal: cal.poll_interval,
)
async def read_calendar(cal: CalendarConfig, adapter: CalDavPort) -> Reading:
    ...
```

The framework calls `name(settings)` during device wiring. For `dict[str, T]`:

- Each key becomes a device name.
- Each value is registered in per-device DI by `type(value)`.
- The handler declares the value type as a parameter to receive it via injection.

For `list[str]`:

- Each element becomes a device name.
- No per-device config is injected (equivalent to a loop of `add_telemetry(name=x)`).

#### Per-device config injection

Dict values are registered in per-device DI scope by `type(value)`. The handler
declares the type as a parameter and the framework injects the per-device value.
If both a dict value and an `init=` return value produce the same type, the framework
raises `ValueError` at registration time.

#### Per-device interval resolution

When `name=` returns `dict[str, T]` and `interval=` is callable:

- **Callable interval + dict-name → always per-device.** The callable receives the
  per-device config value `T`, not `Settings`.
- **Float interval + dict-name → shared.** All devices use the same interval.
- **Per-device interval + `group=` → `ValueError`.** Coalescing groups require a
  shared interval by definition.

This convention avoids annotation inspection for lambdas and keeps the rule simple:
dict-name + callable interval = per-device.

## Decision Drivers

- **Three independent workarounds.** caldates2mqtt, velux2mqtt, and gas2mqtt all
  independently invented module-level settings access patterns with the same failure
  modes. A framework-level solution eliminates the pattern.
- **`--help`/`--version` must never crash.** Module-level settings construction is
  the root cause. Deferring registration to `on_configure` (after settings are
  resolved by the CLI) eliminates this class of failure.
- **DI consistency.** The hook should follow the same injection model as `init=`
  callbacks and handler functions — declare types, receive instances.
- **Incremental adoption.** Both `on_configure` and dict-name are additive.
  Existing module-level registration (including `IntervalSpec` from ADR-020) continues
  to work unchanged.

## Considered Options

### Option A: After Settings, before adapter construction

Hook runs before adapters are constructed — only `Settings` is injectable.

- *Advantages:* Simple insertion point, no adapter dependencies.
- *Disadvantages:* Can't conditionally register devices based on adapter type or
  instance. Limited DI surface.

### Option B: After adapter construction, before `__aenter__` (chosen)

Hook runs after adapters are constructed and available in DI, but before their
lifecycle methods execute.

- *Advantages:* Full DI surface (Settings + adapters). No side effects yet — hooks
  run in a clean state. Matches `init=` callback DI model.
- *Disadvantages:* Can't probe hardware through adapters (not connected yet). Rare
  need — the driving apps only need Settings to enumerate devices from config.

### Option C: After adapter `__aenter__`, before device wiring

Hook runs after adapters are fully started (connected, initialised).

- *Advantages:* Can probe hardware for dynamic device discovery (e.g., scan BLE for
  available sensors).
- *Disadvantages:* Adds startup latency. Complex error handling — if the hook fails,
  entered adapters must be cleaned up. Couples device registration to adapter
  runtime state.

## Decision Matrix

| Criterion | A: Pre-adapter | B: Post-construct | C: Post-enter |
| --------- | :------------: | :----------------: | :------------: |
| DI surface | 2 | 5 | 5 |
| Simplicity | 5 | 4 | 2 |
| Error handling | 4 | 4 | 2 |
| Covers driving use cases | 3 | 5 | 5 |
| No side-effect risk | 5 | 5 | 2 |
| **Total** | **19** | **23** | **16** |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- `--help`/`--version` never crashes due to device registration code — hooks only
  run inside `_run_async()`, well after CLI handling.
- Settings are constructed once, by the CLI, with `--env-file` support. No stale
  instances.
- Adapters are available in DI during registration, enabling conditional device
  setup based on adapter type.
- Dict-name provides a one-liner for the common "N devices from config" pattern,
  replacing 3–10 lines of loop boilerplate per app.
- Per-device DI injection via dict values eliminates manual closure capture or
  `functools.partial` patterns.
- Fully backward-compatible — existing module-level registration, `IntervalSpec`
  (ADR-020), and `init=` callbacks work unchanged.

### Negative

- Two registration mechanisms to document: module-level decorators vs. `on_configure`
  hook. Clear guidance needed on when to use each.
- `resolve_intervals()` moves later in the lifecycle, slightly changing the error
  surface — validation errors from callable intervals now occur after `on_configure`
  rather than at the top of `_run_async()`.
- Dict-name adds complexity to the decorator parameter space — the `name=` parameter
  now accepts `str | list[str] | Callable[[Settings], dict[str, T] | list[str]]`.
- Per-device interval convention (callable + dict-name = per-device) is implicit.
  Clear documentation and error messages are essential.

_2026-03-31_
