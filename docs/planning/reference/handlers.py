"""MQTT message → command translation and error routing.

The :class:`CommandHandler` bridges MQTT messages and the
:class:`~velux2mqtt.application.service.ActuatorService`:

1. Extracts the target actuator name from the MQTT topic
2. Parses the payload into a domain :data:`Command`
3. Dispatches the command to the service
4. Routes any domain errors to the :class:`ErrorPublisher`

**Topic convention** (relative to ``topic_prefix``)::

    {prefix}/{actuator}/set    → command topic (subscribed by adapter)
    {prefix}/{actuator}/actual → state topic (published by service)

Only ``/set`` topics trigger command dispatch — all other topics
are silently ignored.

**Error handling strategy:**  All :class:`DomainError` variants are
caught and routed to :class:`ErrorPublisher` for structured MQTT
error reporting.  The handler never crashes from a malformed command
or unknown actuator — those are operational events, not fatal errors.
"""

from __future__ import annotations

import logging

from velux2mqtt.application.errors import ErrorPublisher
from velux2mqtt.application.service import ActuatorService
from velux2mqtt.domain.commands import parse_command
from velux2mqtt.domain.errors import DomainError

logger = logging.getLogger(__name__)


class CommandHandler:
    """Routes MQTT messages to the actuator service.

    Designed to be registered as the ``on_message`` callback with the
    MQTT adapter.  Catches all :class:`DomainError` variants and routes
    them to the :class:`ErrorPublisher` for structured MQTT error
    reporting.

    Args:
        service: The actuator orchestration service.
        error_publisher: Structured error publication service.
        topic_prefix: MQTT topic root (e.g. ``"velux2mqtt"``).
    """

    def __init__(
        self,
        *,
        service: ActuatorService,
        error_publisher: ErrorPublisher,
        topic_prefix: str,
    ) -> None:
        self._service = service
        self._error_publisher = error_publisher
        self._topic_prefix = topic_prefix

    async def on_message(self, topic: str, payload: str) -> None:
        """Handle an incoming MQTT message.

        Extracts the actuator name from the topic, parses the command
        payload, and dispatches to the service.  Domain errors are
        caught and published to the error channel.

        Non-command topics (e.g. ``/actual``, ``/error``) are silently
        ignored.

        Args:
            topic: Full MQTT topic path.
            payload: UTF-8 decoded message payload.
        """
        actuator_name = self._extract_actuator_name(topic)
        if actuator_name is None:
            return

        logger.info("Command received: %s → %r", actuator_name, payload)

        try:
            command = parse_command(payload, actuator_name=actuator_name)
            await self._service.handle_command(actuator_name, command)
        except DomainError as error:
            await self._error_publisher.publish(error)

    def _extract_actuator_name(self, topic: str) -> str | None:
        """Extract actuator name from ``{prefix}/{name}/set`` topic.

        Returns ``None`` for topics that don't match the command
        pattern, including:

        - Non-``/set`` suffixes (e.g. ``/actual``, ``/error``)
        - Nested paths (e.g. ``prefix/a/b/set``)
        - Empty actuator names
        """
        prefix = self._topic_prefix + "/"
        suffix = "/set"

        if not (topic.startswith(prefix) and topic.endswith(suffix)):
            return None

        # Extract the middle segment between prefix and /set
        middle = topic[len(prefix) : -len(suffix)]

        # Must be a single non-empty segment (no nested slashes)
        if "/" in middle or not middle:
            return None

        return middle
