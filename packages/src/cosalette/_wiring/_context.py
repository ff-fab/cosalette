"""Device contexts and routing: DeviceInfo, contexts, router, and triggering."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, NamedTuple

from cosalette._clock import ClockPort
from cosalette._command_runner import CommandRunner
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._injection import KNOWN_INJECTABLE_TYPES
from cosalette._mqtt import MqttMessageHandler, MqttPort
from cosalette._mqtt._router import TopicRouter
from cosalette._persistence._stores import Store
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._telemetry_runner import _TriggerSlot
from cosalette._settings import Settings

if TYPE_CHECKING:
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger("cosalette._wiring")


class DeviceInfo(NamedTuple):
    """Device name paired with its root status for availability routing."""

    name: str
    is_root: bool


def build_adapter_device_map(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    resolved_adapters: dict[type, object],
) -> dict[type, list[DeviceInfo]]:
    """Map each adapter port type to the devices that depend on it.

    Scans each registration's ``injection_plan`` to find adapter port
    types (types present in *resolved_adapters* but not in
    ``KNOWN_INJECTABLE_TYPES``).  Returns a mapping from adapter port
    type to a list of ``DeviceInfo(name, is_root)`` tuples.

    A device name appears at most once per adapter type, even when
    telemetry and command registrations share a name (scoped uniqueness).
    """
    adapter_types = set(resolved_adapters) - set(KNOWN_INJECTABLE_TYPES)
    result: dict[type, list[DeviceInfo]] = {t: [] for t in adapter_types}
    seen: dict[type, set[str]] = {t: set() for t in adapter_types}

    for reg in all_registrations:
        for _, param_type in reg.injection_plan:
            if param_type in adapter_types and reg.name not in seen[param_type]:
                seen[param_type].add(reg.name)
                result[param_type].append(DeviceInfo(reg.name, reg.is_root))

    return result


def build_contexts(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    settings: Settings,
    mqtt: MqttPort,
    prefix: str,
    shutdown_event: asyncio.Event,
    adapters: dict[type, object],
    clock: ClockPort,
) -> dict[str, DeviceContext]:
    """Build a DeviceContext for every registered device.

    When a telemetry and command registration share the same name
    (scoped name uniqueness), only one :class:`DeviceContext` is
    created for that name — they share a single context.
    """
    contexts: dict[str, DeviceContext] = {}
    for reg in all_registrations:
        if reg.name not in contexts:
            contexts[reg.name] = DeviceContext(
                name=reg.name,
                settings=settings,
                mqtt=mqtt,
                topic_prefix=prefix,
                shutdown_event=shutdown_event,
                adapters=adapters,
                clock=clock,
                is_root=reg.is_root,
            )
    return contexts


def build_stream_contexts(
    streams: list[_StreamRegistration],
    settings: Settings,
    mqtt: MqttPort,
    prefix: str,
    shutdown_event: asyncio.Event,
    adapters: dict[type, object],
    clock: ClockPort,
) -> dict[str, DeviceContext]:
    """Build a stream-scoped DeviceContext for every registered stream handler.

    Each stream gets its own context keyed by stream name, enabling
    stream handlers to publish via MQTT using their stream name as the
    device segment in topics.
    """
    contexts: dict[str, DeviceContext] = {}
    for reg in streams:
        if reg.name not in contexts:
            contexts[reg.name] = DeviceContext(
                name=reg.name,
                settings=settings,
                mqtt=mqtt,
                topic_prefix=prefix,
                shutdown_event=shutdown_event,
                adapters=adapters,
                clock=clock,
                is_root=reg.is_root,
            )
    return contexts


def _partition_commands(
    commands: list[_CommandRegistration],
) -> tuple[list[_CommandRegistration], dict[str, list[_CommandRegistration]]]:
    regular: list[_CommandRegistration] = []
    by_name: dict[str, list[_CommandRegistration]] = {}
    for r in commands:
        if r.sub is None:
            regular.append(r)
        else:
            by_name.setdefault(r.name, []).append(r)
    return regular, by_name


@dataclasses.dataclass(frozen=True)
class TriggerConfig:
    """Bundled triggerable-telemetry state passed to :func:`wire_router`.

    Groups the two pieces of per-run trigger state that are created together
    and consumed together: the event slots used by MQTT proxies to arm
    triggers, and the telemetry registrations that describe which devices are
    triggerable.

    Use :meth:`build` to construct from a list of telemetry registrations
    rather than constructing directly.

    .. note::
        ``frozen=True`` prevents attribute *reassignment* on this object, but
        the ``slots`` dict and ``telemetry`` list are themselves mutable.  Do
        not mutate them after construction.

    Attributes:
        slots: Mapping of device name → :class:`_TriggerSlot` for every
            ``triggerable=True`` registration in *telemetry*.
        telemetry: Snapshot of the telemetry registrations at build time
            (both triggerable and non-triggerable).
    """

    slots: dict[str, _TriggerSlot]
    telemetry: list[_TelemetryRegistration]

    @classmethod
    def build(cls, telemetry: list[_TelemetryRegistration]) -> TriggerConfig:
        """Build a :class:`TriggerConfig` from *telemetry* registrations.

        Takes a snapshot of *telemetry* (shallow copy) and creates one
        :class:`_TriggerSlot` (with a fresh ``asyncio.Event``) for every
        entry whose ``triggerable`` flag is ``True``.
        """
        snapshot = list(telemetry)
        slots: dict[str, _TriggerSlot] = {
            reg.name: _TriggerSlot(event=asyncio.Event())
            for reg in snapshot
            if reg.triggerable
        }
        return cls(slots=slots, telemetry=snapshot)


def _register_triggerable_telemetry(
    trigger_slots: dict[str, _TriggerSlot],
    telemetry: list[_TelemetryRegistration],
    prefix: str,
    router: TopicRouter,
) -> None:
    for tel_reg in telemetry:
        if tel_reg.triggerable and tel_reg.name in trigger_slots:
            _register_trigger_proxy(
                tel_reg, trigger_slots[tel_reg.name], prefix, router
            )


async def wire_router(
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
    store: Store | None,
    contexts: dict[str, DeviceContext],
    prefix: str,
    error_publisher: ErrorPublisher,
    trigger_config: TriggerConfig | None = None,
    reactors: list[_ReactorRegistration] | None = None,
) -> TopicRouter:
    """Create a :class:`~cosalette._mqtt._router.TopicRouter` and register proxies.

    Registers command-handler proxies for all *devices* and *commands*, and
    — when *trigger_config* is supplied — the MQTT trigger proxies for every
    ``triggerable=True`` telemetry device.

    Args:
        devices: Device registrations whose command topics need proxies.
        commands: Command registrations to wire up.
        store: Optional persistence store passed to :class:`CommandRunner`.
        contexts: Per-device execution contexts keyed by device name.
        prefix: MQTT topic prefix for all subscriptions.
        error_publisher: Used to publish framework errors from proxies.
        trigger_config: Bundled trigger state.  When provided, the
            MQTT ``{prefix}/{device}/set`` topics are subscribed and proxied
            to arm the corresponding :class:`_TriggerSlot` event.  Build
            with :meth:`TriggerConfig.build`.
        reactors: Optional list of reactor registrations to dispatch after
            successful command execution.
    """
    cmd_runner = CommandRunner(store=store)
    router = TopicRouter(topic_prefix=prefix)
    for reg in devices:
        CommandRunner.register_device_proxy(
            reg, contexts[reg.name], error_publisher, router
        )

    regular_commands, sub_commands_by_name = _partition_commands(commands)
    for cmd_reg in regular_commands:
        await cmd_runner.register_command_proxy(
            cmd_reg, contexts[cmd_reg.name], error_publisher, router, reactors
        )
    for name, group in sub_commands_by_name.items():
        await cmd_runner.register_sub_command_proxy(
            group, contexts[name], error_publisher, router, reactors
        )

    if trigger_config and trigger_config.slots:
        _register_triggerable_telemetry(
            trigger_config.slots, trigger_config.telemetry, prefix, router
        )

    return router


async def subscribe_and_connect(
    mqtt: MqttPort,
    router: TopicRouter,
) -> None:
    """Subscribe to command topics and wire message handler."""
    for topic in router.subscriptions:
        await mqtt.subscribe(topic)
    if isinstance(mqtt, MqttMessageHandler):
        mqtt.on_message(router.route)


def _register_trigger_proxy(
    reg: _TelemetryRegistration,
    slot: _TriggerSlot,
    prefix: str,
    router: TopicRouter,
) -> None:
    """Register a trigger command proxy for a triggerable telemetry device."""
    expected_topic = f"{prefix}/{reg.name}/set"

    async def _trigger_proxy(
        topic: str,
        payload: str,
        _slot: _TriggerSlot = slot,
        _name: str = reg.name,
        _expected: str = expected_topic,
    ) -> None:
        # Reject sub-topic triggers — only the exact device /set topic is valid.
        if topic != _expected:
            logger.debug(
                "Trigger ignored for '%s': unexpected topic '%s' (expected '%s')",
                _name,
                topic,
                _expected,
            )
            return
        if _slot.event.is_set():
            logger.debug("Trigger coalesced for '%s', run already pending", _name)
        else:
            logger.debug("Trigger received for '%s', scheduling immediate run", _name)
        _slot.arm(payload)  # raw string stored; JSON parsed lazily in consume()

    router.register(reg.name, _trigger_proxy, is_root=reg.is_root)
