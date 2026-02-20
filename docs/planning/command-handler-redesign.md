# Command Handler Redesign: FastAPI-Style `@app.command()`

## Problem Statement

The current `@app.device` + `@ctx.on_command` pattern has a structural limitation: the
command handler is a nested function inside the device body, making it a local variable
that Pyright/Pylance flags as "not accessed." This is not a cosmetic issue — it means
**every code example in the documentation will produce a linter warning** when users
copy-paste it into their projects.

More fundamentally, the current pattern requires users to understand closures,
`nonlocal`, `asyncio.Event().wait()`, and nested decorator semantics to write a basic
command device. This is at odds with the project's "FastAPI for MQTT" identity.

## Current State

### Device Registration

```python
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    state = "closed"

    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        nonlocal state
        state = payload
        await ctx.publish_state({"state": state})

    await ctx.publish_state({"state": state})
    while not ctx.shutdown_requested:
        await ctx.sleep(30)
```

**Problems:**

1. `handle` triggers Pyright "not accessed" warning
2. User must understand closures, `nonlocal`, decorator-inside-coroutine
3. `await asyncio.Event().wait()` boilerplate for simple handlers
4. Command handler has fixed `(topic, payload)` signature — no injection
5. Testing requires simulating the full device lifecycle

### Telemetry Registration (Already FastAPI-Style)

```python
@app.telemetry("sensor", interval=5.0)
async def read_sensor() -> dict[str, object]:
    return {"temperature": 22.5}
```

This is clean: standalone function, injected params, framework-managed loop,
auto-published return value. **No linter warnings.**

## Proposal: `@app.command()` Decorator

Add a new `@app.command()` decorator modeled after `@app.telemetry()`:

```python
@app.command("valve")
async def handle_valve(
    topic: str,
    payload: str,
    ctx: cosalette.DeviceContext,
) -> dict[str, object]:
    return {"state": payload}
```

**How it works:**

- Module-level function → no "not accessed" warning
- Parameters injected by type, same as device/telemetry handlers
- `topic: str` and `payload: str` are positional — the framework passes these
- Return value auto-published via `ctx.publish_state()`
- Framework-managed lifecycle — no user-written loop

### With Adapters (Complex Case)

```python
@app.command("valve")
async def handle_valve(
    topic: str,
    payload: str,
    controller: ValveControllerPort,
    ctx: cosalette.DeviceContext,
) -> dict[str, object]:
    controller.actuate(payload)
    return {"state": controller.read_state()}
```

The `ValveControllerPort` adapter is already a singleton — shared across all handlers
that inject it.

### Combined Command + Polling

When a device needs both command handling AND periodic state polling, use two decorators:

```python
@app.command("valve")
async def handle_valve(
    topic: str,
    payload: str,
    ctrl: ValveControllerPort,
) -> dict[str, object]:
    ctrl.actuate(payload)
    return {"state": ctrl.read_state()}

@app.telemetry("valve", interval=10.0)
async def poll_valve(ctrl: ValveControllerPort) -> dict[str, object]:
    return {"state": ctrl.read_state()}
```

Both handlers inject the same adapter singleton and auto-publish state. No shared mutable
variable, no closure, no `nonlocal`. The hardware controller IS the source of truth.

## The Shared State Question

### When Adapters Are Sufficient (Most Cases)

In real IoT bridges, the "state" typically lives in the hardware or the adapter:

- `controller.read_state()` → reads from hardware
- `controller.actuate(payload)` → writes to hardware

The adapter is a singleton shared across all handlers. No `nonlocal` needed.

### When Explicit Shared State Is Needed

Some devices track application-level state that doesn't live in hardware (e.g., "last
command timestamp", "pending operations count"). For these cases, two options:

**Option S1: State in the adapter itself**

Design the adapter (port implementation) to hold mutable state. The adapter is already
a singleton shared across handlers:

```python
class ValveAdapter:
    def __init__(self) -> None:
        self.last_commanded: float | None = None

    def actuate(self, position: str) -> None:
        self.last_commanded = time.monotonic()
        ...
```

**Option S2: `Depends()` with scoped state (future enhancement)**

A FastAPI-style `Depends()` marker enabling factory-based dependencies with lifecycle
control:

```python
class ValveState:
    position: str = "closed"
    last_commanded: float | None = None

@app.command("valve")
async def handle(
    topic: str,
    payload: str,
    state: ValveState = cosalette.Depends(ValveState, scope="device"),
) -> dict[str, object]:
    state.position = payload
    return {"state": state.position}
```

`Depends()` is a significant feature in its own right. This proposal recommends
**deferring it** — adapters cover the vast majority of state-sharing needs. `Depends()`
can be added later as a non-breaking enhancement.

## What About `@app.device()`?

`@app.device()` stays. It's the escape hatch for genuinely complex stateful devices
that need:

- Custom async loops with non-trivial flow control
- Dynamic subscription management
- Complex state machines
- Long-running coroutines with setup/teardown phases

The guidance becomes:

| Pattern | Use When |
|---|---|
| `@app.command()` | Device reacts to MQTT commands. Most common. |
| `@app.telemetry()` | Device polls/streams data on an interval. |
| `@app.device()` | Complex lifecycle — custom loops, state machines. |

## Framework Changes Required

### New Components

