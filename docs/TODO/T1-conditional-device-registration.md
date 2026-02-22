# T1: Conditional Device Registration

## Status

Deferred — phase trigger: "when building the next app that needs conditional devices"

## Context

The `if app.settings.enable_debug_device:` pattern in gas2mqtt works for now because
`app.settings` is eagerly available. But it's a static import-time decision based on one
settings snapshot. If settings were ever hot-reloadable, this would break.

A more framework-native approach would be an `enabled=` parameter on `@app.telemetry` and
`@app.device` decorators:

```python
@app.telemetry(
    "magnetometer",
    interval=app.settings.poll_interval,
    enabled=app.settings.enable_debug_device,  # or a callable
)
```

## Option A: Boolean `enabled=` Parameter

**What it does:** `@app.telemetry(enabled=False)` registers the device but skips task creation
in `_start_device_tasks`.

**Advantages:**

- Device is always registered (visible in introspection, health reporting)
- Simple boolean, resolved at registration time from `app.settings`
- Backward compatible (default `enabled=True`)

**Disadvantages:**

- Still static — can't change at runtime
- Device context is still built even if the device won't run

## Option B: Callable `enabled=`

**What it does:** `enabled=lambda s: s.enable_debug_device` resolved at `_run_async` time.

**Advantages:**

- Mirrors the (rejected) lazy interval pattern — consistent API
- Could support runtime re-evaluation in a future hot-reload feature

**Disadvantages:**

- More complex API
- Lambda syntax is noisy (the reason we chose eager settings over lambdas)

## Option C: Keep Current Pattern

**What it does:** `if app.settings.flag:` before the decorator — plain Python.

**Advantages:**

- Zero framework complexity
- Explicit, readable, no magic
- Works today

**Disadvantages:**

- Device doesn't appear in health reporting when disabled
- Not discoverable via framework introspection

## Recommendation

Option C (current pattern) is adequate. Revisit if framework introspection or health
reporting for disabled devices becomes a requirement.
