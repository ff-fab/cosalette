# cosalette Framework Evaluation

**Date:** 2026-03-07
**Scope:** Full framework review.
**Version evaluated:** 0.1.8

---

## Executive Summary

cosalette has evolved from a single refactoring impulse into a coherent, well-documented
framework with strong architectural foundations. The "FastAPI for MQTT" vision is clearly
realized for simple cases — the debug app is clean, declarative, and immediately
readable. The hexagonal architecture, protocol-based ports, publish strategies, and
testing toolkit are genuine strengths that set this framework apart from ad-hoc IoT
scripts.

However, three real-world adopter projects have surfaced tension between the framework's
declarative ideal and the pragmatic needs of complex IoT hardware. This evaluation
assesses each concern from `framework-review.md` and proposes concrete action points.

---

## 1. Declarativeness of Main Entry Points

### Assessment: Partially Achieved — Focus Area

**The ideal** (realized in the debug app):

```python
app = cosalette.App(name="debugapp", version="0.1.0-debug", lifespan=lifespan)

@app.telemetry("sensor", interval=3.0)
async def read_sensor() -> dict[str, object]:
    return {"temperature": round(temp, 1)}

@app.command("valve")
async def handle_valve(payload: str, ctx: DeviceContext) -> dict[str, object]:
    return {"valve_state": payload}
```

**What breaks the ideal in real projects:**

1. **Dynamic registration from config** — Projects with many similar devices (vito2mqtt:
   7+ telemetry groups) loop over config and call `app.add_telemetry()` imperatively.
   The main.py becomes a wiring module rather than a readable contract.

2. **Lambda intervals** — `interval=lambda s: s.poll_interval` (ADR-020) solves
   `--help` crashes but reads poorly vs. `interval=60`. Five lambdas in a row obscure
   intent.

3. **Factory/registration functions** — Complex init patterns get extracted into helper
   functions, which is clean code but hides the declarative contract from the main
   module.

4. **Adapter wiring boilerplate** — `app.adapter(Port, "module:Class", dry_run=Fake)`
   plus lifespan connect/close adds several lines per adapter before any device
   registration.

### Proposals

#### P1.1 — Configuration-Driven Bulk Registration (Medium effort)

**What:** Introduce a `@app.telemetry_group` or `app.register_from_config()` pattern
that maps a config structure to multiple telemetry/command registrations declaratively.

**Sketch:**

```python
# Instead of:
for group in settings.groups:
    app.add_telemetry(group.name, make_handler(group), interval=group.interval)

# Offer:
@app.telemetry_group(config_key="groups", interval=lambda g: g.interval)
async def read_group(group_cfg: GroupConfig, ctx: DeviceContext) -> dict[str, object]:
    return ctx.adapter(OptolinkPort).read(group_cfg.address)
```

**Advantages:**

- Single decorator expresses "N devices from config"
- The handler function is the contract — config determines multiplicity
- `main.py` stays readable at a glance

**Disadvantages:**

- Adds framework surface area for a pattern that may vary across projects
- Config-driven registration is harder to type-check statically

**Recommendation:** Start with a lighter approach — a
**`register_telemetry_batch()`** convenience function in a `cosalette.contrib` or
`cosalette.helpers` namespace. Validate it against 2+ real projects before promoting to
a decorator.

#### P1.2 — Named Interval Presets (Low effort)

**What:** Allow intervals to reference settings attribute names as strings instead of
lambdas:

```python
# Instead of:
@app.telemetry("temp", interval=lambda s: s.poll_interval)

# Allow:
@app.telemetry("temp", interval="poll_interval")
```

The framework resolves `getattr(settings, "poll_interval")` after settings are loaded —
same deferred semantics, cleaner syntax.

**Advantages:**

- Dramatically improves readability for the common case
- Backward compatible (floats and callables still work)
- Simple to implement (string → `lambda s: getattr(s, name)` at registration)

**Disadvantages:**

- Only supports flat attribute access (not `s.hardware.interval`)
- Loses IDE autocompletion on the settings class

**Recommendation:** Implement. This is a quick win with high readability impact. Support
dotted paths (`"hardware.poll_interval"`) for nested settings.

