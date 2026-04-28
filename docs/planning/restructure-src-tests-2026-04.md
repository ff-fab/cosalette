# Restructuring Plan: `src` and `tests` — April 2026

## Context

Analysis of `/workspace/packages/src/cosalette` (45 root-level `.py` files) and
`/workspace/packages/tests/unit` (73 flat `.py` files) against Python community norms.

### Community Thresholds

| Metric | Soft limit | Hard limit | Source |
|--------|-----------|-----------|--------|
| File lines (module) | 300 | 500 | community consensus, many linters |
| File lines (data/content) | 500 | 1000 | pragmatic — harder to enforce |
| Files per flat directory | 10–15 | 20 | readability / import ergonomics |
| Methods per class | 10 | 20 | SRP / God-class smell |
| `__init__.py` lines | 50 | 100 | should re-export, not define logic |

---

## Current Violations

### Source — `/packages/src/cosalette/`

```
45 files at root level  ← exceeds 20-file flat-dir threshold
```

| File | Lines | Severity | Issue |
|------|-------|----------|-------|
| `_app.py` | **2,475** | 🔴 Critical | God class — 20+ methods, all concerns |
| `_wiring.py` | **1,594** | 🔴 Critical | Dependency graph + resolution + validation |
| `_ai_content.py` | **1,130** | 🟠 High | Entire help/guidance corpus in one file |
| `_telemetry_runner.py` | **894** | 🟠 High | Runner + retry logic + strategy dispatch |
| `_schema/_cli.py` | **675** | 🟡 Moderate | CLI commands + rendering + validation mixed |
| `_context.py` | **702** | 🟡 Moderate | Too many context types in one place |
| `_cron.py` | **632** | 🟡 Moderate | Scheduler + expression parsing + validation |
| `_health.py` | **555** | 🟡 Moderate | Provider types + aggregation + MQTT publish |
| `_schema/_loader.py` | **555** | 🟡 Moderate | Loading + resolution + error formatting |
| `_schema/__init__.py` | **244** | 🟡 Moderate | Init files should be thin re-exports |
| `_strategies.py` | **507** | 🟡 Moderate | Multiple unrelated strategy implementations |
| `_package_cli.py` | **525** | 🟡 Moderate | CLI + validation + rendering mixed |
| `_schema/_consumer_gen.py` | **433** | 🟡 Moderate | Code generation logic |
| `_registration.py` | **435** | 🟡 Moderate | Multiple registration dataclasses + validation |
| `_command_runner.py` | **408** | 🟡 Moderate | Runner + protocol + result types |
| `_stores.py` | **402** | 🟡 Moderate | Multiple store implementations |
| `_adapter_lifecycle.py` | **393** | 🟡 Moderate | Lifecycle + state machine + events |
| `_injection.py` | **390** | 🟡 Moderate | DI container + resolver + validation |
| `_mcp/_scaffolding.py` | **486** | 🟡 Moderate | Template rendering + validation + CLI |

### Tests — `/packages/tests/unit/`

```
73 files in one flat directory  ← far exceeds 20-file threshold
```

| Test file | Lines | Maps to source |
|-----------|-------|---------------|
| `test_app_registration.py` | **2,430** | `_app.py` registration concern |
| `test_app_wiring.py` | **2,404** | `_app.py` + `_wiring.py` |
| `test_app_adapters.py` | **1,416** | `_app.py` adapter concern |
| `test_command.py` | **1,241** | `_command_runner.py` |
| `test_auto_restart.py` | **1,162** | auto-restart behaviour |
| `test_context.py` | **1,098** | `_context.py` |
| `test_mqtt.py` | **1,040** | `_mqtt.py` / `_mqtt_client.py` |
| `test_introspect.py` | **1,033** | `_mcp/_introspect.py` |
| `test_schema_cli.py` | **1,006** | `_schema/_cli.py` |
| `test_render_adr.py` | **869** | `_mcp/_adrs.py` |
| `test_strategies.py` | **849** | `_strategies.py` |
| `test_package_cli.py` | **832** | `_package_cli.py` |
| `test_schema_consumer_gen.py` | **817** | `_schema/_consumer_gen.py` |

12 test files currently mirror `_app.py`'s multiple concerns — a direct symptom of the
God class.

---

## Options

### Option A — Full Decomposition (Recommended)

Extract the three most critical God files into sub-packages, and introduce sub-directories
in `tests/unit/`.

#### `_app.py` (2,475 lines) → `_app/` package

