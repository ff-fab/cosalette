"""Shared preparation of registrations after settings and configure hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cosalette._wiring._resolution import (
    resolve_enabled,
    resolve_intervals,
    resolve_intervals_periodic,
    resolve_stale_after,
    resolve_timeouts,
    resolve_timeouts_commands,
    resolve_timeouts_periodic,
)
from cosalette._wiring._resolution_checks import (
    _check_expanded_duplicates,
    expand_name_specs,
)

if TYPE_CHECKING:
    from cosalette._persistence._stores import Store
    from cosalette._registration import (
        _CommandRegistration,
        _DeviceRegistration,
        _InboundRegistration,
        _StreamRegistration,
        _TelemetryRegistration,
    )
    from cosalette._runners._periodic import _PeriodicRegistration
    from cosalette._settings import Settings


def prepare_registrations(
    telemetry: list[_TelemetryRegistration],
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
    settings: Settings,
    store: Store | None,
    *,
    periodic: list[_PeriodicRegistration],
    streams: list[_StreamRegistration],
    inbounds: list[_InboundRegistration],
) -> None:
    """Expand, resolve deferred timing, filter, and validate registrations.

    Runtime calls this with its resolved store; schema generation calls it
    with ``store=None`` because it performs no persistence I/O. Keep the
    deferred interval/timeout steps between expansion and enabled filtering:
    enabled registrations must expose the same resolved configuration in both
    paths.
    """
    expand_name_specs(telemetry, devices, commands, settings, inbound_list=inbounds)
    resolve_intervals(telemetry, settings)
    resolve_timeouts(telemetry, settings)
    resolve_stale_after(telemetry, settings)
    resolve_intervals_periodic(periodic, settings)
    resolve_timeouts_periodic(periodic, settings)
    resolve_timeouts_commands(commands, settings)
    resolve_enabled(
        telemetry,
        devices,
        commands,
        settings,
        store,
        periodic_list=periodic,
        stream_list=streams,
        inbound_list=inbounds,
    )
    _check_expanded_duplicates(devices, telemetry, commands, inbound_list=inbounds)