#### P1.3 — "Contract Summary" Auto-Generation (Low effort)

**What:** Add an `app.summary()` method or CLI flag (`--show-devices`) that prints a
table of all registered devices, their types, intervals, strategies, and adapters.

```
$ myapp --show-devices
Device        Type        Interval  Strategy           Group
temperature   telemetry   1.0s      Every(300)|OnChange  vito
hot_water     telemetry   1.0s      OnChange           vito
valve         command     —         —                  —
```

**Why:** Even when `main.py` is procedural, the contract is still accessible. AI agents
can use this as a quick orientation tool.

**Recommendation:** Implement. Low effort, high discoverability value.

---

## 2. The `@app.device` Overuse Concern

### Assessment: Largely Resolved — Validate with Adopters

The concern was historically valid: before ADR-013 (publish strategies), ADR-018
(coalescing groups), ADR-019 (scoped name uniqueness), and the `@app.command` addition,
many use cases were forced into `@app.device`. The framework has since addressed the
root causes:

| Previously required `@app.device` | Now possible with |
|---|---|
| Fast poll, slow publish | `@app.telemetry(interval=1, publish=Every(300))` |
| Publish only on change | `@app.telemetry(publish=OnChange(threshold=0.5))` |
| Bidirectional (poll + command) | `@app.telemetry("x") + @app.command("x")` (ADR-019) |
| Shared hardware resource | `group="optolink"` (ADR-018) |
| Config-driven interval | `interval=lambda s: s.interval` (ADR-020) |

**Remaining legitimate `@app.device` cases:**

- Event-driven / trigger-based logic (Schmitt triggers, hysteresis state machines)
- Complex stateful loops with fine-grained sleep/publish control
- Hardware needing error recovery within the loop

### Proposals

#### P2.1 — Migration Audit of Early Adopters (Low effort)

**What:** Revisit gas2mqtt, vito2mqtt, and jeelink2mqtt. For each `@app.device`
registration, evaluate whether it can now be refactored to `@app.telemetry` +
`@app.command` with the new strategies and coalescing groups.

**Expected outcome:** Concrete data on whether the remaining `@app.device` uses are
truly irreducible, or if the new features haven't been backported to the pioneer
projects.

**Recommendation:** High priority. This answers the question definitively.

#### P2.2 — Event-Driven Telemetry Extension (Medium effort)

**What:** If the migration audit reveals a pattern of event-driven devices that don't
fit `@app.telemetry` but don't need full `@app.device` complexity, consider a fourth
archetype or a callback-based variant:

```python
@app.telemetry("gas_counter", interval=1.0, publish=OnEvent())
async def counter(ctx: DeviceContext) -> dict[str, object] | None:
    # Return None → don't publish, framework continues loop
    # Return dict → publish
    impulses = read_sensor()
    if impulses != ctx.previous.get("impulses"):
        return {"impulses": impulses}
    return None
```

**Why:** This would eliminate the last common reason to reach for `@app.device` — the
handler returning `None` signals "skip this cycle" without needing a manual loop.

**Assessment:** Evaluate after P2.1 tells us if this pattern appears frequently enough
to warrant framework support. Do not over-abstract prematurely.

---

## 3. AI-Agent First-Class Citizen Optimization

### Assessment: Already Strong — Opportunities for Enhancement

cosalette's AI-agent friendliness is notably above average:

- **`cosalette-framework-reference.instruction.md`** — single-file API cheat sheet
  designed as a Copilot instruction. Excellent.
- **`cosalette-migration-prompt.md`** — structured agent workflow for legacy app
  migration with phased approach and improvement collection.
- **20 ADRs** — AI agents parse these well; evidence, alternatives, and decision
  matrices give agents the "why" behind design choices.
- **Protocol-based architecture** — agents understand PEP 544 protocols; the
  structural subtyping pattern maps well to how LLMs reason about code.

### Proposals

#### P3.1 — Machine-Readable Device Registry (Low effort)

**What:** Expose registered devices as a structured JSON/YAML schema (either via CLI
`--dump-schema` or a `cosalette.introspect` module).

