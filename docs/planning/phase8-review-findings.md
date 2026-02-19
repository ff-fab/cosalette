# Phase 8.4 Review Findings — Architecture Decisions

Two review findings from PR #17 warrant analysis beyond simple doc fixes. Both involve
mismatches between documentation and framework behavior, and in both cases the question
is: **fix the docs, fix the code, or both?**

---

## Finding 1: Shutdown Hook Execution Order

### Current Behavior

In `_app.py` lines 381–382, the teardown sequence is:

```python
# --- Phase 4: Tear down ---
await self._run_hooks(self._shutdown_hooks, app_context, "Shutdown")  # hooks first
await self._cancel_tasks(device_tasks)                                 # then cancel
await health_reporter.shutdown()
```

**Shutdown hooks run BEFORE device tasks are cancelled.**

The documentation (lifecycle-hooks.md) says the opposite: "After devices stop, before
MQTT disconnects" and shows the diagram with device cancellation before hooks.

### What Users Need

A shutdown hook's primary use case is **resource cleanup**: closing serial ports,
releasing GPIO pins, flushing caches, disconnecting databases. There are two competing
concerns:

1. **Safety:** If a hook closes a resource while devices are still running, a device
   could attempt I/O on a closed resource during its next loop iteration — causing
   spurious errors in the brief window before cancellation.

2. **Signalling:** Some users may want shutdown hooks to _prepare_ for shutdown (e.g.,
   telling a device to enter a safe state, logging diagnostics) while devices are
   still alive.

### Option A: Fix docs only (keep current code behavior)

Hooks run before device cancellation. Document this accurately.

- **Advantages:**
  - Zero code change, no risk of regression
  - Matches Starlette/FastAPI's `on_shutdown` behavior (hooks run while the server
    can still serve requests)
  - Allows "preparation" hooks that interact with live devices

- **Disadvantages:**
  - The dominant use case (resource cleanup) is unsafe: closing a serial port in
    `on_shutdown` while a telemetry loop is still reading from it creates a race
    condition
  - Users must add defensive checks (`if self._conn is not None`) in every adapter
    method to guard against use-after-close
  - Counter-intuitive: "on shutdown" implies "things have shut down", not "things
    are about to shut down"

### Option B: Fix code only (hooks after device cancellation)

Move `_run_hooks(shutdown_hooks)` to after `_cancel_tasks(device_tasks)`.

```python
# --- Phase 4: Tear down ---
await self._cancel_tasks(device_tasks)                                 # cancel first
await self._run_hooks(self._shutdown_hooks, app_context, "Shutdown")   # then hooks
await health_reporter.shutdown()
```

- **Advantages:**
  - Resource cleanup in hooks is safe — no device can be using the resource
  - Matches the mental model most users will have ("on shutdown = after things stop")
  - Matches the principle of least surprise
  - The existing documentation is already correct for this ordering
  - Simpler contract: "hooks run in a guaranteed-quiet state"

- **Disadvantages:**
  - If a user wanted to do something while devices are still alive, they can't — but
    this use case is unusual and arguably belongs in a different mechanism (e.g., a
    `pre_shutdown` hook or a shutdown flag that devices check)
  - Code change in `_app.py` — needs tests updated if any rely on current ordering

### Option C: Both — two hook phases

Introduce `on_pre_shutdown` (runs before device cancellation) and keep `on_shutdown`
(runs after). This gives users both options.

```python
# --- Phase 4: Tear down ---
await self._run_hooks(self._pre_shutdown_hooks, app_context, "Pre-shutdown")
await self._cancel_tasks(device_tasks)
await self._run_hooks(self._shutdown_hooks, app_context, "Shutdown")
await health_reporter.shutdown()
```

- **Advantages:**
  - Maximum flexibility — both use cases covered
  - Clear semantic distinction: pre_shutdown = preparation, on_shutdown = cleanup
  - Follows the pattern used by some frameworks (Django has `pre_shutdown` signals)

- **Disadvantages:**
  - API surface increases (new decorator, new hook list, new concept to document)
  - YAGNI risk — the `pre_shutdown` use case may be extremely rare for IoT daemons
  - More complexity for a framework that values simplicity ("FastAPI for MQTT")

### Recommendation

**Option B** — fix the code. Move hooks to after device cancellation.

**Reasoning:**

1. cosalette targets IoT daemons. The overwhelming use case for `on_shutdown` is
   "close hardware connections." Making this safe by default is more important than
   supporting the rare "interact with live devices during teardown" case.

2. The Starlette comparison is misleading. In Starlette, `on_shutdown` runs while the
   HTTP server can still serve responses — because HTTP requests are independent and
   short-lived. In cosalette, device tasks are long-lived coroutines with shared
   hardware resources. The concurrency model is different, so the shutdown semantics
   should be different.

3. If `pre_shutdown` is ever needed, it can be added later (Option C extends Option B).
   Starting with the simpler, safer contract leaves the door open without committing
   to extra API surface now. YAGNI.

4. The docs already describe the "hooks after devices" ordering, so Option B actually
   requires _fewer_ changes overall.

---

## Finding 2: Command Handler Error Publication

### Current Behavior

The error handling chain for command handlers is:

```
MQTT message arrives
  → MqttClient._dispatch()                 # catches exceptions, logs them
    → TopicRouter.route()                   # extracts device, calls handler
      → _proxy()                            # calls ctx.command_handler
        → user's @ctx.on_command function   # may raise
```

When the user's handler raises, the exception propagates back to `_dispatch()`, which
catches it with `logger.exception()` and does nothing else. **No error payload is
published to MQTT.**

