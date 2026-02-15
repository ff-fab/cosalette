"""Pytest plugin providing shared test fixtures for cosalette.

Auto-registers ``mock_mqtt``, ``fake_clock``, and ``device_context``
fixtures for any test suite that depends on cosalette.

Discovered automatically via the ``pytest11`` entry point — no explicit
``pytest_plugins`` import is needed in consumer ``conftest.py`` files.

**Why lazy imports?** This module is loaded by pytest during plugin
discovery — *before* coverage measurement starts.  Eager top-level
imports of cosalette modules would cause those modules to be imported
before ``pytest-cov`` begins tracing, resulting in artificially low
coverage numbers.  Deferring imports into the fixture bodies ensures
all cosalette code is first touched while coverage is active.
(ADR-006 lazy-import pattern.)

See Also:
    ADR-007 — Testing strategy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from cosalette._context import DeviceContext
    from cosalette._mqtt import MockMqttClient
    from cosalette.testing._clock import FakeClock


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    """Fresh MockMqttClient for each test."""
    from cosalette._mqtt import MockMqttClient

    return MockMqttClient()


@pytest.fixture
def fake_clock() -> FakeClock:
    """FakeClock starting at time 0."""
    from cosalette.testing._clock import FakeClock

    return FakeClock()


@pytest.fixture
def device_context(mock_mqtt: MockMqttClient, fake_clock: FakeClock) -> DeviceContext:
    """DeviceContext wired with test doubles.

    Provides a ready-to-use context configured with:

    - ``name="test_device"``
    - ``topic_prefix="test"``
    - MockMqttClient and FakeClock from companion fixtures
    - ``make_settings()`` defaults
    - Fresh ``asyncio.Event`` for shutdown
    """
    import asyncio

    from cosalette._context import DeviceContext
    from cosalette.testing._settings import make_settings

    return DeviceContext(
        name="test_device",
        settings=make_settings(),
        mqtt=mock_mqtt,
        topic_prefix="test",
        shutdown_event=asyncio.Event(),
        adapters={},
        clock=fake_clock,
    )
