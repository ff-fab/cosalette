# ADR-009: Python Version and Dependencies

## Status

Accepted **Date:** 2026-02-14

## Context

cosalette is a greenfield project — there is no legacy code to constrain the Python
version. All deployment targets are Raspberry Pi 4 or later (including Pi Zero 2),
running Raspberry Pi OS, which can be configured with current Python versions. The
framework is async-first (MQTT client, device lifecycles, signal handling), making
modern `asyncio` features and type annotation improvements valuable.

The framework needs a small, focused dependency set for MQTT communication,
configuration management, and CLI — without pulling in large transitive dependency
trees that would bloat Docker images for resource-constrained Raspberry Pi deployments.

## Decision

Use **Python ≥ 3.14** with the following core dependencies: **aiomqtt**, **pydantic**,
**pydantic-settings**, and **typer**, because this is a greenfield project that can
leverage the latest language features and the chosen libraries align with the framework's
async-first, type-driven design philosophy.

### Core dependencies

```toml
[project]
requires-python = ">=3.14"
dependencies = [
    "aiomqtt>=2.5.0",
    "pydantic>=2.12.5",
    "pydantic-settings>=2.12.0",
    "typer>=0.12",
]

[project.optional-dependencies]
testing = [
    "pytest>=9.0",
    "pytest-asyncio>=1.3",
]
```

### Rationale per dependency

| Dependency        | Role                    | Why this over alternatives                      |
| ----------------- | ----------------------- | ----------------------------------------------- |
| aiomqtt           | Async MQTT client       | Native asyncio, clean API, wraps paho-mqtt      |
| pydantic          | Data validation         | Type-driven validation, used by settings        |
| pydantic-settings | Configuration loading   | Env vars, `.env` files, nested models (ADR-003) |
| typer             | CLI scaffolding         | Type-hint-driven argument parsing (ADR-005)     |

## Decision Drivers

- Greenfield project — no legacy compatibility constraints
- Async-first architecture requires modern `asyncio` features
- Type-driven philosophy benefits from latest type annotation syntax
- Resource-constrained Raspberry Pi deployments need minimal dependencies
- aiomqtt provides native asyncio MQTT without callback-style programming

## Considered Options

### Option 1: Support Python 3.12+

Support Python 3.12 and later for broader compatibility.

- *Advantages:* Wider compatibility with existing OS packages. More users can adopt
  without upgrading Python.
- *Disadvantages:* Misses Python 3.13 and 3.14 improvements (pattern matching
  refinements, improved error messages, type parameter syntax). The target audience is
  a single developer with full control over deployment targets. Constraining to older
  Python adds no value.

### Option 2: Use paho-mqtt directly

Use the paho-mqtt library directly instead of aiomqtt.

- *Advantages:* Fewer dependencies (paho-mqtt is aiomqtt's underlying library).
  More control over the MQTT client implementation.
- *Disadvantages:* paho-mqtt uses a callback-style API that does not integrate cleanly
  with asyncio. Wrapping paho-mqtt in async is exactly what aiomqtt does — reimplementing
  this wrapper would duplicate aiomqtt's work. The velux2mqtt reference implementation
  already wraps paho-mqtt manually (356 lines) — the framework should use aiomqtt to
  avoid this.

### Option 3: Python ≥ 3.14 with aiomqtt, pydantic, pydantic-settings, typer (chosen)

Require the latest stable Python and use a focused set of async-first, type-driven
libraries.

- *Advantages:* Latest language features (type parameter syntax, improved asyncio,
  better error messages). aiomqtt provides clean async MQTT without callback wrappers.
  Focused dependency set keeps Docker images lean. All dependencies are mature, well-
  maintained libraries.
- *Disadvantages:* Python 3.14 may not be pre-packaged in all OS repositories —
  requires manual installation or Docker base images with 3.14.

## Consequences

### Positive

- Full access to modern Python features — type parameter syntax (PEP 695), improved
  `asyncio`, better error messages
- aiomqtt eliminates the need for a custom paho-mqtt wrapper (~356 lines in velux2mqtt)
- Focused dependency set keeps installation fast and Docker images small
- All dependencies are well-maintained with active communities

### Negative

- Python 3.14 may require building from source or using specific Docker base images
  on Raspberry Pi OS
- Narrow Python version support means the framework cannot be adopted by projects
  stuck on older Python versions (acceptable for a personal project ecosystem)
- aiomqtt adds a transitive dependency on paho-mqtt — though this is the standard
  Python MQTT library

_2026-02-14_
