"""Tests for the apply_backpressure() helper in _runners/_stream_types.py.

Test Techniques Used:
- Equivalence Partitioning: queue full vs not-full, all three policies
- Boundary Value Analysis: maxsize=0 (unbounded boundary)
- Error Guessing: raise policy propagates QueueFull
- Specification-based Testing: on_evict callback contract
"""

from __future__ import annotations

import asyncio

import pytest

from cosalette._runners._stream_types import apply_backpressure

pytestmark = pytest.mark.unit


def test_apply_backpressure_unbounded_no_op() -> None:
    """Unbounded queue (maxsize=0) ignores policy."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=0)
    for i in range(100):
        apply_backpressure(q, f"item{i}", "drop_newest")
    assert q.qsize() == 100


def test_apply_backpressure_drop_newest() -> None:
    """drop_newest discards incoming item when queue is full."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
    apply_backpressure(q, "item1", "drop_newest")
    apply_backpressure(q, "item2", "drop_newest")
    apply_backpressure(q, "item3", "drop_newest")  # dropped

    assert q.qsize() == 2
    assert q.get_nowait() == "item1"
    assert q.get_nowait() == "item2"
    assert q.empty()


def test_apply_backpressure_drop_oldest() -> None:
    """drop_oldest evicts oldest item when queue is full."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
    apply_backpressure(q, "item1", "drop_oldest")
    apply_backpressure(q, "item2", "drop_oldest")
    apply_backpressure(q, "item3", "drop_oldest")  # evicts item1

    assert q.qsize() == 2
    assert q.get_nowait() == "item2"
    assert q.get_nowait() == "item3"
    assert q.empty()


def test_apply_backpressure_drop_oldest_with_on_evict() -> None:
    """drop_oldest calls on_evict when evicting."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    evict_count = 0

    def on_evict() -> None:
        nonlocal evict_count
        evict_count += 1

    apply_backpressure(q, "item1", "drop_oldest", on_evict=on_evict)
    apply_backpressure(q, "item2", "drop_oldest", on_evict=on_evict)  # evicts item1

    assert evict_count == 1
    assert q.get_nowait() == "item2"


def test_apply_backpressure_raise() -> None:
    """raise policy propagates QueueFull."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    apply_backpressure(q, "item1", "raise")

    with pytest.raises(asyncio.QueueFull):
        apply_backpressure(q, "item2", "raise")


def test_apply_backpressure_not_full_enqueues_normally() -> None:
    """When queue is not full, enqueues regardless of policy."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
    apply_backpressure(q, "item1", "drop_newest")
    assert q.qsize() == 1
    assert q.get_nowait() == "item1"


def test_apply_backpressure_drop_newest_does_not_call_on_evict() -> None:
    """drop_newest never calls on_evict — only drop_oldest does."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    q.put_nowait("existing")
    evict_calls = 0

    def on_evict() -> None:
        nonlocal evict_calls
        evict_calls += 1

    apply_backpressure(q, "incoming", "drop_newest", on_evict=on_evict)

    assert evict_calls == 0
    assert q.qsize() == 1
    assert q.get_nowait() == "existing"
