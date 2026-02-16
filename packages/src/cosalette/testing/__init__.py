"""Public test-support utilities for cosalette.

Re-exports test doubles and factories so that consumer test suites
can import everything from a single ``cosalette.testing`` namespace
instead of reaching into private modules.

Provided symbols:

- :class:`AppHarness` — test harness wrapping App with pre-configured doubles.
- :class:`MockMqttClient` — in-memory MQTT double that records calls.
- :class:`NullMqttClient` — silent no-op MQTT adapter.
- :class:`FakeClock` — deterministic clock for timing tests.
- :func:`make_settings` — factory for ``Settings`` without ``.env`` files.

See Also:
    ADR-007 for testing strategy decisions.
"""

from cosalette._mqtt import MockMqttClient, NullMqttClient
from cosalette.testing._clock import FakeClock
from cosalette.testing._harness import AppHarness
from cosalette.testing._settings import make_settings

__all__ = [
    "AppHarness",
    "FakeClock",
    "MockMqttClient",
    "NullMqttClient",
    "make_settings",
]
