"""MQTT command topic routing.

Extracts device names from ``{prefix}/{device}/set`` topics and
dispatches inbound command messages to per-device handlers.

Topic convention (ADR-002)::

    {prefix}/{device}/set    → command topic (subscribed, routed here)
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
    and dispatches to registered handlers.

    Topic convention (ADR-002)::

        {prefix}/{device}/set    → command topic (subscribed, routed here)
        {prefix}/{device}/state  → state topic (published, not routed)

    See Also:
        ADR-002 — MQTT topic conventions.
    """

    def __init__(self, *, topic_prefix: str) -> None:
        self._topic_prefix = topic_prefix
        self._handlers: dict[str, MessageCallback] = {}

    def register(self, device_name: str, handler: MessageCallback) -> None:
        """Register a command handler for a device.

        Raises:
            ValueError: If a handler is already registered for *device_name*.
        """
        if device_name in self._handlers:
            msg = f"Handler already registered for device '{device_name}'"
            raise ValueError(msg)
        self._handlers[device_name] = handler

    async def route(self, topic: str, payload: str) -> None:
        """Route an inbound MQTT message to the appropriate device handler.

        Silently ignores:
        - Topics that don't match ``{prefix}/{device}/set``
        - Devices with no registered handler (logs WARNING)
        """
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
        return [f"{self._topic_prefix}/{device}/set" for device in self._handlers]
