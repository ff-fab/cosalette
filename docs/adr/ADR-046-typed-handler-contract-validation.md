---
status: Accepted
date: 2026-05-09
impact: high
tags: [architecture, mqtt, di, serialization, testing]
---

# ADR-046: Typed Handler Contract Validation

## Status

Accepted **Date:** 2026-05-09

## Context

cos-ebc introduces typed handler contracts: decorators such as `@app.command` and `@app.telemetry` inspect handler signatures to extract payload and return-type annotations, validate inbound MQTT payloads at runtime, and normalise outbound values before publishing.

Python offers several strategies for runtime type validation ranging from Pydantic's rich ecosystem to stdlib-only approaches. The chosen backend must:

- Handle at minimum: Pydantic `BaseModel`, stdlib `dataclasses`, `TypedDict`, primitives (`str`, `int`, `float`, `bool`), and JSON-compatible mappings/sequences.
- Parse and validate JSON payloads arriving as raw `bytes` or `str`.
- Serialise Python objects back to JSON-compatible dicts for MQTT publish paths.
- Emit actionable diagnostics on validation failure so device operators can diagnose malformed messages.
- Integrate with the DI system (`typing.Annotated` + marker types) without forcing users to annotate plain parameters.

Pydantic `>=2.12.5` is already declared as a runtime dependency in `pyproject.toml`, so adopting its `TypeAdapter` API adds zero new third-party weight. The framework processes tens to hundreds of MQTT messages per second; validation must not become a bottleneck, so reusing pre-built `TypeAdapter` instances (cached per annotation) is essential.

## Decision

Use Pydantic v2 `TypeAdapter` as the runtime validation and serialisation backend for typed handler contracts because Pydantic is already a runtime dependency (`pydantic>=2.12.5`), supports all first-wave model kinds (BaseModel, stdlib dataclasses, TypedDict, primitives, and generic mappings/sequences) through a single uniform API, and provides rich error diagnostics with minimal overhead when adapters are cached.

```python
from __future__ import annotations

from typing import Annotated

from cosalette import App
from cosalette.mqtt import Message, Payload, Topic
from pydantic import BaseModel

app = App("demo")


class SetpointCmd(BaseModel):
    value: float
    unit: str = "celsius"


class ThermostatState(BaseModel):
    setpoint: float
    unit: str


@app.command("thermostat_setpoint")
async def handle_setpoint(
    cmd: Annotated[SetpointCmd, Payload()],
    full_topic: Annotated[str, Topic()],
    message: Message,
) -> ThermostatState:          # non-None return → auto-publish via TypeAdapter
    return ThermostatState(
        setpoint=cmd.value,
        unit=cmd.unit,
    )


@app.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, float]:   # plain annotation; TypeAdapter handles dict
    return {"celsius": 21.5}
```

## Decision Drivers

- Pydantic v2 is already a runtime dependency; zero new third-party weight required
- TypeAdapter provides a uniform API across BaseModel, stdlib dataclasses, TypedDict, primitives, and generic containers
- Rich ValidationError diagnostics with field-level paths allow device operators to diagnose malformed MQTT payloads
- TypeAdapter instances are inexpensive to cache per annotation, keeping per-message overhead sub-millisecond
- PEP 593 Annotated preserves plain type annotations for handlers that do not use DI markers, maintaining backward compatibility

## Considered Options

### Option 1: Pydantic TypeAdapter (chosen)

Use `pydantic.TypeAdapter` (v2) as the single validation and serialisation engine. For each unique type annotation encountered during handler registration, the framework builds and caches a `TypeAdapter` instance. Inbound MQTT payloads are first decoded from JSON bytes, then validated with `adapter.validate_python()`. Outbound return values are serialised with `adapter.dump_python(mode='json')`. Annotated DI markers (`Payload`, `Topic`, `Depends`) are stripped before the remaining type hint is passed to TypeAdapter, so plain annotations remain unaffected.

