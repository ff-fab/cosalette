"""Read-only collection property mixin for App and Router.

Both :class:`~cosalette.App` and :class:`~cosalette.Router` expose the
same five read-only introspection properties.  This mixin centralises
the definitions so they stay in sync.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._wiring._adapter_lifecycle import _AdapterEntry


class _RegistrationViewsMixin:
    """Read-only collection views shared by App and Router.

    Subclasses must initialise the five backing attributes in ``__init__``:
    ``_devices``, ``_telemetry``, ``_commands``, ``_periodic``, ``_adapters``.
    """

    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _commands: list[_CommandRegistration]
    _periodic: list[_PeriodicRegistration]
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
    def adapters(self) -> Mapping[type, _AdapterEntry]:
        """Registered adapter entries keyed by port type (live read-only view).

        Returns a :class:`~types.MappingProxyType` wrapping the live adapter
        registry.  Unlike the four tuple-based properties, this is a **live
        view** — adapters registered after obtaining the proxy remain visible
        through it.
        """
        return MappingProxyType(self._adapters)
