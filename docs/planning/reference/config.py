"""Application configuration via pydantic-settings.

Configuration is loaded from environment variables (prefixed ``V2M_``) and/or
``.env`` files.  Nested models use ``__`` as the delimiter in env var names,
e.g. ``V2M_MQTT__HOST=pi4server.lan``.

The schema mirrors the three infrastructure concerns of velux2mqtt:

* **MQTT** — broker connection and topic layout.
* **Actuators** — per-device GPIO pin mapping and travel timing.
* **Logging** — level, optional file sink, rotation.

All durations are in **seconds**.  Pin numbers follow the selected GPIO
numbering mode (BCM by default — see ``gpio_mode``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Sub-models (not BaseSettings — they participate via nesting)
# ---------------------------------------------------------------------------


class MqttSettings(BaseModel):
    """MQTT broker connection and topic configuration.

    Environment variables (with ``V2M_`` prefix and ``__`` nesting)::

        V2M_MQTT__HOST=pi4server.lan
        V2M_MQTT__PORT=1883
        V2M_MQTT__USERNAME=jl4
        V2M_MQTT__PASSWORD=secret
        V2M_MQTT__TOPIC_PREFIX=velux2mqtt
    """

    host: str = Field(
        default="localhost",
        description="MQTT broker hostname or IP address.",
    )
    port: Annotated[int, Field(ge=1, le=65535)] = Field(
        default=1883,
        description="MQTT broker port.",
    )
    username: str | None = Field(
        default=None,
        description="MQTT authentication username (optional).",
    )
    password: SecretStr | None = Field(
        default=None,
        description="MQTT authentication password (optional).",
    )
    client_id: str = Field(
        default="velux2mqtt",
        description="MQTT client identifier.",
    )
    reconnect_interval: Annotated[float, Field(gt=0)] = Field(
        default=5.0,
        description="Seconds to wait before reconnecting after connection loss.",
    )
    qos: Literal[0, 1, 2] = Field(
        default=1,
        description="Default MQTT Quality of Service level.",
    )
    topic_prefix: str = Field(
        default="velux2mqtt",
        description="Root prefix for all MQTT topics (e.g. ``velux2mqtt/blind/…``).",
    )


class GpioPinConfig(BaseModel):
    """GPIO pin triplet for a single actuator (up / stop / down).

    Pin numbers follow the numbering mode selected by ``gpio_mode``
    (BCM by default).
    """

    up: Annotated[int, Field(ge=0)] = Field(
        description="GPIO pin for the UP direction.",
    )
    stop: Annotated[int, Field(ge=0)] = Field(
        description="GPIO pin for the STOP button.",
    )
    down: Annotated[int, Field(ge=0)] = Field(
        description="GPIO pin for the DOWN direction.",
    )

    @model_validator(mode="after")
    def _pins_must_be_distinct(self) -> Self:
        """All three pins must be different physical pins."""
        pins = [self.up, self.stop, self.down]
        if len(set(pins)) != len(pins):
            msg = (
                f"GPIO pins must be distinct, got "
                f"up={self.up}, stop={self.stop}, down={self.down}"
            )
            raise ValueError(msg)
        return self


class ActuatorConfig(BaseModel):
    """Configuration for a single Velux actuator (blind or window).

    Each actuator has a unique ``name`` used in MQTT topics, three GPIO
    pins, and timing parameters.
    """

    name: str = Field(
        description="Unique actuator identifier, used in MQTT topic paths.",
        min_length=1,
    )
    kind: Literal["blind", "window"] = Field(
        description="Actuator type — determines default behaviour.",
    )
    pins: GpioPinConfig = Field(
        description="GPIO pin assignments for this actuator.",
    )
    travel_duration: Annotated[float, Field(gt=0)] = Field(
        description="Full one-direction travel time in seconds.",
    )
    pulse_duration: Annotated[float, Field(gt=0, le=5.0)] = Field(
        default=0.5,
        description="GPIO button-press pulse length in seconds.",
    )


class LoggingSettings(BaseModel):
    """Logging configuration.

    When ``file`` is set, logs are written to a rotating file (midnight
    rotation, ``backup_count`` generations kept).  When ``None``, logs go
    to stderr only.

    The ``format`` field selects the output format:

    - ``"json"`` (default) — structured JSON lines for container log
      aggregators (Loki, Elasticsearch, CloudWatch).  Each line is a
      complete JSON object with correlation metadata (service, version).
    - ``"text"`` — human-readable timestamped format for local
      development and direct terminal use.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Root log level.",
    )
    format: Literal["json", "text"] = Field(
        default="json",
        description=(
            "Log output format. "
            "'json' emits structured JSON lines for container environments; "
            "'text' emits human-readable timestamped lines for development."
        ),
    )
    file: str | None = Field(
        default=None,
        description="Optional log file path.  ``None`` means stderr only.",
    )
    backup_count: Annotated[int, Field(ge=0)] = Field(
        default=3,
        description="Number of rotated log files to keep.",
    )


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Root application settings for velux2mqtt.

    Loaded from environment variables with the prefix ``V2M_`` and the
    nested delimiter ``__``.  A ``.env`` file in the working directory is
    also read.

    Actuators are supplied as a **JSON array** in the ``V2M_ACTUATORS``
    environment variable (or the ``.env`` file).

    Example ``.env``::

        V2M_MQTT__HOST=pi4server.lan
        V2M_MQTT__PORT=1883
        V2M_MQTT__USERNAME=jl4
        V2M_MQTT__PASSWORD=secret
        V2M_ACTUATORS='[{"name":"blind","kind":"blind","pins":{"up":9,"stop":10,"down":11},"travel_duration":18.5},{"name":"window","kind":"window","pins":{"up":23,"stop":24,"down":25},"travel_duration":20}]'
        V2M_LOGGING__LEVEL=INFO
        V2M_GPIO_MODE=BCM
    """

    model_config = SettingsConfigDict(
        env_prefix="V2M_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    actuators: list[ActuatorConfig] = Field(
        default_factory=list,
        description="List of actuator configurations.",
    )
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    gpio_mode: Literal["BCM", "BOARD"] = Field(
        default="BCM",
        description="GPIO pin numbering mode (Broadcom or physical board).",
    )
    gpio_backend: Literal["rpi", "dry-run"] = Field(
        default="rpi",
        description=(
            "GPIO backend to use. "
            "'rpi' requires RPi.GPIO on a Raspberry Pi; "
            "'dry-run' logs commands at INFO level without hardware."
        ),
    )

    @model_validator(mode="after")
    def _actuator_names_unique(self) -> Self:
        """Every actuator must have a distinct ``name``."""
        names = [a.name for a in self.actuators]
        if len(set(names)) != len(names):
            seen: set[str] = set()
            dupes = {n for n in names if n in seen or seen.add(n)}  # type: ignore[func-returns-value]
            msg = f"Actuator names must be unique, duplicates: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _all_pins_globally_unique(self) -> Self:
        """No two actuators may share a GPIO pin."""
        pin_owners: dict[int, str] = {}
        for actuator in self.actuators:
            for role, pin in [
                ("up", actuator.pins.up),
                ("stop", actuator.pins.stop),
                ("down", actuator.pins.down),
            ]:
                if pin in pin_owners:
                    msg = (
                        f"GPIO pin {pin} is used by both "
                        f"'{pin_owners[pin]}' and '{actuator.name}.{role}'"
                    )
                    raise ValueError(msg)
                pin_owners[pin] = f"{actuator.name}.{role}"
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings.

    Call ``get_settings.cache_clear()`` in tests that mutate environment
    variables to force re-evaluation.
    """
    return Settings()
