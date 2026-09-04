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


# ---------------------------------------------------------------------------
# Framework-owned MQTT topics
# ---------------------------------------------------------------------------

# ADR-069: retained, machine-readable ``state_model`` declaration-drift
# snapshot, published as ``{prefix}/{STATE_MODEL_DRIFT_TOPIC_SUFFIX}``.
STATE_MODEL_DRIFT_TOPIC_SUFFIX = "_meta/state_model_drift"
