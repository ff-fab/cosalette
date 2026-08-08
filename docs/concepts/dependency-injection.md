---
icon: material/needle
---

# Dependency Injection

Cosalette uses signature-based dependency injection to supply framework objects
(device context, settings, logger, adapters, etc.) to handler functions without
requiring explicit wiring code. This page explains how resolution works, what
constraints apply to markers and annotations, and how to reason about when
providers are resolved.

!!! info "Concept vs Reference"

    This page explains the **design and rules** of the DI system.
    For the complete **injectable-type table** and per-archetype resolution
    frequency table, see [Dependency Injection Reference](../reference/dependency-injection.md).
    For DI-related exceptions, see [Error Taxonomy](../reference/errors.md).

## How Resolution Works

At **registration time**, the framework inspects every handler's signature with
`typing.get_type_hints()` and builds an *injection plan* — a list of
`(param_name, annotation_type)` tuples (see `_injection.py`,
`build_injection_plan`). This step validates annotations and rejects invalid
ones eagerly so that errors surface before the app starts.

At **dispatch time**, `resolve_request_kwargs` walks the plan and looks up each
type in a *providers map* — a dict mapping types to live instances built from
`DeviceContext` and registered adapters. The resolved kwargs dict is then
passed to the handler via `handler(**kwargs)`.

### Resolution order

For a plain (non-`Annotated`) parameter, resolution tries three strategies in
order:

1. **Exact type match** — `providers[annotation]`
2. **Settings subclass match** — `isinstance(instance, annotation)` over all
   Settings-typed entries (`_find_settings_instance`)
3. **Adapter subclass match** — `issubclass(ptype, annotation)` over all
   provider entries (`_find_subclass_instance`)

Resolution is **never by parameter name** for the providers map. The only
name-based conventions are the shorthand bindings for `topic` (plain `str`)
and `payload` (non-`str` annotation) in command handlers — and even those
only activate when no `Annotated` marker is present.

### Subclass matching and ambiguity

When a parameter annotation matches multiple registered providers by subclass,
the framework raises `TypeError` ("Ambiguous provider…"). Only a single
distinct provider instance may match. If you have a port hierarchy and register
multiple subtype adapters, use a more specific annotation to disambiguate.

### Registration-time vs dispatch-time errors

Unknown types do **not** fail at registration. The plan records them and
defers the error to dispatch time. This is intentional — it lets adapters be
registered in any order relative to devices. A missing `app.adapter()` call
therefore raises `TypeError` on the first matching MQTT message, not at
startup.

Registration **does** fail immediately for:

- A parameter with no annotation
- A parameter with an unsupported kind (`*args`, `**kwargs`, positional-only)
- A generic annotation that is not a concrete type (see below)
- A `Depends()` callable that is async or unhashable
- An `Annotated` parameter with no recognized binding marker, or with more
  than one binding marker

## Annotation Constraints

### Generics are rejected

The DI system resolves by type identity, so the annotation must be a concrete
type — an actual `type` object. Generic forms like `list[str]`, `dict[str,
int]`, or `Optional[DeviceContext]` (from `typing`) are not types; they are
`GenericAlias` objects. Registering such an annotation raises `TypeError` at
registration time:

```python
# Wrong — Optional[X] is not a concrete type
async def handler(ctx: Optional[DeviceContext]) -> dict[str, object]: ...
# TypeError: has annotation ... which is not a concrete type.
# For an optional dependency use Annotated[DeviceContext | None, Optional()]

# Correct — use the framework Optional() marker
async def handler(ctx: Annotated[DeviceContext | None, Optional()] = None) -> dict[str, object]: ...
```

### `param.default` is not used for optional injection

The framework does not read `param.default` to decide whether injection is
optional. If a parameter's type has no registered provider, the framework
raises `TypeError` — regardless of what default value the parameter declares.
To inject a provider optionally (falling back to `None` or a custom default),
use the `Optional()` marker, which explicitly captures the default at
registration time via `_build_optional_plan_entry`.

### Async `Depends()` callables are rejected at construction

`Depends()` (in `cosalette.di`) checks whether the dependency callable is
async — either an `async def` function, an async generator, or a callable
object with an async `__call__` — and raises `TypeError` immediately at
marker construction time. This check runs when the handler's annotation is
first evaluated (at registration under PEP 563 `from __future__ import
annotations`). Async dependencies are not supported because DI is resolved
synchronously while building handler kwargs.

### `Depends()` dependency plans are cached; results are not

`_cached_dep_plan` wraps `build_injection_plan` with `functools.lru_cache`
so that signature inspection for a given dependency callable runs only once.
The dependency callable itself is **invoked on every resolution** — there is
no memoization of its return value. The cache covers the plan, not the result.

## Annotated Markers

When a parameter is annotated with `Annotated[T, marker]`, the framework
identifies the binding marker by scanning all metadata elements after the
base type (`args[1:]`) and requires **exactly one** recognized marker
(`Depends`, `Payload`, `Topic`, or `Optional`). Zero markers and multiple
markers both raise `TypeError` at registration.

### Marker types and asymmetry

| Syntax | Used in | What it binds |
|---|---|---|
| `Annotated[T, Payload()]` | Commands, triggered telemetry | Parsed MQTT payload (T via TypeAdapter) |
| `Annotated[str, Topic()]` | Commands, reactors | Raw MQTT topic string |
| `Annotated[T, Depends(fn)]` | Any handler | Return value of synchronous `fn` |
| `Annotated[T \| None, Optional()]` | Any handler | Provider T if registered, else default |
| `Message` (bare type) | Commands | Full `Message(topic, payload)` object |

`Payload()` and `Topic()` are factory functions that return marker objects.
`Message` is a dataclass annotated directly as a bare type — not a marker
inside `Annotated`. This asymmetry is intentional: `Message` bundles topic
and payload as a single value, while `Payload()` and `Topic()` bind them
separately with independent type coercion.

### `Topic()` validates its inner type

`Topic()` requires `Annotated[str, Topic()]` exactly — the inner type must be
`str`. A non-`str` inner type raises `TypeError` at registration:

```python
# Wrong
topic: Annotated[bytes, Topic()]  # TypeError: Topic() requires a str inner type

# Correct
topic: Annotated[str, Topic()]
```

### `Topic()` and `Message` require a request context

Both raise `TypeError` at dispatch time when used in a context that has no
MQTT topic (e.g. a scheduled telemetry cycle with no incoming message). See
[Error Taxonomy](../reference/errors.md#request-context-errors) for the exact
messages.

## Resolution Frequency

Providers are not rebuilt on every dispatch. The providers map is constructed
once per device lifetime and reused. What varies per archetype is **when
`resolve_request_kwargs` is called** relative to the message loop:

| Archetype | Resolved | When |
|---|---|---|
| `@app.command` | Per message | `prepare_command_kwargs` is called for every incoming MQTT message |
| `@app.react` | Per message | `resolve_request_kwargs` is called inside `dispatch_reactors` for each command |
| `@app.telemetry` | Once per lifetime | Before the `while not ctx.shutdown_requested` loop |
| `@app.device` (periodic) | Once per lifetime | Before the `while True` loop |
| `@app.stream` | Once per lifetime | `_build_handler_kwargs` is called once before the async generator is iterated |

For telemetry and periodic devices the resolved kwargs are **reused across
every publish cycle** — the handler is called with the same kwargs dict on
each iteration. This means injected objects are stable references: you get the
same `DeviceContext`, `Settings`, and adapter instances on every cycle.

See [Dependency Injection Reference](../reference/dependency-injection.md) for
the complete injectable-type table and per-archetype resolution frequency
detail.
