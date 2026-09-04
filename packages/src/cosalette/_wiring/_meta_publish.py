"""Shared publish helper for framework-owned retained ``_meta/`` snapshots.

The ``_meta/registry`` and ``_meta/state_model_drift`` snapshots share the same
posture (ADR-069): a retained QoS-1 message, computed once after setup, cached
as a serialised string on the ``App`` and republished byte-identically on every
connect, with broker failures logged rather than propagated. This helper is the
single implementation of that posture so a third ``_meta/`` snapshot cannot
drift from it.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from cosalette._mqtt import MqttPort

logger = logging.getLogger("cosalette._wiring")


async def publish_retained_cached(
    app: Any,  # App — Any to avoid circular import
    mqtt: MqttPort,
    topic: str,
    cache_attr: str,
    build_payload: Callable[[], str],
    *,
    failure_desc: str,
) -> None:
    """Publish a cached retained QoS-1 snapshot (fire-and-forget).

    On the first call *build_payload* serialises the document and the result is
    cached on *app* under *cache_attr*; subsequent calls reuse the cached string
    so reconnect republishes are byte-identical. Any error — serialisation or a
    dead broker — is logged as ``"Failed to publish {failure_desc} to {topic}"``
    and swallowed, so startup never aborts on a publish failure.

    Args:
        app: The ``App`` — typed ``Any`` to avoid a circular import.
        mqtt: The MQTT port to publish through.
        topic: Fully-qualified retained topic.
        cache_attr: Attribute name on *app* holding the cached payload string.
        build_payload: Serialises the document; called at most once per *app*.
        failure_desc: Human-readable subject for the failure log line.
    """
    try:
        payload_str: str | None = getattr(app, cache_attr, None)
        if payload_str is None:
            payload_str = build_payload()
            with contextlib.suppress(TypeError, AttributeError):
                object.__setattr__(app, cache_attr, payload_str)
        await mqtt.publish(topic, payload_str, retain=True, qos=1)
    except Exception:
        logger.exception("Failed to publish %s to %s", failure_desc, topic)
