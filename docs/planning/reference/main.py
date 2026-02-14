"""Composition root — application wiring and lifecycle.

This module is the only place where all layers are imported together.
It wires infrastructure adapters to port protocols, injects them into
application services, and manages the async event loop lifecycle.

Responsibilities:

1. Load and validate configuration (pydantic-settings)
2. Configure logging (stderr, container-friendly)
3. Instantiate infrastructure adapters (MQTT, GPIO, Clock)
4. Construct application services with injected adapters
5. Start the ``asyncio`` event loop
6. Run startup homing to establish known actuator positions
7. Subscribe to MQTT command topics and dispatch incoming messages
8. Handle graceful shutdown (SIGTERM / SIGINT)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import NoReturn

from velux2mqtt import __version__
from velux2mqtt.application.errors import ErrorPublisher
from velux2mqtt.application.handlers import CommandHandler
from velux2mqtt.application.service import ActuatorService
from velux2mqtt.config import Settings, get_settings
from velux2mqtt.infrastructure.clock import SystemClock
from velux2mqtt.infrastructure.gpio_adapter import DryRunGpioAdapter, RpiGpioAdapter
from velux2mqtt.infrastructure.mqtt_client import MqttClientAdapter
from velux2mqtt.ports.protocols import GpioPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def configure_logging(settings: Settings) -> None:
    """Configure root logger from application settings."""
    root = logging.getLogger()
    root.setLevel(settings.logging.level)

    # Avoid duplicate handlers on repeated calls (e.g. tests)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(settings.logging.level)

        if settings.logging.format == "json":
            from velux2mqtt.infrastructure.log_format import JsonFormatter

            formatter: logging.Formatter = JsonFormatter(
                service="velux2mqtt", version=__version__
            )
        else:
            formatter = logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )

        handler.setFormatter(formatter)
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# Adapter factories
# ---------------------------------------------------------------------------


def create_gpio_adapter(settings: Settings) -> GpioPort:
    """Create GPIO adapter based on the configured backend."""
    if settings.gpio_backend == "dry-run":
        logger.info("GPIO backend: dry-run (no hardware will be driven)")
        return DryRunGpioAdapter()

    # "rpi" — only supported hardware at the moment, add new backends here as needed
    logger.debug("GPIO backend: RPi.GPIO")
    return RpiGpioAdapter(gpio_mode=settings.gpio_mode)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


async def run(
    settings: Settings,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Wire all components and run the application lifecycle.

    This is the async composition root.  It creates adapters, wires
    services, runs startup homing, and blocks until a shutdown signal
    is received.

    Args:
        settings: Validated application settings.
        shutdown_event: Optional pre-created event for shutdown
            signalling.  When ``None`` (production), SIGTERM/SIGINT
            handlers are installed automatically.  Pass an event in
            tests to control shutdown without real signals.
    """
    # --- Infrastructure adapters ---
    gpio = create_gpio_adapter(settings)
    mqtt = MqttClientAdapter(settings=settings.mqtt)
    clock = SystemClock()

    # --- Application services ---
    service = ActuatorService(
        gpio=gpio,
        mqtt=mqtt,
        clock=clock,
        topic_prefix=settings.mqtt.topic_prefix,
        actuators=settings.actuators,
    )
    error_publisher = ErrorPublisher(
        mqtt=mqtt,
        topic_prefix=settings.mqtt.topic_prefix,
    )
    handler = CommandHandler(
        service=service,
        error_publisher=error_publisher,
        topic_prefix=settings.mqtt.topic_prefix,
    )

    # --- MQTT wiring ---
    # Register callback before start — messages dispatched to handler
    mqtt.on_message(handler.on_message)

    # Track subscription — restored automatically on (re)connect
    command_topic = f"{settings.mqtt.topic_prefix}/+/set"
    await mqtt.subscribe(command_topic)

    # --- Shutdown signal ---
    if shutdown_event is None:
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)

    # --- Start ---
    logger.info(
        "Starting velux2mqtt v%s with %d actuator(s)",
        __version__,
        len(settings.actuators),
    )
    await mqtt.start()

    try:
        # Startup homing — establish known positions before accepting
        # commands.  MQTT may not be connected yet; position publishes
        # during homing may fail — that's acceptable (positions are
        # tracked locally, MQTT state is best-effort).
        await service.home_all()

        logger.info(
            "velux2mqtt ready — listening for commands on %s",
            command_topic,
        )

        # Block until shutdown signal
        await shutdown_event.wait()

    finally:
        logger.info("Shutting down velux2mqtt...")
        await service.shutdown()
        await mqtt.stop()
        await gpio.cleanup()
        logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> NoReturn | None:
    """Application entry point — load config and run the event loop.

    Exit codes:

    - ``0`` — clean shutdown (SIGTERM / SIGINT)
    - ``1`` — configuration error (invalid env vars, missing required
      settings)
    - ``2`` — unexpected runtime error
    """
    try:
        settings = get_settings()
    except Exception:
        # Minimal logging before proper setup — config failed
        logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
        logger.exception("Failed to load configuration")
        sys.exit(1)

    configure_logging(settings)
    logger.info(
        "Configuration loaded: %d actuator(s), MQTT=%s:%d, GPIO=%s",
        len(settings.actuators),
        settings.mqtt.host,
        settings.mqtt.port,
        settings.gpio_mode,
    )

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        # Clean exit on Ctrl-C (after signal handler has run)
        pass
    except Exception:
        logger.exception("Unexpected error — exiting")
        sys.exit(2)

    return None


if __name__ == "__main__":
    main()
