"""Port protocol definitions for velux2mqtt.

These ``Protocol`` classes (PEP 544) define the contracts between the
application/domain layers and infrastructure adapters.  Adapters implement
ports via *structural subtyping* — no base-class inheritance required.

**Why Protocols over ABCs:**  Structural subtyping means adapters satisfy
the contract by shape alone.  This keeps the domain free of framework
imports and aligns with Go-style interface satisfaction.

**Layer dependency rule:**  Ports may reference domain types but must
never import from application or infrastructure layers.

See Also:
    ADR-001 for the full architectural rationale and data-flow diagrams.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GpioPort(Protocol):
    """Hardware abstraction for GPIO actuator control.

    Implementations pulse a GPIO pin HIGH for a specified duration, then
    return it to LOW.  The pulse is the physical equivalent of pressing
    a button on a Velux remote control.

    Concrete implementations:

    - **RpiGpioAdapter** — real ``RPi.GPIO`` on Raspberry Pi hardware
    - **DryRunGpioAdapter** — operator-visible dry-run mode (no hardware)

    Test doubles (``MockGpioAdapter``) live in ``tests/fixtures/gpio.py``.

    All implementations must be safe to call concurrently from an
    ``asyncio`` event loop (i.e. they must not block).
    """

    async def pulse(self, pin: int, duration: float) -> None:
        """Pulse a GPIO pin HIGH for *duration* seconds, then LOW.

        Args:
            pin: GPIO pin number (in the configured numbering mode).
            duration: How long to hold the pin HIGH, in seconds.
                Must be positive; typically 0.3–0.5 s for Velux remotes.

        Raises:
            RuntimeError: If the GPIO subsystem is unavailable or pin
                is not configured for output.
        """
        ...

    async def cleanup(self) -> None:
        """Release GPIO resources.

        Called during graceful shutdown.  Implementations should set all
        managed pins to LOW and release the GPIO subsystem.  Must be
        idempotent — safe to call multiple times.
        """
        ...


@runtime_checkable
class MqttPort(Protocol):
    """MQTT client abstraction for publish/subscribe.

    Implementations wrap an MQTT client library (e.g. ``aiomqtt``) and
    expose only the operations needed by the application layer.

    Lifecycle (connect/disconnect) is managed by the composition root,
    not by the consumers of this port.
    """

    async def publish(
        self,
        topic: str,
        payload: str,
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        """Publish a message to an MQTT topic.

        Args:
            topic: Full MQTT topic path (e.g. ``velux2mqtt/blind/state``).
            payload: Message payload as a UTF-8 string.
            retain: Whether the broker should retain this message.
            qos: MQTT Quality of Service level (0, 1, or 2).
        """
        ...

    async def subscribe(self, topic: str) -> None:
        """Subscribe to an MQTT topic pattern.

        Args:
            topic: MQTT topic or topic filter (may contain wildcards
                ``+`` and ``#``).
        """
        ...


@runtime_checkable
class ClockPort(Protocol):
    """Monotonic clock for timing measurements.

    Used by the position estimator and travel-time calculator to
    measure elapsed time without being affected by system clock
    adjustments (NTP, manual changes, etc.).

    The default implementation wraps ``time.monotonic()``.  Tests
    inject a deterministic fake clock for reproducible timing.
    """

    def now(self) -> float:
        """Return monotonic time in seconds.

        Returns:
            A float representing seconds from an arbitrary epoch.
            Only the *difference* between two calls is meaningful.
        """
        ...
