---
status: Accepted
date: 2026-03-31
impact: high
tags: [mqtt, architecture]
---

# ADR-025: Command Channel and Sub-Topic Routing

## Status

Accepted **Date:** 2026-03-31

## Context

Three command-handling mechanisms exist today:

1. **`@app.command("name")`** — module-level standalone async function with
   DI-injected parameters. Best for stateless, fire-and-forget commands.
2. **`@ctx.on_command`** — runtime callback registered on `DeviceContext`. Single
   handler slot per device; `DeviceContext` raises `RuntimeError` on duplicate
   registration. Best for devices that react to commands but don't need a
   long-running loop.
3. **`@app.device` with manual loop** — escape hatch for devices that maintain
   complex state machines across command boundaries (a cover controller tracking
   position, a GPIO-driven actuator with multi-step sequences).

The third pattern is where problems concentrate. Devices with long-running command
loops repeatedly reinvent three workaround patterns:

**Inline execution in callback.** The `@ctx.on_command` handler does all the work
directly. This blocks on long-running commands and prevents concurrent processing —
a cover controller that takes 30 seconds to reach a target position cannot accept
new commands during traversal.

**Explicit `asyncio.Queue` bridge.** The developer creates a queue, forwards
commands from the callback into it, and `await`s from the queue in the device loop.
This works correctly but adds ~20 lines of mechanical boilerplate per device:

```python
@app.device("actuator")
async def actuator_device(ctx):
    queue: asyncio.Queue[str] = asyncio.Queue()

    @ctx.on_command
    async def handle(topic, payload):
        await queue.put(payload)

    while not ctx.shutdown_requested:
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=5)
            await process(payload)
        except TimeoutError:
            await periodic_check()
```

**`nonlocal` variable mutation.** The callback sets a flag or value via closure,
and the device loop polls it — fragile, race-prone, and difficult to reason about
under concurrent access.

All three workarounds exist because `DeviceContext` has no built-in mechanism to
bridge commands into a running device loop.

### Sub-topic problem

Devices that handle multiple command types — position commands alongside
calibration commands, or mode-switch commands alongside parameter-set commands —
must currently demultiplex by payload inspection. The single `{prefix}/{device}/set`
topic carries all command types, so handlers parse JSON payloads to determine intent.
This couples command routing to payload structure and makes adding new command types a
change to the parsing logic rather than a new subscription.

MQTT's topic hierarchy is the natural routing mechanism. A cover controller should
receive position commands on `{prefix}/cover/set` and calibration commands on
`{prefix}/cover/calibrate/set` — separate topics, separate handlers, no payload
inspection needed.

### No Command dataclass

Handlers receive raw `(topic: str, payload: str)` tuples with no metadata —
no timestamp, no sub-topic context. Downstream code that needs to know which
sub-topic a command arrived on must re-parse the topic string.

## Decision

Introduce two complementary features: a queue-backed async iterator on
`DeviceContext` and sub-topic routing via `@ctx.on_command`.

### Command dataclass

A frozen, slotted dataclass represents inbound commands:

```python
@dataclass(frozen=True, slots=True)
class Command:
    topic: str
    payload: str
    sub_topic: str | None = None
    timestamp: float = 0.0
```

Immutable and hashable. Commands are messages from MQTT — semantically they should
not be mutated after receipt. `sub_topic` is `None` for root-level commands
(`{device}/set`). `timestamp` defaults to `0.0` and is set by the framework at
receipt time.

### `ctx.commands()` async iterator

`DeviceContext` gains a `commands(timeout: float | None = None)` method that
returns an async iterator backed by an internal `asyncio.Queue[Command]`:

```python
@app.device("actuator")
async def actuator_device(ctx):
    async for cmd in ctx.commands(timeout=5):
        if cmd is None:
            await periodic_check()
        else:
            await process(cmd.payload)
```

When `timeout` is provided, the iterator yields `None` on timeout expiry,
enabling periodic-work patterns without external `asyncio` wrappers. Without
`timeout`, the iterator blocks until a command arrives or `ctx.shutdown_requested`
becomes true. Internally, a 1-second polling interval checks the shutdown flag.

### Sub-topic routing

