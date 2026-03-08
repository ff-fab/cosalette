"""Performance benchmarks for signal filters.

Run with: task test:bench

Test Techniques Used:
    - Performance Benchmarking: steady-state update() throughput
    - Equivalence Partitioning: representative dt values (100Hz/1kHz/10kHz),
      window sizes (3/5/11)
"""

from __future__ import annotations

import pytest

from cosalette._filters import MedianFilter, OneEuroFilter, Pt1Filter


# Pt1Filter benchmarks
@pytest.mark.benchmark
@pytest.mark.parametrize(
    "dt",
    [0.01, 0.001, 0.0001],
    ids=["100Hz", "1kHz", "10kHz"],
)
def test_pt1_update(benchmark, dt):
    filt = Pt1Filter(tau=1.0, dt=dt)
    filt.update(1.0)
    benchmark(filt.update, 42.0)


# MedianFilter benchmarks
@pytest.mark.benchmark
@pytest.mark.parametrize(
    "window",
    [3, 5, 11],
    ids=["win3", "win5", "win11"],
)
def test_median_update(benchmark, window):
    filt = MedianFilter(window=window)
    for i in range(window):
        filt.update(float(i))
    benchmark(filt.update, 42.0)


# OneEuroFilter benchmark
@pytest.mark.benchmark
def test_one_euro_update(benchmark):
    filt = OneEuroFilter(min_cutoff=1.0, beta=0.007, d_cutoff=1.0, dt=0.1)
    filt.update(20.0)
    benchmark(filt.update, 20.5)
