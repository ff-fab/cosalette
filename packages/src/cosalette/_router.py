"""MQTT command topic routing.

Extracts device names from ``{prefix}/{device}/set`` topics and
dispatches inbound command messages to per-device handlers.

Also supports root-level devices (unnamed) that listen on
``{prefix}/set`` directly.

Topic convention (ADR-002)::

    {prefix}/{device}/set    → command topic (subscribed, routed here)
    {prefix}/set             → root device command topic (when registered)
    {prefix}/{device}/state  → state topic (published, not routed)

The router is an internal component — not part of cosalette's public API.
Users register command handlers via ``DeviceContext.on_command()``;
the framework wires them into the router automatically.

See Also:
    ADR-002 — MQTT topic conventions.
"""

from __future__ import annotations

import logging

from cosalette._mqtt import MessageCallback

logger = logging.getLogger(__name__)


class TopicRouter:
    """Routes MQTT command messages to per-device handlers.

    Parses ``{prefix}/{device}/set`` topics, extracts device names,
    and dispatches to registered handlers.  Also supports a single
    root handler for the ``{prefix}/set`` topic (unnamed devices).

    Topic convention (ADR-002)::

        {prefix}/{device}/set    → command topic (subscribed, routed here)
        {prefix}/set             → root device command topic
        {prefix}/{device}/state  → state topic (published, not routed)

    See Also:
        ADR-002 — MQTT topic conventions.
    """

    def __init__(self, *, topic_prefix: str) -> None:
        self._topic_prefix = topic_prefix
        self._handlers: dict[str, MessageCallback] = {}
        self._root_handler: MessageCallback | None = None

    def register(
        self,
        device_name: str,
        handler: MessageCallback,
        *,
        is_root: bool = False,
    ) -> None:
        """Register a command handler for a device.

        When *is_root* is True, registers the handler for the
        ``{prefix}/set`` topic instead of ``{prefix}/{device}/set``.

        Raises:
            ValueError: If a handler is already registered for *device_name*
                or if a root handler is already registered.
        """
        if is_root:
            if self._root_handler is not None:
                msg = "Root handler already registered"
                raise ValueError(msg)
            self._root_handler = handler
        else:
            if device_name in self._handlers:
                msg = f"Handler already registered for device '{device_name}'"
                raise ValueError(msg)
            self._handlers[device_name] = handler

    async def route(self, topic: str, payload: str) -> None:
        """Route an inbound MQTT message to the appropriate device handler.

        Checks for root device match (``{prefix}/set``) first, then
        falls back to extracting a device name from
        ``{prefix}/{device}/set``.

        Silently ignores:
        - Topics that don't match either pattern
        - Devices with no registered handler (logs WARNING)
        """
        # Check for root device match: {prefix}/set
        if topic == f"{self._topic_prefix}/set":
            if self._root_handler is not None:
                await self._root_handler(topic, payload)
            else:
                logger.warning("No root handler registered (topic: %s)", topic)
            return

        device = self._extract_device(topic)
        if device is None:
            return

        handler = self._handlers.get(device)
        if handler is None:
            logger.warning(
                "No handler registered for device '%s' (topic: %s)",
                device,
                topic,
            )
            return

        await handler(topic, payload)

    def _extract_device(self, topic: str) -> str | None:
        """Extract device name from topic.

        Returns:
            The device name if *topic* matches ``{prefix}/{device}/set``,
            otherwise ``None``.
        """
        prefix = self._topic_prefix + "/"
        suffix = "/set"
        if not (topic.startswith(prefix) and topic.endswith(suffix)):
            return None
        middle = topic[len(prefix) : -len(suffix)]
        if "/" in middle or not middle:
            return None
        return middle

    @property
    def subscriptions(self) -> list[str]:
        """Return topics that should be subscribed to for all registered devices."""
        subs = [f"{self._topic_prefix}/{device}/set" for device in self._handlers]
        if self._root_handler is not None:
            subs.append(f"{self._topic_prefix}/set")
        return subs