`@ctx.on_command` accepts an optional sub-topic string:

```python
@ctx.on_command("calibrate")
async def handle_calibrate(topic, payload):
    await run_calibration(payload)
```

This subscribes to `{prefix}/{device}/calibrate/set`. The root
`@ctx.on_command` (no argument) continues to subscribe to
`{prefix}/{device}/set`.

Topic segment order is `{device}/{sub}/set` — sub-topic before the `/set`
suffix. This matches Home Assistant and Zigbee2MQTT conventions and is
consistent with ADR-002 where `/set` is the terminal command suffix.

Sub-topic depth is limited to a single level. The sub-topic string must not
contain `/`; the framework raises `ValueError` at registration time if it does.
The `TopicRouter` currently rejects topics with `/` in the device segment —
single-level sub-topics preserve this invariant.

### Per-sub-topic exclusivity

`ctx.commands()` and `@ctx.on_command` cannot both handle the same sub-topic on
the same device. Attempting to register both raises `RuntimeError` at
registration time. However, they **can** coexist on the same device for
**different** sub-topics:

```python
@app.device("controller")
async def controller_device(ctx):
    # Calibration: separate callback on sub-topic
    @ctx.on_command("calibrate")
    async def handle_calibrate(topic, payload):
        await run_calibration(payload)

    # Position: root commands via async iterator
    async for cmd in ctx.commands(timeout=60):
        if cmd is None:
            await periodic_maintenance()
        else:
            await set_position(cmd.payload)
```

This extends the existing single-handler constraint (`DeviceContext` already
raises `RuntimeError` on duplicate `on_command` registration) to the
per-sub-topic level.

### Command DI injection in `@app.command` handlers

`@app.command` handlers can declare `cmd: Command` to receive the full
`Command` object via dependency injection, in addition to the existing `topic`
and `payload` parameters:

```python
@app.command("reset")
async def handle_reset(cmd: Command) -> None:
    log.info("Reset at %s on %s", cmd.timestamp, cmd.topic)
    await do_reset(cmd.payload)
```

This makes the interface consistent across `@app.command` and `@app.device`
patterns — both can work with the structured `Command` object.

### Backward compatibility

Existing `@ctx.on_command` handlers with `(topic, payload)` signatures continue
to work unchanged. The `Command` dataclass is additive — handlers that don't
declare it never see it. Sub-topic routing is opt-in; devices with no sub-topic
argument behave exactly as before.

## Decision Drivers

- **Three independent workarounds.** The queue bridge, inline execution, and
  nonlocal mutation patterns appear across multiple device implementations.
  A framework-level primitive eliminates the pattern.
- **MQTT-native routing.** Topic hierarchies are the protocol's built-in
  demultiplexing mechanism. Payload inspection for command routing works against
  the grain of MQTT.
- **ADR-002 consistency.** The `/set` suffix convention naturally extends to
  `{device}/{sub}/set` without inventing new conventions.
- **Structured command metadata.** Handlers need timestamp and sub-topic context
  without re-parsing raw topic strings.
- **Backward compatibility.** Both features are additive — no existing API
  changes, no migration required.

## Decision Matrix

Decision 1 (exclusivity model) is the most nuanced trade-off with three viable
options. The remaining five decisions are binary or near-obvious — their
rationale is covered in Considered Options below.

| Criterion                   | A: Fully exclusive | B: Exclusive per sub-topic (chosen) | C: Non-exclusive (both receive) |
| --------------------------- | ------------------ | ----------------------------------- | ------------------------------- |
| Simplicity of mental model  | 5                  | 4                                   | 2                               |
| Flexibility for mixed cases | 1                  | 4                                   | 5                               |
| Risk of double-processing   | 5                  | 4                                   | 1                               |
| Backward compatibility      | 4                  | 5                                   | 3                               |

_Scale: 1 (poor) to 5 (excellent)_

## Considered Options

### Decision 1: Exclusivity model

**Option A: Fully exclusive.** `ctx.commands()` and `@ctx.on_command` cannot
coexist on the same device at all. Simple rule, but too restrictive — devices
that want a main command loop plus a separate calibration callback would need
to route everything through the queue.

