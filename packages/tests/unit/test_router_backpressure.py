"""Tests for router command-queue backpressure (Finding 3a)."""

from __future__ import annotations

import asyncio

import pytest

from cosalette._mqtt._router import TopicRouter


@pytest.mark.asyncio
async def test_router_bounded_queue_drop_oldest() -> None:
    """Bounded queue with drop_oldest keeps newest messages and balances join()."""
    router = TopicRouter(topic_prefix="test")
    messages: list[str] = []
    gate = asyncio.Event()

    async def handler(topic: str, payload: str) -> None:
        await gate.wait()
        messages.append(payload)

    # Register with maxsize=1, drop_oldest
    router.register("dev", handler, maxsize=1, backpressure="drop_oldest")

    # Enqueue first message and yield to let worker dequeue it
    await router.route("test/dev/set", "msg1")
    await asyncio.sleep(0)  # Let worker dequeue msg1 and block on gate

    # Enqueue 2 more messages while handler is blocked on msg1
    await router.route("test/dev/set", "msg2")
    await router.route("test/dev/set", "msg3")

    # Release handler
    gate.set()
    await router.wait_idle()

    # msg1 should be processing when msg2/msg3 arrive
    # Queue after msg2: [msg2]
    # Queue after msg3 with drop_oldest: [msg3] (msg2 evicted)
    # Result: msg1 and msg3 processed
    assert len(messages) == 2
    assert messages == ["msg1", "msg3"]

    await router.aclose()


@pytest.mark.asyncio
async def test_router_bounded_queue_drop_newest() -> None:
    """Bounded queue with drop_newest keeps oldest messages."""
    router = TopicRouter(topic_prefix="test")
    messages: list[str] = []
    gate = asyncio.Event()

    async def handler(topic: str, payload: str) -> None:
        await gate.wait()
        messages.append(payload)

    # Register with maxsize=2, drop_newest
    router.register("dev", handler, maxsize=2, backpressure="drop_newest")

    # Enqueue 4 messages while handler is blocked
    await router.route("test/dev/set", "msg1")
    await router.route("test/dev/set", "msg2")
    await router.route("test/dev/set", "msg3")  # dropped
    await router.route("test/dev/set", "msg4")  # dropped

    # Release handler
    gate.set()
    await router.wait_idle()

    # Only msg1 and msg2 should survive
    assert len(messages) == 2
    assert messages == ["msg1", "msg2"]

    await router.aclose()


@pytest.mark.asyncio
async def test_router_bounded_queue_raise() -> None:
    """Bounded queue with raise policy surfaces QueueFull."""
    router = TopicRouter(topic_prefix="test")
    gate = asyncio.Event()

    async def handler(topic: str, payload: str) -> None:
        await gate.wait()

    # Register with maxsize=1, raise
    router.register("dev", handler, maxsize=1, backpressure="raise")

    # First message enqueues fine
    await router.route("test/dev/set", "msg1")

    # Second message raises QueueFull (no await, enqueue is sync)
    with pytest.raises(asyncio.QueueFull):
        await router.route("test/dev/set", "msg2")

    gate.set()
    await router.wait_idle()
    await router.aclose()


@pytest.mark.asyncio
async def test_router_unbounded_queue_ignores_policy() -> None:
    """Unbounded queue (maxsize=0) never applies backpressure policy."""
    router = TopicRouter(topic_prefix="test")
    messages: list[str] = []
    gate = asyncio.Event()

    async def handler(topic: str, payload: str) -> None:
        await gate.wait()
        messages.append(payload)

    # Register unbounded (default maxsize=0)
    router.register("dev", handler, maxsize=0, backpressure="drop_newest")

    # Enqueue many messages
    for i in range(100):
        await router.route("test/dev/set", f"msg{i}")

    # Release handler
    gate.set()
    await router.wait_idle()

    # All messages should survive
    assert len(messages) == 100

    await router.aclose()


@pytest.mark.asyncio
async def test_router_root_device_bounded_queue() -> None:
    """Root device queue also honors bounded settings."""
    router = TopicRouter(topic_prefix="test")
    messages: list[str] = []
    gate = asyncio.Event()

    async def handler(topic: str, payload: str) -> None:
        await gate.wait()
        messages.append(payload)

    # Register root with maxsize=1, drop_oldest
    router.register(
        "<root>",
        handler,
        is_root=True,
        maxsize=1,
        backpressure="drop_oldest",
    )

    # Enqueue first message and yield to let worker dequeue it
    await router.route("test/set", "msg1")
    await asyncio.sleep(0)  # Let worker dequeue msg1 and block on gate

    # Enqueue 2 more messages while handler is blocked on msg1
    await router.route("test/set", "msg2")
    await router.route("test/set", "msg3")

    # Release handler
    gate.set()
    await router.wait_idle()

    # msg1 processing when msg2/msg3 arrive, msg2 evicted by msg3
    assert len(messages) == 2
    assert messages == ["msg1", "msg3"]

    await router.aclose()
