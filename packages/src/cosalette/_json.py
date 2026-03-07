"""Centralised JSON helpers — thin wrappers around *orjson*.

Every module that needs JSON serialisation imports from here rather than
from ``json`` or ``orjson`` directly.  This gives us a single choke-point
for configuration and a trivial swap-path if the backend ever changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import orjson

# Re-export so callers can ``except JSONDecodeError`` without an extra import.
JSONDecodeError = orjson.JSONDecodeError


def dumps(obj: object, *, default: Callable[[Any], Any] | None = None) -> str:
    """Serialize *obj* to a JSON string.

    orjson.dumps() returns ``bytes``; we decode to ``str`` because every
    call-site in cosalette (MQTT publish, to_json(), …) expects a string.
    """
    return orjson.dumps(obj, default=default).decode()


def dumps_pretty(obj: object) -> str:
    """Serialize *obj* with 2-space indentation (for human-readable stores)."""
    return orjson.dumps(obj, option=orjson.OPT_INDENT_2).decode()


def loads(data: str | bytes) -> Any:
    """Deserialize a JSON string or bytes."""
    return orjson.loads(data)