```json
{
  "devices": [
    {"name": "temperature", "type": "telemetry", "interval": 1.0,
     "strategy": "Every(300) | OnChange(0.5)", "group": "vito"},
    {"name": "valve", "type": "command", "init": true}
  ],
  "adapters": [
    {"port": "OptolinkPort", "impl": "vito2mqtt.adapters:OptolinkAdapter"}
  ]
}
```

**Why:** Agents can ingest this programmatically instead of pattern-matching source code.
Pairs well with P1.3 (human-readable contract summary).

#### P3.2 — Error Taxonomy Reference (Low effort)

**What:** Document common framework errors (registration-time TypeErrors, missing
annotations, adapter resolution failures) with causes and fixes. Format as a reference
page agents can consult.

**Why:** Fail-fast validation errors are excellent for developers — they're even better
for AI agents if there's a lookup table mapping error messages to resolutions.

#### P3.3 — Worked Migration Examples (Medium effort)

**What:** Add 2–3 complete migration case studies as documentation:
- Before (legacy script) → After (cosalette app)
- Decision log: why telemetry vs. device, which strategy, which adapter pattern

**Why:** AI agents learn best from examples. The migration prompt defines the *process*;
worked examples provide the *patterns*. These become few-shot exemplars for future
migrations.

#### P3.4 — Instruction File Per Project Pattern (Low effort)

**What:** Create a `.github/instructions/cosalette.instructions.md` template that
adopter projects can include, containing project-specific device registrations, adapter
list, and settings schema.

**Why:** Agents working on adopter projects (not the framework) benefit from
project-specific context alongside the framework reference.

---

## 4. Rust Integration for Performance

### Assessment: Feasible and Well-Scoped — Strategic Opportunity

The interest in Rust is well-founded both practically and as a learning exercise.
cosalette's architecture makes Rust integration clean because the protocol-based
boundaries provide natural FFI seams.

### Performance-Sensitive Hot Paths

| Component | Current | Bottleneck Type | Rust Benefit |
|---|---|---|---|
| Signal filters (`Pt1Filter`, `MedianFilter`, `OneEuroFilter`) | Pure Python math | CPU-bound per-sample | **High** — tight numeric loops are pyo3's sweet spot |
| JSON serialization (`json.dumps` per publish) | stdlib `json` | CPU per-publish | **Medium** — `orjson` (Rust-based) already exists as drop-in |
| Coalescing group scheduler | `heapq` + int arithmetic | Negligible | **Low** — already efficient |
| MQTT publish/reconnect | `aiomqtt` (async I/O) | I/O-bound | **None** — Rust doesn't help async I/O wrappers |

### Proposals

#### P4.1 — Rust Signal Filter Library via pyo3 (High effort, high learning value)

**What:** Create a `cosalette-filters-rs` Rust crate exposing `Pt1Filter`,
`MedianFilter`, and `OneEuroFilter` as Python classes via pyo3/maturin.

**Architecture:**

```
cosalette-filters-rs/          # Rust crate
├── src/lib.rs                 # pyo3 module
├── src/pt1.rs                 # PT1 filter
├── src/median.rs              # Median filter
├── src/one_euro.rs            # 1€ filter
├── Cargo.toml
└── pyproject.toml             # maturin build

cosalette/                     # Python package
├── _filters.py                # Pure Python (always available)
└── filters.py                 # Auto-selects: try Rust, fall back to Python
```

**Key design decisions:**

- **Optional dependency** — the Rust package is an extra (`pip install
  cosalette[fast-filters]`). Pure Python is the default, Rust is the accelerator.
- **Same API** — Rust classes implement the same `Filter` protocol. User code doesn't
  change.
- **Fallback import** pattern:

  ```python
  try:
      from cosalette_filters_rs import Pt1Filter, MedianFilter, OneEuroFilter
  except ImportError:
      from cosalette._filters import Pt1Filter, MedianFilter, OneEuroFilter
  ```

**Advantages:**

- Excellent Rust learning project — small, self-contained, testable
- 10–50× speedup realistic for high-frequency sampling (1 kHz+)
- Clean separation; framework logic stays in Python
- Protocol-based architecture means zero changes to the framework core
- Cross-platform wheels via maturin + GitHub Actions (manylinux, musllinux, macOS, Windows)
-  Targets the exact hardware the user mentioned (RPi Zero: ARMv6/v7 — Rust cross-compiles well)

