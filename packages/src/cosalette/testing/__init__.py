"""Public test-support utilities for cosalette.

Re-exports test doubles and factories so that consumer test suites
can import everything from a single ``cosalette.testing`` namespace
instead of reaching into private modules.

Provided symbols:

- :class:`AppHarness` — test harness wrapping App with pre-configured doubles.
- :class:`MockMqttClient` — in-memory MQTT double that records calls.
- :class:`NullMqttClient` — silent no-op MQTT adapter.
- :class:`FakeClock` — deterministic clock for timing tests.
- :class:`ManualClock` — gating clock whose ``sleep()`` only ``advance()``
  releases, plus ``settle()`` for negative timing assertions.
- :func:`make_settings` — factory for ``Settings`` without ``.env`` files.
- :class:`StreamHandlerProxy` — public alias for the stream proxy guard.
- :func:`assert_discovery_topics_published` — discovery↔runtime topic
  cross-check assertion (F23).

See Also:
    ADR-007 for testing strategy decisions.
"""

from cosalette._mqtt import MockMqttClient, NullMqttClient
from cosalette._runners._stream_runner import _StreamHandlerProxy as StreamHandlerProxy
from cosalette.testing._clock import FakeClock, ManualClock
from cosalette.testing._discovery import assert_discovery_topics_published
from cosalette.testing._harness import AppHarness
from cosalette.testing._settings import make_settings

__all__ = [
    "AppHarness",
    "FakeClock",
    "ManualClock",
    "MockMqttClient",
    "NullMqttClient",
    "StreamHandlerProxy",
    "assert_discovery_topics_published",
    "make_settings",
]
