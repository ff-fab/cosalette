# Benchmark Suite for Hot Paths

*2026-03-08T16:40:09Z by Showboat 0.6.1*
<!-- showboat-id: b463ad07-600d-4f8a-a614-1958a99d54ec -->

Added pytest-benchmark infrastructure and 10 benchmarks covering all three signal filters (Pt1Filter, MedianFilter, OneEuroFilter) and JSON serialization via cosalette._json.dumps(). Benchmarks are disabled by default and run via task test:bench.

```bash
task test:bench 2>&1 | grep -c PASSED
```

```output
10
```

```bash
task test:unit 2>&1 | grep -oP '\d+ passed'
```

```output
952 passed
```
