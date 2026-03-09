# Rust-only signal filters (ADR-022)

*2026-03-09T19:02:52Z by Showboat 0.6.1*
<!-- showboat-id: e4a86915-3761-4d53-9860-2eff62fdcfe4 -->

cosalette-filters-rs is now a hard dependency (ADR-022). Python filter implementations removed. _filters.py retains only the Filter Protocol. All imports via cosalette.filters route directly to Rust.

```bash
uv run python -c "from cosalette.filters import Pt1Filter, MedianFilter, OneEuroFilter, Filter; f = Pt1Filter(tau=1.0, dt=0.1); [f.update(42.0) for _ in range(5)]; print(f'Pt1Filter type: {type(Pt1Filter).__module__}'); print(f'value after 5 updates: {f.value:.4f}'); print(f'isinstance check: {isinstance(f, Filter)}')"
```

```output
Pt1Filter type: builtins
value after 5 updates: 42.0000
isinstance check: True
```

```bash
uv run python -c "import cosalette; from cosalette.filters import Pt1Filter; print(f'Top-level is same object: {cosalette.Pt1Filter is Pt1Filter}')"
```

```output
Top-level is same object: True
```

```bash
grep 'cosalette-filters-rs' pyproject.toml | head -3
```

```output
    "cosalette-filters-rs>=0.1.0",
cosalette-filters-rs = { path = "crates/cosalette-filters-rs", editable = true }
```

```bash
wc -l packages/src/cosalette/_filters.py && echo '---' && head -5 packages/src/cosalette/_filters.py
```

```output
40 packages/src/cosalette/_filters.py
---
"""Signal filter protocol.

Defines the structural typing contract that all filter implementations
must satisfy.  Concrete implementations live in ``cosalette-filters-rs``
(Rust/pyo3).  See ADR-014 for design rationale and ADR-022 for the
```