Compare with telemetry errors:

```python
# _run_telemetry — errors ARE published
except Exception as exc:
    logger.error("Telemetry '%s' error: %s", reg.name, exc)
    await error_publisher.publish(exc, device=reg.name)
```

And device-level errors:

```python
# _run_device — errors ARE published (when the whole device crashes)
except Exception as exc:
    logger.error("Device '%s' crashed: %s", reg.name, exc)
    await error_publisher.publish(exc, device=reg.name)
```

So telemetry errors → published. Device crashes → published. **Command handler
errors → logged only.** This is an asymmetry.

### What Users Need

Command handler errors are important. If someone sends `"blink"` to a device that
only supports `"on"/"off"/"toggle"`, the error should be observable:

- **For monitoring dashboards** — operators need to know commands are failing
- **For Home Assistant** — it can display error state to the user
- **For debugging** — log files may not be readily accessible on embedded devices

### Option A: Fix docs only (keep current behavior)

Document that command handler exceptions are logged-only, not published. Users who
want MQTT error publication must catch exceptions themselves and publish manually.

- **Advantages:**
  - Zero code change
  - Keeps `_dispatch` simple — it's a generic callback dispatcher, not
    cosalette-specific
  - Manual error handling gives users full control over payload content

- **Disadvantages:**
  - Asymmetric: telemetry errors auto-publish, command errors don't. This is
    surprising and forces users to write boilerplate for a common case
  - Users must manually construct and publish error payloads — but they don't have
    easy access to `ErrorPublisher` from inside a command handler (it's not on
    `DeviceContext`)
  - IoT devices should be observable by default — swallowing errors silently in
    a daemon is the worst failure mode

### Option B: Fix code — publish command handler errors via ErrorPublisher

Wire error publication into the command dispatch path. This could be done at the
`TopicRouter.route()` level or at the `_proxy()` level in `_wire_router()`.

**Approach B1: Wrap the proxy in `_wire_router()`** (cleanest, avoids touching router
or MQTT client):

```python
async def _proxy(
    topic: str,
    payload: str,
    _ctx: DeviceContext = dev_ctx,
    _ep: ErrorPublisher = error_publisher,
    _name: str = reg.name,
) -> None:
    handler = _ctx.command_handler
    if handler is not None:
        try:
            await handler(topic, payload)
        except Exception as exc:
            logger.error("Command handler error for '%s': %s", _name, exc)
            await _ep.publish(exc, device=_name)
```

This means command handler exceptions would:
1. Be caught in the proxy
2. Published as structured error payloads (like telemetry errors)
3. Then `_dispatch()` never sees the exception (it's already handled)

**Approach B2: Give TopicRouter an error_publisher** — inject `ErrorPublisher` into
`TopicRouter` and handle errors in `route()`.

- **Advantages:**
  - Symmetric behavior: all device errors (telemetry, device crash, command) are
    published to MQTT
  - Observable by default — the IoT daemon philosophy
  - Users don't need to manually catch and publish
  - Small, localized code change (just the proxy function in `_wire_router()`)
  - `error_type_map` can be wired through `ErrorPublisher` in the future

- **Disadvantages:**
  - The command handler loop continues after an error (it should — and this is
    already the behavior), so some might argue the error is "handled" and doesn't
    need publication. But telemetry loops also continue after errors, and those are
    published
  - Slightly increases coupling: the proxy now knows about `ErrorPublisher`

### Option C: Both — publish errors AND give users manual control

Publish automatically (Option B), AND also expose `error_publisher` or a convenience
method on `DeviceContext` so users can publish custom error payloads. This is additive
to Option B.

- **Advantages:**
  - Best of both worlds: automatic for simple cases, manual for advanced
  - Users who want custom `error_type_map` support can do it themselves until the
    framework wires it through

- **Disadvantages:**
  - Larger API surface
  - Can be done incrementally (B now, expose on ctx later)

### Recommendation

**Option B (approach B1)** — fix the code by wrapping the proxy in `_wire_router()`.

**Reasoning:**

1. **Symmetry.** The framework already publishes errors for telemetry and device
   crashes. Not publishing for command handlers is an oversight, not a deliberate
   design choice. ADR-011 says "all errors should be observable via MQTT" — command
   handler errors should be no exception.

2. **Minimal change.** The fix is ~6 lines in `_wire_router()`. It doesn't change
   `TopicRouter` (which is correctly a pure router) or `MqttClient._dispatch()`
   (which is a generic dispatcher). The error-handling concern stays in `_app.py`
   where all other error publication lives.

3. **IoT daemon philosophy.** Unattended daemons must be observable. Swallowing
   command errors into a log file that nobody reads on a Raspberry Pi is the worst
   failure mode. MQTT error topics are the monitoring channel.

4. **Forward-compatible.** When `error_type_map` is eventually wired to `App` and
   `ErrorPublisher`, command handler errors will automatically benefit. If we only
   fix the docs now, that future feature would require _another_ doc update.

---

## Summary

| Finding   | Recommendation | Scope       | Risk  |
|-----------|---------------|-------------|-------|
| Shutdown hooks order | Fix code (hooks after cancel) | `_app.py` line 381–382 swap | Low — behavioral change but docs already describe the new behavior |
| Command error publish | Fix code (proxy wraps handler) | `_app.py` `_wire_router()` | Low — adds error publication, doesn't change existing paths |

Both changes should include corresponding test updates and documentation corrections.

## Next Steps

1. Get user approval on both recommendations
2. Implement code changes + tests in the same PR (or a follow-up)
3. Correct documentation to match the (now correct) code
