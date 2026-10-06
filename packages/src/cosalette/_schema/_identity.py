"""Instance identity validation shared by CLI and consumer generators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cosalette._settings._identity import validate_instance_id

if TYPE_CHECKING:
    from cosalette._schema import SchemaRegistry


def instance_identity_token(instance_id: str | None, fallback: str) -> str:
    """Frame explicit IDs so app/device concatenations have an unambiguous boundary.

    ``a`` with device ``b_sensor`` must differ from ``a_b`` with ``sensor``.
    Length counts canonical ASCII characters; absent IDs keep legacy spelling.
    """
    return f"i{len(instance_id)}_{instance_id}" if instance_id else fallback


def check_instance_id_scope(registry: SchemaRegistry, instance_id: str | None) -> None:
    """Require a canonical identity naming a single app instance (ADR-089)."""
    if not instance_id:
        return
    validate_instance_id(instance_id)
    apps = registry.all_app_names()
    if len(apps) > 1:
        msg = (
            f"instance id {instance_id!r} names a single app instance, but the "
            f"schema describes {len(apps)} apps ({', '.join(sorted(apps))})"
        )
        raise ValueError(msg)