**Disadvantages:**

- Build complexity (Rust toolchain, maturin, CI matrix for ARM wheels)
- Maintenance of two implementations (Python + Rust) that must stay in sync
- For most cosalette apps (1–60 Hz polling), Python filters are fast enough

**Recommendation:** Pursue this as a dedicated learning project. Start with `Pt1Filter`
alone — it's the simplest (~20 lines of Rust), validates the full build/test/publish
pipeline, and provides a template for the others.

#### P4.2 — Adopt `orjson` for JSON Serialization (Low effort)

**What:** Replace `json.dumps()` in MQTT publish paths with `orjson.dumps()`.

**Why:** `orjson` is Rust-based, ~5–10× faster than stdlib `json`, and is a drop-in
replacement. No custom Rust code needed.

**Recommendation:** Add as optional dependency (`cosalette[fast]`). Conditional import
in `_mqtt.py`.

#### P4.3 — Benchmark Suite (Medium effort)

**What:** Before any Rust work, establish a benchmark suite for the hot paths (filter
throughput, publish serialization, scheduler tick) using `pytest-benchmark` or `asv`.

**Why:** Prevents premature optimization. Provides before/after evidence. Identifies
whether Python is actually the bottleneck on target hardware.

**Recommendation:** Implement before P4.1. Benchmarks on RPi Zero hardware are essential
— the bottleneck profile differs significantly from developer machines.

---

## 5. Architectural Health & Technical Debt

### Observations

The codebase is well-decomposed with consistent patterns. 20 ADRs for a 0.1.x project
is exceptional discipline. However, some organic growth is visible:

#### 5.1 `_mqtt.py` Module Size (450+ lines)

Contains protocols, value objects, 3 implementations (Null, Mock, real MqttClient), and
the full reconnect loop. The real `MqttClient` with `_connection_loop()` dominates.

**Proposal:** Extract `MqttClient` into `_mqtt_client.py`. Keep protocols and value
objects in `_mqtt.py`. Keep `MockMqttClient` and `NullMqttClient` where they are or move
to a `_mqtt_testing.py`.

#### 5.2 Delegate Methods on `App`

Several `App` methods simply delegate to `_wiring` functions. These were kept during the
extraction from the original "god class" pattern.

**Proposal:** Since there is no backward compatibility requirement (v0.1.x, no
production deployments), remove the delegate methods and call `_wiring` functions
directly. Reduces cognitive load when reading `_app.py`.

#### 5.3 `_AdapterEntry` Colocation

Adapter registration types are in `_registration.py` alongside device registration
types. These are different concerns.

**Proposal:** Extract adapter registration into `_adapter_lifecycle.py` which already
owns adapter resolution. Or create `_adapter_registration.py` if the file gets too large.
Low priority — current layout works fine.

#### 5.4 `cast(float, reg.interval)` After Resolution

Deferred interval resolution (ADR-020) leaves `_TelemetryRegistration.interval` typed as
`float | Callable`. After `resolve_intervals()` runs, downstream code uses
`cast(float, ...)` which loses type safety.

**Proposal:** Introduce a `_ResolvedTelemetryRegistration` frozen dataclass (or simply a
`resolved_interval: float` field set post-resolution) to make the type system track
resolution state.

#### 5.5 `_call_factory` / `_call_init` Duplication

Both do signature-based DI invocation with slightly different semantics.

**Proposal:** Unify into a single `_invoke_with_injection(func, providers)` function in
`_injection.py`. Low priority — the duplication is small.

---

## 6. Testing & Quality

### Assessment: Excellent

- 28 unit test files, integration tests, shared fixtures
- `AppHarness` + `MockMqttClient` + `FakeClock` + pytest plugin
- Dry-run adapter pattern built into the framework
- 80% coverage threshold enforced

### Proposals

#### P6.1 — Property-Based Tests for Strategies and Filters (Medium effort)

**What:** Add Hypothesis property-based tests for publish strategies and signal filters.

**Why:** Strategies and filters have mathematical invariants (monotonicity, convergence,
idempotency) that are hard to cover exhaustively with example-based tests but natural
for property tests.

**Example properties:**

