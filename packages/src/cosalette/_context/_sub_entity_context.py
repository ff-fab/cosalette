"""SubEntityContext for sub-entity within devices.

Context for a sub-entity within a device. Provides scoped MQTT publishing
for a sub-entity's topic namespace.
"""

from __future__ import annotations

from cosalette._mqtt import CommandHandler
from cosalette._utils import _DEFAULT_COMMAND_TIMEOUT


class SubEntityContext:
    """Context for a sub-entity within a device.

    Provides scoped MQTT publishing for a sub-entity's topic namespace.
    Created via :meth:`DeviceContext.sub_entity` context manager — not
    instantiated directly by user code.

    See Also:
        ADR-031 — Sub-entity context manager.
    """

    __slots__ = ("name", "parent")

    def __init__(self, *, name: str, parent) -> None:
        from cosalette._context._device_context import DeviceContext

        self.name = name
        self.parent: DeviceContext = parent

    async def publish_state(
        self,
        payload: dict[str, object],
        *,
        retain: bool = True,
    ) -> None:
        """Publish sub-entity state to ``{device}/{name}/state`` as JSON.

        Args:
            payload: Dict to serialise as JSON.
            retain: Whether the message should be retained (default True).
        """
        topic = f"{self.parent._topic_base}/{self.name}/state"
        await self.parent._mqtt.publish(topic, payload, retain=retain, qos=1)

    def on_command(
        self,
        handler: CommandHandler,
        *,
        timeout: float | None = _DEFAULT_COMMAND_TIMEOUT,
    ) -> CommandHandler:
        """Register a command handler for this sub-entity's sub-topic.

        Delegates to the parent device's :meth:`~DeviceContext.on_command`
        with this sub-entity's name as the sub-topic.

        Args:
            handler: Async callable to handle inbound commands.
            timeout: Per-invocation watchdog in seconds (ADR-060).
                Defaults to ``_DEFAULT_COMMAND_TIMEOUT`` (30 s); pass
                ``None`` for unbounded execution.

        Returns:
            The handler, unchanged.
        """
        return self.parent.on_command(self.name, timeout=timeout)(handler)
