"""Compatibility stub: _stream moved to cosalette._runners._stream_primitives.

Deprecated: import from cosalette._runners._stream_primitives directly.
This stub will be removed in a follow-up cleanup PR.
"""

# Re-exported for backward compatibility
from cosalette._runners._stream_primitives import (
    BackpressurePolicy,
    Stream,
    StreamablePort,
)

__all__ = ["BackpressurePolicy", "Stream", "StreamablePort"]