| Component | File | Effort |
|---|---|---|
| `@app.command()` decorator | `_app.py` | Moderate |
| `_CommandRegistration` dataclass | `_app.py` | Simple |
| `_run_command()` method | `_app.py` | Moderate |
| Extended `build_injection_plan()` | `_injection.py` | Moderate |
| Return-value auto-publish logic | `_app.py` | Simple |

### Modified Components

| Component | Change | Effort |
|---|---|---|
| `_wire_router()` | Read handler from `_CommandRegistration` instead of proxy→ctx lookup | Moderate |
| `build_providers()` | Add `topic: str` and `payload: str` to providers for command handlers | Simple |
| `_subscribe_and_connect()` | Subscribe for command-registered devices | Simple |
| `__init__.py` | Export new public symbols | Simple |
| `DeviceContext.on_command()` | Keep as-is (backward compat for `@app.device` users) | None |

### `_CommandRegistration` Design

```python
@dataclass(frozen=True, slots=True)
class _CommandRegistration:
    """Internal record of a registered @app.command handler."""
    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
```

### `_run_command()` Design

Called per MQTT message, not as a long-running task:

```python
async def _run_command(
    self,
    reg: _CommandRegistration,
    ctx: DeviceContext,
    topic: str,
    payload: str,
    error_publisher: ErrorPublisher,
) -> None:
    try:
        providers = build_providers(ctx, reg.name)
        providers[str] = ...  # Need to handle topic/payload specially
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        result = await reg.func(**kwargs)
        if result is not None:
            await ctx.publish_state(result)
    except Exception as exc:
        await error_publisher.publish(exc, device=reg.name)
```

### Open Design Question: How to Inject `topic` and `payload`

Command handlers need `topic: str` and `payload: str` — but both are `str`, so
type-based injection can't distinguish them. Options:

**Q1: Positional convention** — first two `str` params are always `(topic, payload)`:

```python
@app.command("valve")
async def handle(topic: str, payload: str, ctx: DeviceContext) -> ...:
```

**Q2: Named convention** — params named `topic` and `payload` are matched by name, not
type:

```python
# Framework checks param.name == "topic" or param.name == "payload"
```

**Q3: Wrapper types** — `Topic` and `Payload` newtypes:

```python
from cosalette import Topic, Payload

@app.command("valve")
async def handle(topic: Topic, payload: Payload, ctx: DeviceContext) -> ...:
```

**Recommendation:** Q2 (named convention) is simplest and most readable. Falls back to
Q1 if names don't match. Q3 is overengineered for string params.

## Test Impact

| Test File | Affected Tests | Change Type |
|---|---|---|
| `test_app.py` | 3 command-routing tests | Update or add new tests |
| `test_context.py` | 5 `on_command` tests | Keep (backward compat) |
| `test_integration.py` | 2 command tests | Add `@app.command()` variants |
| `test_injection.py` | 0 | Add command handler injection tests |
| New: `test_command.py` | ~10-15 new tests | Full coverage of `@app.command()` |

## Documentation Impact

| File | Change |
|---|---|
| `docs/guides/command-device.md` | Rewrite to lead with `@app.command()` |
| `docs/concepts/device-archetypes.md` | Add command archetype, update table |
| `docs/guides/full-app.md` | Update examples |
| `docs/reference/api.md` | Add `@app.command()` reference |
| `docs/adr/ADR-010-device-archetypes.md` | Supersede or amend |
| `packages/cosalette_debug_app.py` | Update example |

## Implementation Phases

### Phase 1: Core `@app.command()` (Red → Green)

- Add `_CommandRegistration` dataclass
- Add `command()` decorator method
- Add `_run_command()` dispatch method
- Update `_wire_router()` for command registrations
- Extend injection to handle `topic`/`payload` params
- Tests: registration, routing, injection, error handling, return-value publishing

### Phase 2: Refactor Existing Tests and Examples

- Update integration tests to use both `@app.command` and `@app.device` styles
- Update debug app
- Ensure backward compatibility — `@app.device` + `@ctx.on_command` still works

### Phase 3: Documentation

- Rewrite command-device guide to lead with `@app.command()`
- Update all code examples across docs
- Add migration guidance: `@ctx.on_command` → `@app.command()`
- ADR for the decision

### Phase 4: `Depends()` (Deferred — Future Epic)

- `Depends()` sentinel class
- Dependency graph resolution
- Scoped lifecycle (`"request"`, `"device"`, `"app"`)
- Test overrides via `app.dependency_overrides`

## Decision Drivers

1. **Developer experience** — Copy-paste from docs must not produce warnings
2. **FastAPI alignment** — The framework's identity is "FastAPI for MQTT"
3. **Simplicity** — The 80% case should be trivial
4. **Backward compatibility** — `@app.device()` stays for power users
5. **Testability** — Standalone handlers are easier to unit test

## Risks

- **Two ways to handle commands** — `@app.command()` and `@app.device()` + `@ctx.on_command`.
  Mitigated by clear documentation: `@app.command()` is recommended, `@app.device()` is
  for advanced use cases.
- **topic/payload injection** — Injecting positional string params by name is unusual.
  Mitigated by Q2 (named convention) which feels natural.
- **Breaking mental model** — Existing users (if any) must learn the new pattern.
  Mitigated by keeping `@app.device()` fully backward-compatible.