```
cosalette/_app/
    __init__.py       # public App class, thin composition root
    _device.py        # @app.device decorator + DeviceRegistration logic
    _telemetry.py     # @app.telemetry + TelemetryRegistration
    _command.py       # @app.command + CommandRegistration
    _adapters.py      # app.adapter() + AdapterLifecycle orchestration
    _lifecycle.py     # startup / shutdown / signal handling
    _periodic.py      # @app.periodic + per-device scheduling
```

**Why:** `_app.py` is a textbook God class.  SRP (SOLID) says each class/module should
have one reason to change.  Splitting by decorator family is both idiomatic (FastAPI uses
this pattern in its internal packages) and matches the existing test naming (`test_app_registration`,
`test_app_telemetry`, etc.) — so the test split follows naturally.

**Gotcha:** All existing `from cosalette._app import App` imports continue to work because
`_app/__init__.py` re-exports `App`.

#### `_wiring.py` (1,594 lines) → `_wiring/` package

```
cosalette/_wiring/
    __init__.py       # public WiringGraph / wire() re-exports
    _graph.py         # dependency graph construction
    _resolution.py    # resolution order, cycle detection
    _validation.py    # type checking, schema validation
```

**Why:** `_wiring.py` mixes three distinct algorithmic concerns.  Separating them
enables targeted testing and easier debugging of each phase.

#### `tests/unit/` → sub-directories mirroring source

```
tests/unit/
    conftest.py
    app/
        conftest.py           # shared app fixtures
        test_registration.py  # from test_app_registration.py
        test_wiring.py        # from test_app_wiring.py
        test_adapters.py      # from test_app_adapters.py
        test_telemetry.py     # from test_app_telemetry.py
        test_state.py         # from test_app_state.py
        test_init.py          # from test_app_init.py
        test_periodic.py      # from test_app_periodic.py
        test_command.py       # from test_app_command.py
        test_app.py           # from test_app.py
    schema/
        test_schema.py
        test_schema_cli.py
        test_schema_validator.py
        test_schema_monitor.py
        test_schema_enforcement.py
        test_schema_consumer_gen.py
        test_schema_acl.py
    mcp/
        test_introspect.py    # from test_introspect.py
        test_mcp_*.py         # all mcp tests
    (remaining 40+ unit tests stay at unit/ root — already focused)
```

**Why:** pytest's own documentation recommends mirroring source layout.  73 flat files
make it genuinely hard to find related tests at a glance.  Sub-directories create a
navigational contract: if I change `_app.py`, I look in `tests/unit/app/`.

**Advantages:**
- Mirrors source structure → discoverability
- Each sub-directory `conftest.py` can hold domain-specific fixtures
- pytest works without any config changes (it recurses automatically)
- Avoids the double-blast of "fix the source, also fix the giant test file" by
  encouraging co-evolution

**Disadvantages:**
- Non-trivial migration: 73 renames + import updates + conftest extraction
- CI paths in coverage thresholds may need updates

---

### Option B — Thin Extraction Only

Only split `_app.py` (the worst offender) into an `_app/` package.  Leave `_wiring.py`,
`tests/unit/`, and moderate files unchanged.

**Advantages:** Lower risk, faster, targeted.
**Disadvantages:** Doesn't address the 73-flat-file test problem or the second largest file.

---

### Option C — File-Size Cap Only (No Structural Change)

Add a `max-doc-length = 500` or `max-complexity` gate to `ruff` / cyclomatic complexity
checks.  Flag violations in CI but don't restructure now.

**Advantages:** Zero migration risk, purely additive.
**Disadvantages:** Doesn't fix existing violations, only prevents new ones.  The God
class remains a maintenance liability.

---

## Recommendation

**Option A** — prioritised in phases:

| Phase | Scope | Risk |
|-------|-------|------|
| 1 | Split `tests/unit/` into sub-directories | Low — pytest recurses, no imports to update |
| 2 | Extract `_app.py` → `_app/` package | Medium — public import surface preserved via `__init__.py` re-exports |
| 3 | Extract `_wiring.py` → `_wiring/` package | Medium — internal use, fewer external import paths |
| 4 | Split `_ai_content.py`, moderate source files (≥500 lines) | Low–Medium |

Phase 1 is effectively free: pytest discovers tests by recursion, `conftest.py` fixtures
move to the lowest common ancestor directory, no source files change.

---

## Out of Scope

- Moderate files (300–500 lines): `_command_runner.py`, `_stores.py`, `_injection.py`,
  `_cron.py` — these are focused enough to address in a follow-up.
- `_ai_content.py` (1,130 lines): data-heavy file, lower priority unless it grows further.
- `_schema/` (9 files): already reasonably partitioned; `_schema/__init__.py` (244 lines)
  could be trimmed but isn't blocking.
