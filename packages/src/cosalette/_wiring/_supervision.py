"""Wiring between the task supervisor and the framework's task starters (ADR-081).

Maps each supervised task to the entities and registrations it serves and
builds the restart factories the supervisor calls.  The factories reuse the
starters that create the tasks at startup, so a re-created task gets the
same trigger slots, contexts and reactors as the original.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from cosalette._health import HealthCheckRunner
    from cosalette._registration import _DeviceRegistration, _TelemetryRegistration
    from cosalette._supervisor import TaskSupervisor
    from cosalette._wiring._context import DeviceInfo


def entity_task_members(
    task_name: str,
    devices: Sequence[_DeviceRegistration],
    telemetry: Sequence[_TelemetryRegistration],
) -> list[_DeviceRegistration | _TelemetryRegistration]:
    """Return the registrations an entity task (device, telemetry, group) runs."""
    kind, _, ident = task_name.partition(":")
    selectors = {
        "device": lambda: [device for device in devices if device.name == ident],
        "telemetry": lambda: [
            registration
            for registration in telemetry
            if registration.name == ident and registration.group is None
        ],
        "group": lambda: [
            registration for registration in telemetry if registration.group == ident
        ],
    }
    selector = selectors.get(kind)
    return cast(
        "list[_DeviceRegistration | _TelemetryRegistration]",
        selector() if selector is not None else [],
    )


def supervise_entity_tasks(
    supervisor: TaskSupervisor,
    tasks: Iterable[asyncio.Task[None]],
    devices: Sequence[_DeviceRegistration],
    telemetry: Sequence[_TelemetryRegistration],
    restart_entities: Callable[[list[str]], asyncio.Task[None]],
) -> None:
    """Supervise device, telemetry and group tasks.

    *restart_entities* re-creates the one task serving the given entity
    names; a coalescing group is always re-created whole.
    """
    for task in tasks:
        if task.done():
            continue
        members = entity_task_members(task.get_name(), devices, telemetry)
        names = [reg.name for reg in members]
        supervisor.supervise(
            task,
            entities=[(reg.name, reg.is_root) for reg in members],
            registrations=members,
            restart=functools.partial(restart_entities, names),
        )


def adapter_exhausted_check(
    health_check_runner: HealthCheckRunner | None,
    adapter_device_map: Mapping[type, Sequence[DeviceInfo]] | None,
) -> Callable[[str], bool] | None:
    """Return a predicate: is *entity*'s adapter restart-exhausted (ADR-029)?

    ``None`` when adapter health checks are not wired.
    """
    if health_check_runner is None or not adapter_device_map:
        return None
    runner = health_check_runner
    device_map = adapter_device_map

    def _exhausted(entity: str) -> bool:
        for adapter_type, infos in device_map.items():
            status = runner.adapter_health_status.get(adapter_type)
            if status is None or not status.restart_exhausted:
                continue
            if any(info.name == entity for info in infos):
                return True
        return False

    return _exhausted


def prune_done(tasks: list[asyncio.Task[None]]) -> None:
    """Drop finished tasks from *tasks* in place."""
    tasks[:] = [t for t in tasks if not t.done()]