- *Advantages:* Already a declared runtime dependency — no additional install required; Single API covers BaseModel, stdlib dataclasses, TypedDict, primitives, and generic collections; Detailed ValidationError with field paths suitable for structured logging; Pydantic v2 core is implemented in Rust; validation throughput is high; Supports both `validate_python` (Python objects) and `validate_json` (raw bytes) overloads; Schema generation via `TypeAdapter.json_schema()` aligns with the future cos-bnq contract layer
- *Disadvantages:* Ties framework validation semantics to Pydantic internals; a future Pydantic v3 migration could require adapter-layer changes; attrs models are not explicitly supported in the first wave; attrs users must provide a compatible dataclass or BaseModel; TypeAdapter caching requires a thread-safe registry; adds a small amount of framework machinery

### Option 2: JSON Schema only

Validate inbound payloads against JSON Schema documents (stored or generated per handler) using `jsonschema` or `fastjsonschema`. Types would be extracted by inspecting annotations and converting them to JSON Schema manually. Return values would be serialised through a custom recursive dict converter with no schema enforcement.

- *Advantages:* Validation is entirely schema-driven and language-agnostic; schemas can be published externally; No dependency on a specific Python type system; works with any dict-compatible input
- *Disadvantages:* Adds `jsonschema` or `fastjsonschema` as new runtime dependencies; Requires a bespoke annotation-to-schema converter covering the full type grammar; No automatic deserialisation into typed Python objects; application code receives raw dicts; Error messages from JSON Schema validators are less actionable for Python developers; Ongoing maintenance burden as Python type annotations evolve (PEP 695, PEP 696, etc.)

### Option 3: Stdlib ad hoc conversion

Implement a lightweight, stdlib-only validation layer using `dataclasses.fields`, `typing.get_type_hints`, and manual isinstance checks. JSON decoding via `json.loads`. Each model kind (dataclass, TypedDict, BaseModel) requires a dedicated code path. Return serialisation uses `dataclasses.asdict` or custom `__dict__` traversal.

- *Advantages:* Zero additional runtime dependencies; Full control over validation behaviour; no hidden Pydantic version constraints
- *Disadvantages:* Significant implementation and maintenance surface: each model kind needs its own code path; Validation errors are low-quality without significant additional investment; No built-in support for nested models, generic aliases, or discriminated unions; Effectively reimplements a subset of Pydantic without benefit of its test suite or community; Schema generation for cos-bnq would require a separate, parallel implementation effort

## Decision Matrix

| Criterion | Pydantic TypeAdapter | JSON Schema only | Stdlib ad hoc conversion |
| --- | --- | --- | --- |
| Zero new runtime dependencies | 5 | 2 | 5 |
| Coverage of first-wave model kinds | 5 | 3 | 2 |
| Actionable validation diagnostics | 5 | 3 | 1 |
| Runtime validation throughput | 5 | 4 | 3 |
| Schema generation readiness for cos-bnq | 5 | 4 | 1 |
| Implementation and maintenance burden | 4 | 2 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Handlers can declare rich typed parameters and return values using idiomatic Python type annotations; the framework handles all parsing and serialisation transparently.
- Validation failures produce structured `ValidationError` objects with field-level paths, enabling precise error publishing and structured logging.
- TypeAdapter caching means per-message validation overhead is dominated by JSON decode time, not schema compilation.
- Payload annotations / `Payload` markers and the return annotation / `state_model` drive runtime validation and serialisation; `payload_model` is introspection metadata for documentation and tooling only. These same contract sources will drive schema generation in cos-bnq with no additional framework changes.
- Backward compatibility is preserved: handlers using positional `topic`/`payload` `str` parameters continue to work as compatibility shims; typed binding is additive.

### Negative

- attrs model users must wrap their models in a Pydantic BaseModel or stdlib dataclass; attrs is not explicitly supported in the first wave.
- A Pydantic v3 major release may require an adapter-layer migration; the TypeAdapter caching registry is the main coupling point.
- Non-JSON payloads (e.g. plain text, binary protocols) must use explicit `Message` or raw `str`/`bytes` bindings; typed payload binding always assumes JSON and will fail with a clear diagnostic otherwise.

_2026-05-09_
