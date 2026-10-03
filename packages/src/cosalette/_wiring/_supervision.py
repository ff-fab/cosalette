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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cosalette._health import HealthCheckRunner
    from cosalette._registration import (
        _DeviceRegistration,
        _StreamRegistration,
        _TelemetryRegistration,
    )
    from cosalette._supervisor import TaskSupervisor
    from cosalette._wiring._context import DeviceInfo

    type _EntityRegistration = (
        _DeviceRegistration | _TelemetryRegistration | _StreamRegistration
    )


def entity_task_members(
    task_name: str,
    devices: Sequence[_DeviceRegistration],
    telemetry: Sequence[_TelemetryRegistration],
    streams: Sequence[_StreamRegistration] = (),
) -> list[_EntityRegistration]:
    """Return the registrations an entity task (device, telemetry, group,
    stream) runs."""
    kind, _, ident = task_name.partition(":")
    if kind == "group":
        return [reg for reg in telemetry if reg.group == ident]
    candidates: dict[str, Sequence[_EntityRegistration]] = {
        "device": devices,
        "telemetry": [reg for reg in telemetry if reg.group is None],
        "stream": streams,
    }
    return [reg for reg in candidates.get(kind, ()) if reg.name == ident]


def supervise_entity_tasks(
    supervisor: TaskSupervisor,
    tasks: Iterable[asyncio.Task[None]],
    devices: Sequence[_DeviceRegistration],
    telemetry: Sequence[_TelemetryRegistration],
    restart_entities: Callable[[list[str]], asyncio.Task[None]],
    streams: Sequence[_StreamRegistration] = (),
) -> None:
    """Supervise device, telemetry, group and stream tasks.

    *restart_entities* re-creates the one task serving the given entity
    names; a coalescing group is always re-created whole.  A named stream
    goes offline under the supervisor source; a root stream is
    heartbeat-only and its error payload goes to ``{prefix}/error``.  Both
    clear at the first item after a restart.
    """
    for task in tasks:
        if task.done():
            continue
        members = entity_task_members(task.get_name(), devices, telemetry, streams)
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