**Option B: Exclusive per sub-topic (chosen).** Each sub-topic has exactly one
owner. Allows clean separation of concerns on devices with multiple command
types while preventing accidental double-processing.

**Option C: Non-exclusive (both receive).** Both the callback and the queue
receive every command. Maximally flexible, but introduces ordering ambiguity
and makes it easy to process commands twice.

### Decision 2: Command dataclass mutability

**Option A: Mutable dataclass.** Allows handlers to annotate commands with
processing metadata. Rejected — commands are inbound messages, not working
state. Mutation invites action-at-a-distance bugs when commands are shared.

**Option B: Frozen dataclass (chosen).** Immutable and hashable. Matches the
semantic nature of MQTT messages and enables safe passing across async
boundaries.

### Decision 3: Command DI in `@app.command`

**Option A: No injection — handlers parse topic/payload manually.** Status quo.
Keeps the DI surface small but forces handlers that need metadata to re-derive
it.

**Option B: Injectable (chosen).** Handlers declare `cmd: Command` to opt in.
Consistent with the framework's existing DI model (Settings, adapters, Logger).
Non-breaking — handlers without the parameter are unaffected.

### Decision 4: Sub-topic depth

**Option A: Single level only (chosen).** `@ctx.on_command("calibrate")` maps
to `{device}/calibrate/set`. Simple, predictable, preserves the
`TopicRouter`'s single-segment device constraint.

**Option B: Arbitrary depth.** `@ctx.on_command("calibrate/step")` maps to
`{device}/calibrate/step/set`. More expressive, but complicates topic matching,
introduces ambiguity in wildcard subscriptions, and breaks the `TopicRouter`'s
assumption that device names are single segments.

### Decision 5: Topic segment order

**Option A: `{device}/{sub}/set` (chosen).** Sub-topic before suffix. Matches
Home Assistant conventions (`light/brightness/set`) and keeps `/set` as the
terminal segment per ADR-002.

**Option B: `{device}/set/{sub}`.** Suffix before sub-topic. Allows wildcard
`{device}/set/+` to catch all command types, but breaks the convention that
`/set` terminates the topic and is inconsistent with the broader ecosystem.

### Decision 6: Timeout model

**Option A: No built-in timeout.** Callers wrap with `asyncio.wait_for` or
`asyncio.timeout`. Pushes boilerplate back to the user — the exact problem
`ctx.commands()` is solving.

**Option B: Built-in timeout parameter (chosen).** `ctx.commands(timeout=60)`
yields `None` on timeout. Clean periodic-work pattern without external
wrappers. The `None` sentinel is explicit — callers must handle it, which
prevents silent timeout bugs.

**Option C: Separate `ctx.commands_with_timeout()` method.** Avoids the
`Command | None` union type by splitting the API. Rejected — two methods for
the same underlying queue is unnecessary indirection.

## Consequences

### Positive

- Eliminates three boilerplate workaround patterns (inline, queue bridge,
  nonlocal mutation) with a single framework primitive
- Command routing via MQTT topics — natural for the protocol — instead of
  payload inspection
- Structured `Command` object with timestamp and sub-topic metadata
- Per-sub-topic exclusivity prevents accidental double-processing while
  allowing mixed patterns on the same device
- Fully backward-compatible — no breaking changes to existing APIs
- Timeout model enables clean periodic-work patterns without external
  `asyncio` wrappers
- Consistent DI model — `Command` is injectable in `@app.command` the same
  way `Settings` and adapters are

### Negative

- `ctx.commands()` yield type is `Command | None` when `timeout` is used —
  callers must handle the union
- Internal queue adds a small per-command allocation
- Sub-topic routing changes `TopicRouter` internals — must handle the
  `{device}/{sub}/set` pattern alongside existing `{device}/set`
- `@app.command` DI injection adds one more annotation-inspection path to the
  framework's startup logic

### Neutral

- `@app.command` (module-level) and `@ctx.on_command` (device-level callback)
  remain the recommended patterns for simple devices; `ctx.commands()` is for
  devices that need a control loop with command integration

## References

- ADR-002: MQTT Topic Conventions
- ADR-010: Device Archetypes
- ADR-023: `on_configure` Lifecycle Phase

_2026-03-31_
