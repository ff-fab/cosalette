"""Read-only collection property mixin for App and Router.

Both :class:`~cosalette.App` and :class:`~cosalette.Router` expose the
same six read-only introspection properties.  This mixin centralises
the definitions so they stay in sync.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from cosalette._registration import (
    StreamRegistration,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._wiring._adapter_lifecycle import _AdapterEntry


class _RegistrationViewsMixin:
    """Read-only collection views shared by App and Router.

    Subclasses must initialise the six backing attributes in ``__init__``:
    ``_devices``, ``_telemetry``, ``_commands``, ``_periodic``, ``_adapters``,
    ``_streams``.
    """

    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _commands: list[_CommandRegistration]
    _periodic: list[_PeriodicRegistration]
    _streams: list[_StreamRegistration]
    _adapters: dict[type, _AdapterEntry]

    @property
    def commands(self) -> Sequence[_CommandRegistration]:
        """Registered command handlers (read-only snapshot)."""
        return tuple(self._commands)

    @property
    def telemetry_registrations(self) -> Sequence[_TelemetryRegistration]:
        """Registered telemetry handlers (read-only snapshot).

        Named ``telemetry_registrations`` rather than ``telemetry`` to
        avoid shadowing the :meth:`telemetry` registration decorator.
        """
        return tuple(self._telemetry)

    @property
    def devices(self) -> Sequence[_DeviceRegistration]:
        """Registered device handlers (read-only snapshot)."""
        return tuple(self._devices)

    @property
    def periodic_registrations(self) -> Sequence[_PeriodicRegistration]:
        """Registered periodic handlers (read-only snapshot).

        Named ``periodic_registrations`` rather than ``periodic`` to
        avoid shadowing the :meth:`periodic` registration decorator.
        """
        return tuple(self._periodic)

    @property
    def stream_registrations(self) -> Sequence[StreamRegistration]:
        """Registered stream handlers (read-only snapshot).

        Named ``stream_registrations`` rather than ``stream`` to avoid
        shadowing the :meth:`stream` registration decorator.
        """
        return tuple(self._streams)

    @property
    def root_names(self) -> frozenset[str]:
        """Names of root-level registrations (``is_root``) across archetypes.

        Root entities occupy the app namespace with no device segment, so
        by the ADR-058 contract they never contribute a name to a schema's
        ``device_names``.  Callers comparing registrations against schema
        device names (e.g. ``cosalette schema check``) must exclude these to
        avoid a spurious ``EXTRA``.  Periodic registrations carry no
        ``is_root`` and have no MQTT/AsyncAPI presence (ADR-041), so they are
        intentionally absent here.
        """
        return frozenset(
            reg.name
            for regs in (self._devices, self._telemetry, self._commands, self._streams)
            for reg in regs
            if reg.is_root
        )

    @property
    def adapters(self) -> Mapping[type, _AdapterEntry]:
        """Registered adapter entries keyed by port type (live read-only view).

        Returns a :class:`~types.MappingProxyType` wrapping the live adapter
        registry.  Unlike the four tuple-based properties, this is a **live
        view** — adapters registered after obtaining the proxy remain visible
        through it.
        """
        return MappingProxyType(self._adapters)
