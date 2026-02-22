# Signal Filters Utility Library

*2026-02-22T20:32:20Z by Showboat 0.5.0*

Added three signal filters to cosalette as a utility library (ADR-014): Pt1Filter (first-order low-pass, sample-rate-independent EWMA), MedianFilter (sliding-window median for spike rejection), and OneEuroFilter (adaptive 1€ filter for variable-rate signals). All satisfy a Filter protocol with update/reset/value. No external dependencies — stdlib math, statistics, and collections.deque only.

```bash
cd /workspace && uv run python -c "from cosalette.filters import Pt1Filter, MedianFilter, OneEuroFilter, Filter; f = Pt1Filter(tau=2.0, dt=0.5); [f.update(10.0) for _ in range(20)]; print(f'Pt1Filter converged to: {f.value:.4f}'); m = MedianFilter(window=5); vals = [1,2,3,100,5]; [m.update(v) for v in vals]; print(f'MedianFilter (spike rejection): {m.value}'); print(f'All implement Filter protocol: {all(isinstance(c(tau=1,dt=1) if c is Pt1Filter else c(window=3) if c is MedianFilter else c(), Filter) for c in [Pt1Filter, MedianFilter, OneEuroFilter])}')"
```

```output
Pt1Filter converged to: 10.0000
MedianFilter (spike rejection): 3
All implement Filter protocol: True
```

```bash
cd /workspace && task test:file -- packages/tests/unit/test_filters.py 2>&1 | tail -1 | sed 's/in [0-9.]*s/in Xs/'
```

```output
============================== 44 passed in Xs ==============================
```
