"""Shared constants for the cosalette framework.

Centralises values used across multiple modules to avoid circular imports.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 3
EXIT_TASK_FAILURE = 4
"""A supervised task failed and ended the app (ADR-081)."""
EXIT_STALE = 5
"""A telemetry entity stayed stale for ``exit_after_stale`` (ADR-083)."""


# ---------------------------------------------------------------------------
# Framework-owned MQTT topics
# ---------------------------------------------------------------------------

# Canonical AsyncAPI registry snapshot, published as
# ``{prefix}/{REGISTRY_TOPIC_SUFFIX}`` (ADR-012). The name is load-bearing for
# broker ACL rules and existing subscribers.
REGISTRY_TOPIC_SUFFIX = "_meta/registry"

# ADR-069: retained, machine-readable ``state_model`` declaration-drift
# snapshot, published as ``{prefix}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}``.
STATE_MODEL_DRIFT_TOPIC_SUFFIX = "_meta/state_model_drift"

AVAILABILITY_PAYLOADS = ("online", "offline")
"""The two retained payloads an availability topic carries (ADR-012)."""


def availability_topic(prefix: str, name: str, *, is_root: bool) -> str:
    """Return the retained availability topic entity *name* publishes on.

    A root entity (ADR-058) uses the app-wide ``{prefix}/availability`` — flat,
    even when a Router prefix put a ``/`` into its name — and every other
    entity ``{prefix}/{name}/availability``.  The health reporter, the AsyncAPI
    generator and the consumer generators all resolve through here, so the
    topic the runtime publishes and the one the schema documents cannot drift.
    """
    if is_root:
        return f"{prefix}/availability"
    return f"{prefix}/{name}/availability"