- `Every(seconds=N)` publishes at most once per N seconds for any input sequence
- `Pt1Filter` output converges to constant input value
- `MedianFilter` bounded by min/max of window
- `OnChange(threshold=T)` never publishes when `|current - previous| < T`

#### P6.2 — Integration Tests Against Real MQTT Broker (Low effort)

**What:** Add a docker-compose with Mosquitto for integration tests that verify actual
MQTT publish/subscribe/LWT behavior.

**Why:** `MockMqttClient` tests the framework logic but not the actual `aiomqtt`
integration. Edge cases in QoS, reconnection, and LWT only surface with a real broker.

---

## 7. Documentation Refinements

### Assessment: Very Good — 20 ADRs, comprehensive guides

### Proposals

#### P7.1 — Architecture Overview Diagram (Low effort)

**What:** Add a Mermaid diagram to `docs/concepts/architecture.md` showing the hex arch
layers: User code → App (composition root) → Ports → Adapters → Infrastructure.

**Why:** Visual entry point for both humans and agents scanning docs.

#### P7.2 — Decision Tree: Which Device Type? (Low effort)

**What:** Add a flowchart or decision table to the device archetypes documentation:

```
Q: Does your device poll periodically?
├─ Yes → Q: Does it also accept commands?
│  ├─ Yes → @app.telemetry("x") + @app.command("x")
│  └─ No  → @app.telemetry("x")
├─ No  → Q: Does it only respond to commands?
│  ├─ Yes → @app.command("x")
│  └─ No  → @app.device("x")   ← complex lifecycle
```

**Why:** Reduces the "which archetype?" decision fatigue for new projects and AI agents.

---

## Summary: Prioritized Action Points

### Quick Wins (Low effort, high impact)

| # | Action | Section |
|---|--------|---------|
| P1.2 | Named interval presets (string → attribute lookup) | §1 |
| P1.3 | `--show-devices` contract summary CLI flag | §1 |
| P2.1 | Migration audit of gas2mqtt, vito2mqtt, jeelink2mqtt | §2 |
| P7.2 | Decision tree: which device type? | §7 |

### Medium-Term Improvements

| # | Action | Section |
|---|--------|---------|
| P3.2 | Error taxonomy reference | §3 |
| P3.3 | Worked migration examples (2–3 case studies) | §3 |
| P3.4 | Instruction file template for adopter projects | §3 |
| P4.2 | Adopt `orjson` for JSON serialization | §4 |
| P5.1 | Extract `MqttClient` from 450-line `_mqtt.py` | §5 |
| P5.4 | Resolved interval type safety | §5 |
| P7.1 | Architecture overview diagram | §7 |

### Strategic Investments

| # | Action | Section |
|---|--------|---------|
| P1.1 | Configuration-driven bulk registration pattern | §1 |
| P2.2 | Event-driven telemetry extension (evaluate after P2.1) | §2 |
| P3.1 | Machine-readable device registry | §3 |
| P4.1 | Rust signal filter library via pyo3 | §4 |
| P4.3 | Benchmark suite (prerequisite for P4.1) | §4 |
| P6.1 | Property-based tests (Hypothesis) | §6 |

---

## Appendix: Framework Maturity Scorecard

| Dimension | Score | Notes |
|---|---|---|
| Architecture | ★★★★★ | Hex arch consistently applied, protocols everywhere, excellent ADR discipline |
| API Declarativeness | ★★★★☆ | Clean for simple cases, degrades with dynamic registration |
| Testing | ★★★★★ | AppHarness, MockMqtt, FakeClock, pytest plugin, dry-run — outstanding |
| Documentation | ★★★★☆ | 20 ADRs, good guides, AI instruction files. Missing: decision trees, diagrams |
| AI-Agent Friendliness | ★★★★☆ | Framework reference + migration prompt excellent. Could add introspection APIs |
| Performance | ★★★☆☆ | Adequate for current apps; Python-only limits high-frequency paths. No benchmarks yet |
| Code Quality | ★★★★☆ | Well-decomposed, typed, slotted dataclasses. Some organic growth visible |
| Ecosystem Readiness | ★★★☆☆ | v0.1.x, 3 adopters, no production deployment, no backward compat burden |
