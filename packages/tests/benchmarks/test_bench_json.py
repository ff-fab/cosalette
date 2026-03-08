"""Performance benchmarks for JSON serialization.

Run with: task test:bench
"""

from __future__ import annotations

import pytest

from cosalette._json import dumps

SMALL_PAYLOAD = {"celsius": 22.5}

MEDIUM_PAYLOAD = {
    "temperature": 22.5,
    "humidity": 55.0,
    "pressure": 1013.25,
    "wind_speed": 3.2,
    "uv_index": 6,
}

LARGE_PAYLOAD = {
    "status": "online",
    "uptime_s": 3600.0,
    "version": "0.1.8",
    "devices": {
        f"device_{i}": {"status": "ok", "last_seen": 1234567890 + i} for i in range(10)
    },
}


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "payload",
    [SMALL_PAYLOAD, MEDIUM_PAYLOAD, LARGE_PAYLOAD],
    ids=["small", "medium", "large"],
)
def test_dumps(benchmark, payload):
    benchmark(dumps, payload)
