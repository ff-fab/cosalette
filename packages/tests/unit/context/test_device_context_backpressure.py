"""Tests for DeviceContext command-queue backpressure behaviors.

Test Techniques Used:
- Equivalence Partitioning: three backpressure policies
  (drop_oldest, drop_newest, raise)
- Boundary Value Analysis: maxsize=0 (unbounded) vs maxsize>0 (bounded)
- State Transition Testing: queue full → policy applied → queue state verified
"""

from __future__ import annotations

import asyncio

import pytest

from cosalette._command import Command
from cosalette._context import DeviceContext
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


def test_device_context_bounded_command_queue_drop_oldest() -> None:
    """DeviceContext command queue with drop_oldest keeps newest."""
    ctx = DeviceContext(
        name="test",
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="test",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        command_maxsize=2,
        command_backpressure="drop_oldest",
    )

    # Enqueue 4 commands
    for i in range(4):
        cmd = Command(topic="test/test/set", payload=f"msg{i}", timestamp=0.0)
        ctx._enqueue_command(cmd)

    # Queue should have msg2 and msg3 (msg0 evicted by msg2, msg1 evicted by msg3)
    cmd1 = ctx._command_queue.get_nowait()
    cmd2 = ctx._command_queue.get_nowait()
    assert cmd1.payload == "msg2"
    assert cmd2.payload == "msg3"
    assert ctx._command_queue.empty()


def test_device_context_bounded_command_queue_drop_newest() -> None:
    """DeviceContext command queue with drop_newest keeps oldest."""
    ctx = DeviceContext(
        name="test",
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="test",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        command_maxsize=2,
        command_backpressure="drop_newest",
    )

    # Enqueue 4 commands
    for i in range(4):
        cmd = Command(topic="test/test/set", payload=f"msg{i}", timestamp=0.0)
        ctx._enqueue_command(cmd)

    # Queue should have msg0 and msg1 (msg2 and msg3 dropped)
    cmd1 = ctx._command_queue.get_nowait()
    cmd2 = ctx._command_queue.get_nowait()
    assert cmd1.payload == "msg0"
    assert cmd2.payload == "msg1"
    assert ctx._command_queue.empty()


def test_device_context_bounded_command_queue_raise() -> None:
    """DeviceContext command queue with raise propagates QueueFull."""
    ctx = DeviceContext(
        name="test",
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="test",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        command_maxsize=1,
        command_backpressure="raise",
    )

    # First enqueue succeeds
    cmd1 = Command(topic="test/test/set", payload="msg0", timestamp=0.0)
    ctx._enqueue_command(cmd1)

    # Second enqueue raises QueueFull
    cmd2 = Command(topic="test/test/set", payload="msg1", timestamp=0.0)
    with pytest.raises(asyncio.QueueFull):
        ctx._enqueue_command(cmd2)


def test_device_context_unbounded_command_queue() -> None:
    """DeviceContext command queue unbounded (maxsize=0) keeps all."""
    ctx = DeviceContext(
        name="test",
        settings=make_settings(),
        mqtt=MockMqttClient(),
        topic_prefix="test",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=FakeClock(),
        command_maxsize=0,  # unbounded
        command_backpressure="drop_newest",
    )

    # Enqueue 100 commands
    for i in range(100):
        cmd = Command(topic="test/test/set", payload=f"msg{i}", timestamp=0.0)
        ctx._enqueue_command(cmd)

    # All should be in queue
    assert ctx._command_queue.qsize() == 100
