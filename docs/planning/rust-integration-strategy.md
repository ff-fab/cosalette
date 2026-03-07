# Rust Integration Strategy for cosalette

**Date:** 2026-03-07
**Context:** Framework review action point P4.1 from `framework-evaluation.md`
**Status:** Proposal — awaiting evaluation

---

## Motivation

Two forces converge:

1. **Performance** — Some cosalette apps run on Raspberry Pi Zero (ARMv6, 512 MB RAM).
   Signal filters at high sampling rates (100 Hz+) are CPU-bound in pure Python. JSON
   serialization on every MQTT publish adds overhead on constrained hardware.
2. **Learning opportunity** — The owner explicitly values the Rust learning experience
   and is willing to invest effort even for moderate performance gains.

## Why cosalette's Architecture Makes This Clean

The hexagonal architecture (ADR-006) and protocol-based contracts (PEP 544) create
natural FFI seams:

```
Python handler code
       ↕ (Filter protocol: update(float) → float)
cosalette._filters     ← Pure Python (default)
cosalette-filters-rs   ← Rust via pyo3 (optional accelerator)
```

- Filters implement a **3-method protocol** (`update`, `value`, `reset`)
- No framework changes needed — Rust classes satisfy the same `Filter` protocol
- Users don't change their code — the import path swaps transparently

## Candidate Components

### Tier 1: Signal Filters (Best Starting Point)

| Filter | Complexity | Rust Benefit | Lines of Rust (est.) |
|--------|-----------|-------------|---------------------|
| `Pt1Filter` | Low | High for high-freq | ~30 |
| `MedianFilter` | Medium | High (sliding window) | ~60 |
| `OneEuroFilter` | Medium | High (adaptive) | ~80 |

**Why start here:**

- Self-contained — no async, no I/O, pure computation
- Small API surface — 3 methods per filter
- Perfect pyo3 learning project — teaches struct ↔ PyClass, method ↔ pymethods
- Testable — same test suite runs against both Python and Rust implementations
- Measurable — `pytest-benchmark` can compare directly

### Tier 2: JSON Serialization (Quick Win via Existing Crate)

No custom Rust needed. `orjson` is a Rust-based drop-in replacement for `json.dumps()`:

```python
# cosalette/_mqtt.py — conditional import
try:
    import orjson
    def _serialize(payload: dict) -> bytes:
        return orjson.dumps(payload)
except ImportError:
    import json
    def _serialize(payload: dict) -> bytes:
        return json.dumps(payload).encode()
```

**Benchmark expectation:** 5–10× faster serialization. Meaningful on RPi Zero where
every millisecond counts for high-frequency telemetry.

### Tier 3: Not Recommended for Rust

| Component | Why Not |
|-----------|---------|
| MQTT client | I/O-bound, async — Rust doesn't help. `aiomqtt` is fine. |
| Coalescing scheduler | Already efficient (`heapq` + int math). Negligible overhead. |
| DI / injection | Runs once at registration. Not a hot path. |
| App lifecycle | Orchestration logic — Python is the right tool here. |

## Implementation Plan for Tier 1

### Phase 0: Benchmark Baseline

Before writing Rust, establish baselines on target hardware:

```bash
# Run on RPi Zero, RPi Zero 2, and dev machine
uv run pytest tests/benchmarks/ --benchmark-json=baseline.json
```

Benchmarks to create:

- `Pt1Filter.update()` throughput at 100 Hz, 1 kHz, 10 kHz simulated sample rates
- `MedianFilter.update()` with window sizes 3, 5, 11
- `OneEuroFilter.update()` with typical parameters
- `json.dumps()` for typical telemetry payloads (3–10 fields, nested dicts)

### Phase 1: Pt1Filter in Rust

**Goal:** End-to-end pipeline: Rust crate → pyo3 bindings → maturin build → Python
tests → CI wheels.

**Crate structure:**

```
cosalette-filters-rs/
├── Cargo.toml
├── pyproject.toml          # maturin build config
├── src/
│   ├── lib.rs              # pyo3 module definition
│   └── pt1.rs              # Pt1Filter implementation
└── tests/
    └── test_pt1.rs         # Rust unit tests
```

**Rust implementation sketch:**

```rust
use pyo3::prelude::*;

#[pyclass]
pub struct Pt1Filter {
    tau: f64,
    dt: f64,
    alpha: f64,
    value: Option<f64>,
}

#[pymethods]
impl Pt1Filter {
    #[new]
    fn new(tau: f64, dt: f64) -> PyResult<Self> {
        if tau <= 0.0 || dt <= 0.0 {
            return Err(PyValueError::new_err("tau and dt must be positive"));
        }
        let alpha = dt / (tau + dt);
        Ok(Self { tau, dt, alpha, value: None })
    }

    #[getter]
    fn value(&self) -> Option<f64> { self.value }

    fn update(&mut self, raw: f64) -> f64 {
        let filtered = match self.value {
            None => raw,
            Some(prev) => self.alpha * raw + (1.0 - self.alpha) * prev,
        };
        self.value = Some(filtered);
        filtered
    }

    fn reset(&mut self) { self.value = None; }
}
```

**Python integration:**

```python
# cosalette/filters.py — the public import path
try:
    from cosalette_filters_rs import Pt1Filter as _RustPt1Filter
    _HAS_RUST_FILTERS = True
except ImportError:
    _HAS_RUST_FILTERS = False

from cosalette._filters import Pt1Filter as _PythonPt1Filter

# Default to Rust if available, with explicit override
Pt1Filter = _RustPt1Filter if _HAS_RUST_FILTERS else _PythonPt1Filter
```

### Phase 2: MedianFilter + OneEuroFilter

Same pattern. `MedianFilter` uses a `VecDeque<f64>` (Rust equivalent of
`collections.deque`). `OneEuroFilter` uses the adaptive cutoff calculation.

### Phase 3: CI Wheel Matrix

Use maturin + GitHub Actions to build wheels for:

- manylinux (x86_64, aarch64)
- musllinux (x86_64, aarch64) — for Alpine-based containers
- macOS (x86_64, arm64)
- **linux armv6l / armv7l** — critical for RPi Zero targets

The armv6l target requires cross-compilation (`cross` or `cargo-zigbuild`).

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Rust version != Python version behavior | Run identical test suite against both. Parametrize with `@pytest.mark.parametrize("impl", [PythonPt1, RustPt1])` |
| Build complexity for ARM wheels | Start with x86 only. Add ARM after pipeline is stable. |
| `NaN`/`Inf` edge cases differ | Explicit tests for `NaN`, `Inf`, `-Inf`, subnormals |
| Maintenance burden of two codebases | Python remains the reference. Rust is the accelerator. If they diverge, Python wins. |

## Decision Criteria: When Is Rust Worth It?

Pursue Rust acceleration when the benchmark shows:

- Filter throughput < 10,000 updates/second on RPi Zero (Python)
- JSON serialization adds > 1ms per publish on RPi Zero
- Any hot path consuming > 10% of CPU at target polling rate

If Python is fast enough on target hardware, defer Rust and focus on framework
ergonomics instead.
