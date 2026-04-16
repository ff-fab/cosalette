"""Schema enforcement: validation of app registrations against AsyncAPI schema.

Compares an app's device, telemetry, and command registrations to
the channels defined in a SchemaRegistry, producing violations for
missing or misconfigured channels.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cosalette._schema import SchemaRegistry
from cosalette._schema._loader import FileSchemaSource, load_schema
from cosalette._settings import Settings

logger = logging.getLogger(__name__)

# Channels whose address_template ends with these suffixes are auto-wired
# by the framework and should not produce scope violations.
_AUTO_WIRED_SUFFIXES: frozenset[str] = frozenset({"status", "availability"})


@dataclass(frozen=True, slots=True)
class SchemaViolation:
    """A single schema enforcement violation."""

    category: Literal["missing_channel", "missing_capability", "scope_violation"]
    message: str
    channel_name: str | None = None


@dataclass
class SchemaViolationError(Exception):
    """Raised when schema validation fails in strict mode."""

    violations: list[SchemaViolation]

    def __str__(self) -> str:
        header = f"Schema validation failed ({len(self.violations)} violation(s))"
        if len(self.violations) == 1:
            return f"{header}: {self.violations[0].message}"
        bullet_list = "\n".join(f"  - {v.message}" for v in self.violations)
        return f"{header}:\n{bullet_list}"


def _validate_registrations(
    registered_names: frozenset[str],
    registry: SchemaRegistry,
) -> list[SchemaViolation]:
    """Validate app registrations against schema channels.

    Returns a list of violations (empty if all checks pass).
    """
    violations: list[SchemaViolation] = []

    # 1. Check for missing device channels:
    # Schema device_names are extracted from channel address templates.
    # Every device name in the schema should have a matching registration.
    for schema_device in sorted(registry.device_names):
        if schema_device not in registered_names:
            violations.append(
                SchemaViolation(
                    category="missing_channel",
                    message=(
                        f"Schema expects device '{schema_device}' "
                        "but no registration found"
                    ),
                    channel_name=None,
                )
            )

    # 2. Check scope="all_apps" channels:
    # These are app-level channels that don't use {deviceName}.
    # They represent mandatory topics the app must handle.
    # For now, just check they exist - we can't fully validate
    # since framework auto-wires some (like status/availability).
    # We flag channels that have no matching device name segment.
    for name, channel in sorted(registry.channels.items()):
        if (
            channel.scope == "all_apps"
            and "{deviceName}" not in channel.address_template
        ):
            # App-level mandatory channel - check if the app acknowledges it
            # The framework auto-wires status/availability, so skip those.
            # Use the last segment of address_template (not address) so
            # both resolved ("app/status") and templated ("{appName}/status")
            # forms are handled correctly.
            last_segment = channel.address_template.rstrip("/").rsplit("/", 1)[-1]
            if last_segment in _AUTO_WIRED_SUFFIXES:
                continue  # framework auto-wired
            violations.append(
                SchemaViolation(
                    category="scope_violation",
                    message=(
                        f"Mandatory channel '{name}' (scope=all_apps) "
                        "has no registration"
                    ),
                    channel_name=name,
                )
            )

    return violations


async def load_and_validate_schema(
    registered_names: frozenset[str],
    settings: Settings,
    prefix: str,
) -> SchemaRegistry | None:
    """Load schema, filter for app, validate registrations.

    Returns the (possibly filtered) SchemaRegistry, or None when
    enforcement is off or no schema path is configured.

    Raises:
        SchemaViolationError: In strict mode when violations exist.
    """
    if settings.schema_.enforcement == "off":
        return None

    if settings.schema_.path is None:
        return None

    source = FileSchemaSource(Path(settings.schema_.path))
    try:
        registry = await load_schema(source)
    except Exception:
        logger.debug("Schema load failed for %s", settings.schema_.path, exc_info=True)
        msg = "Failed to load schema — check SCHEMA__PATH configuration"
        raise SchemaViolationError(
            [SchemaViolation(category="missing_channel", message=msg)]
        ) from None

    # Network-first: filter to this app's slice
    if registry.enforcement.network_level:
        registry = registry.filter_for_app(prefix)

    violations = _validate_registrations(registered_names, registry)

    if violations and settings.schema_.enforcement == "strict":
        raise SchemaViolationError(violations)
    if violations:
        for v in violations:
            logger.warning("Schema violation: %s", v.message)

    return registry
