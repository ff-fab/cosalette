# ADR-001: Framework Architecture Style

## Status

Accepted **Date:** 2026-02-14

## Context

The cosalette project needs to provide common infrastructure for 8+ IoT-to-MQTT bridge
applications. Each project currently implements its own MQTT lifecycle, logging, error
reporting, signal handling, and CLI — resulting in approximately 1,000+ lines of
duplicated infrastructure code per project. A decision is needed on how to package and
deliver this shared functionality: as a passive library, a plugin system, a
configuration-driven engine, or an opinionated framework.

Analysis of the velux2mqtt reference implementation shows the infrastructure-to-domain
ratio is heavily skewed: `main.py` alone is 238 lines of lifecycle wiring, the custom
`MqttClientAdapter` is 356 lines, `ErrorPublisher` is 251 lines, and `JsonFormatter` is
105 lines — all of which are generic, non-domain code that repeats across projects.

## Decision

Use **Inversion of Control with decorator-based registration** (FastAPI-inspired
framework) because it eliminates the maximum amount of boilerplate while preserving
domain purity through the hexagonal architecture boundary.

The central `cosalette.App` object owns the event loop, signal handlers, MQTT lifecycle,
and logging. Project authors register their devices and adapters via decorators
(`@app.device`, `@app.telemetry`) and method calls (`app.adapter()`), and the framework
calls their code — not the other way around.

```python
import cosalette

app = cosalette.App(name="velux2mqtt", version="0.1.0")

@app.device("blind")
async def blind(ctx: cosalette.DeviceContext) -> None:
    ...
```

## Decision Drivers

- Eliminate ~1,000 lines of duplicated infrastructure per project
- Consistent behaviour across all 8+ IoT projects (topic layout, logging, error
  reporting, shutdown flow)
- FastAPI-like developer experience — decorators, context injection, opinionated defaults
- Domain layer must remain pure (no framework imports)
- Each project produces an independent, standalone console application
- Each project deploys as its own process — no shared runtime or inter-process coupling

## Considered Options

### Option 1: Library approach

Projects import and call framework functions explicitly. The project owns `main()` and
wires everything together.

- *Advantages:* Maximum flexibility, no inversion of control to learn, familiar to all
  Python developers.
- *Disadvantages:* Does not eliminate the composition root boilerplate (~238 lines in
  velux2mqtt's `main.py`). Each project must wire MQTT lifecycle, signal handling, and
  logging setup manually. Consistency depends on discipline, not enforcement.

### Option 2: Plugin architecture

A generic host application discovers and loads project code via entry points or plugin
protocols.

- *Advantages:* Strong separation, standardised extension points.
- *Disadvantages:* Over-engineered for a single-developer ecosystem of 8 projects.
  Plugin discovery adds complexity. Debugging through plugin loading layers is painful.
  Does not match the "standalone app" deployment requirement.

### Option 3: Configuration-driven engine

Projects are defined entirely via YAML/TOML configuration. The engine reads config and
executes pre-built device templates.

- *Advantages:* Zero code for simple cases, very consistent.
- *Disadvantages:* Cannot express arbitrary device logic (e.g., velux2mqtt's homing
  sequence, position estimation). Falls apart for complex bidirectional devices. Would
  require a DSL for non-trivial cases, which is worse than Python.

### Option 4: Framework with Inversion of Control (chosen)

The framework owns the lifecycle. Projects register devices via decorators and implement
domain logic in device functions that receive a `DeviceContext`.

- *Advantages:* Maximum boilerplate elimination (main.py → 2 lines). Consistent
  behaviour enforced by the framework. FastAPI-like DX with decorators and context
  injection. Escape hatches via lifecycle hooks (now the lifespan context manager —
  see Addendum) and raw MQTT access.
- *Disadvantages:* Framework lock-in — migrating away requires reimplementing
  infrastructure. Debugging through framework layers adds indirection. Maintenance
  burden of a real package with CI, versioning, and releases.

## Decision Matrix

| Criterion                  | Library | Plugin | Config-Driven | Framework (IoC) |
| -------------------------- | ------- | ------ | ------------- | --------------- |
| Boilerplate elimination    | 2       | 3      | 5             | 5               |
| Consistency enforcement    | 2       | 4      | 5             | 5               |
| Flexibility for complex    | 5       | 3      | 1             | 4               |
| Developer experience       | 3       | 2      | 3             | 5               |
| Maintenance burden         | 5       | 2      | 3             | 3               |
| Domain purity preservation | 3       | 4      | 2             | 5               |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Each project's `main.py` reduces to ~2 lines (`from myapp.app import app; app.run()`)
- MQTT lifecycle, signal handling, logging, error publication, CLI, and health reporting
  are provided once and shared across all projects
- New projects can be scaffolded rapidly with minimal infrastructure code
- Decorator-based API provides clear, readable device declarations
- The framework acts as the composition root, eliminating manual wiring

### Negative

- Projects are coupled to cosalette's conventions — migrating away means reimplementing
  all infrastructure
- Stack traces cross cosalette internals, adding debugging indirection
- cosalette is a real package with its own release cycle, CI, and maintenance burden
- New contributors must learn cosalette's conventions before contributing to any project

_2026-02-14_

## Addendum — Lifespan Pattern (2026-02-19)

The `@app.on_startup` and `@app.on_shutdown` decorator-based lifecycle hooks
mentioned in the original decision have been replaced by a single **lifespan
context manager** passed to the `App` constructor:

```python
App(name="myapp", version="1.0.0", lifespan=my_lifespan)
```

This follows the same pattern established by Starlette/FastAPI's lifespan API.
The context manager's startup code (before `yield`) runs before devices start;
shutdown code (after `yield`) runs after devices stop. This change improves
resource safety (paired init/cleanup in one function) and reduces API surface.

Additionally, **signature-based handler injection** was introduced: device handlers
now declare only the parameters they need via type annotations. Zero-parameter
handlers are valid. `ctx: DeviceContext` remains supported but is no longer required.

See the [Lifespan guide](../guides/lifespan.md) and
[Lifecycle concept](../concepts/lifecycle.md) for details.

_2026-02-19_
