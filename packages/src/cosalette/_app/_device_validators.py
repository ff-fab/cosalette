"""Validation helpers for ``@app.device`` trigger registration.

Devices participate in the ADR-064 trigger mechanism through the
*local* arming path only.  ``{prefix}/{name}/set`` is already the
device's inbound **command** topic, so an MQTT trigger source would
collide with the command router — see ADR-065.

See Also:
    ADR-065 — Local trigger source for the device archetype.
    ADR-064 — Local (in-process) trigger source for triggerable telemetry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cosalette._runners._device_trigger import DeviceTrigger
from cosalette._runners._trigger import (
    TriggerableSpec,
    TriggerSource,
    normalize_trigger_source,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def plan_declares_device_trigger(plan: Sequence[tuple[str, type]]) -> bool:
    """Return ``True`` when *plan* declares a :class:`DeviceTrigger` parameter."""
    return any(annotation is DeviceTrigger for _, annotation in plan)


def validate_device_triggerable(
    triggerable: TriggerableSpec,
    name: str,
    plan: Sequence[tuple[str, type]],
) -> TriggerSource | None:
    """Validate ``@app.device(triggerable=...)`` and return the source.

    Unlike telemetry, a device accepts ``"local"`` only: the framework
    owns no publish cycle for a device handler, and the MQTT arming
    topic a device would need is already its command topic (ADR-065).

    The ``triggerable=`` flag and the :class:`DeviceTrigger` parameter
    must agree.  Declaring one without the other is always a mistake —
    a device that opts in but never awaits the handle would never see
    a wake, and a handle with no opt-in has no slot to wait on — so
    both directions are rejected loudly at registration time rather
    than becoming a silent no-op.

    Args:
        triggerable: The raw ``triggerable=`` argument.
        name: Resolved device name, used in error messages.
        plan: The handler's injection plan.

    Returns:
        ``"local"`` when the device opted in, otherwise ``None``.

    Raises:
        ValueError: For an unknown source name, for any source other
            than ``"local"``, or when ``triggerable=`` and the
            :class:`DeviceTrigger` parameter disagree.
    """
    source = normalize_trigger_source(triggerable)
    wants_handle = plan_declares_device_trigger(plan)
    if source is not None and source != "local":
        msg = (
            f"triggerable={triggerable!r} is not supported on device {name!r}: "
            f"devices accept triggerable='local' only.  "
            f"{{prefix}}/{name}/set is already this device's command topic, "
            f"so it cannot double as a trigger topic — handle the command "
            f"with ctx.on_command() instead (ADR-065)."
        )
        raise ValueError(msg)
    if source is None and wants_handle:
        msg = (
            f"Device {name!r} declares a DeviceTrigger parameter but is not "
            f"triggerable.  Add triggerable='local' to @app.device(), or drop "
            f"the parameter."
        )
        raise ValueError(msg)
    if source is not None and not wants_handle:
        msg = (
            f"Device {name!r} sets triggerable={source!r} but its handler "
            f"declares no DeviceTrigger parameter, so it could never observe "
            f"a wake.  Add a 'trigger: DeviceTrigger' parameter, or drop "
            f"triggerable=."
        )
        raise ValueError(msg)
    return source
