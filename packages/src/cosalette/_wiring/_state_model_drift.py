"""Machine-readable ``state_model`` declaration-drift snapshot (ADR-069).

ADR-068 clause F warns once per registration when a handler declares
``state_model=M`` but is annotated ``-> N``.  A once-per-boot warning cannot be
scraped across a fleet of unattended daemons, so the same fact is published as a
retained JSON snapshot on ``{prefix}/_meta/state_model_drift`` — one
subscription (``+/_meta/state_model_drift``) answers "which apps ship a handler
whose declared contract disagrees with its code?" for a whole broker.

A clean app publishes ``drift_count: 0`` rather than nothing, so "no drift" is
distinguishable from "never ran a version that publishes drift".
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from cosalette._constants import STATE_MODEL_DRIFT_TOPIC_SUFFIX
from cosalette._json import dumps as _json_dumps
from cosalette._registration import state_model_conflict_labels

if TYPE_CHECKING:
    from cosalette._mqtt import MqttPort

logger = logging.getLogger("cosalette._wiring")

# Payload envelope version — bumped only on an incompatible payload change.
_DRIFT_SCHEMA_VERSION = 1

# Discriminator for the only drift kind in ADR-069 scope: a handler whose
# ``state_model=`` and return annotation name different types.
_ANNOTATION_CONFLICT = "annotation_conflict"

_CACHE_ATTR = "_state_model_drift_cache"


def build_state_model_drift_snapshot(app: Any) -> dict[str, Any]:
    """Build the ADR-069 drift document for *app*'s registration set.

    Only ``@app.telemetry`` and ``@app.command`` can drift: ``@app.device`` and
    ``@app.stream`` validate ``ctx.publish_state()`` payloads and have no return
    contract to contradict.  The registration set is fixed once setup completes,
    so the document is static for the process lifetime.

    Args:
        app: The ``App`` — typed ``Any`` to avoid a circular import.

    Returns:
        A ``schema_version``-stamped envelope over the drift records.
    """
    entries: list[dict[str, str]] = []
    for archetype, registrations in (
        ("telemetry", app.telemetry_registrations),
        ("command", app.commands),
    ):
        for reg in registrations:
            labels = state_model_conflict_labels(reg.func, reg.state_model)
            if labels is None:
                continue
            declared, effective = labels
            entries.append(
                {
                    "handler": reg.name,
                    "archetype": archetype,
                    "kind": _ANNOTATION_CONFLICT,
                    "declared_model": declared,
                    "effective_annotation": effective,
                }
            )
    return {
        "schema_version": _DRIFT_SCHEMA_VERSION,
        "drift_count": len(entries),
        "entries": entries,
    }


async def publish_state_model_drift_snapshot(
    app: Any,  # App — Any to avoid circular import
    mqtt: MqttPort,
    prefix: str,
) -> None:
    """Publish the drift snapshot to MQTT (fire-and-forget).

    Retained at QoS 1 so a monitor connecting hours after boot still sees the
    current state, and republished on every connect because a broker restart
    drops retained messages.  The serialised payload is cached on *app* — the
    drift set is immutable after setup — so reconnect republishes are
    byte-identical.  Errors are logged but never propagated.

    .. warning:: Security

       The payload names handler identifiers and model class names.  This is a
       strict subset of what the always-on ``{prefix}/_meta/registry`` snapshot
       already discloses; shared-broker deployments protect both with the same
       ``_meta/#`` broker ACL rules.
    """
    topic = f"{prefix}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}"
    try:
        payload_str: str | None = getattr(app, _CACHE_ATTR, None)
        if payload_str is None:
            payload_str = _json_dumps(build_state_model_drift_snapshot(app))
            with contextlib.suppress(TypeError, AttributeError):
                object.__setattr__(app, _CACHE_ATTR, payload_str)
        await mqtt.publish(topic, payload_str, retain=True, qos=1)
    except Exception:
        logger.exception("Failed to publish state_model drift snapshot to %s", topic)
