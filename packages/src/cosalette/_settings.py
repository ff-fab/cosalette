"""Application configuration via pydantic-settings.

Configuration is loaded from environment variables and/or ``.env``
files.  Nested models use ``__`` as the delimiter in env var names,
e.g. ``MQTT__HOST=broker.local``.

The schema covers the two infrastructure concerns shared by all
cosalette-based applications:

* **MQTT** — broker connection and topic layout.
* **Logging** — level, format, optional file sink, rotation.

Framework consumers subclass :class:`Settings` and add their own
``env_prefix`` plus any application-specific fields.  The base
``Settings`` intentionally sets **no** ``env_prefix`` so that the
framework itself remains neutral.

All durations are in **seconds**.

See Also:
    ADR-003 for configuration-system decisions.
    ADR-006 for hexagonal architecture and Protocol-based ports.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# -------------------------------------------------------------------
# Sub-models (BaseModel, NOT BaseSettings — nested via composition)
# -------------------------------------------------------------------


class MqttSettings(BaseModel):
    """MQTT broker connection and topic configuration.

    Environment variables (with ``__`` nesting)::

        MQTT__HOST=broker.local
        MQTT__PORT=1883
        MQTT__USERNAME=user
        MQTT__PASSWORD=secret
        MQTT__TOPIC_PREFIX=myapp
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
        default="",
        description=(
            "MQTT client identifier. When empty, App auto-generates "
            "'{name}-{hex8}' at startup for debuggability."
        ),
    )
    reconnect_interval: Annotated[float, Field(gt=0)] = Field(
        default=5.0,
        description=(
            "Initial seconds to wait before reconnecting after "
            "connection loss.  Doubles on each consecutive failure "
            "(exponential backoff with jitter) up to "
            "``reconnect_max_interval``."
        ),
    )
    reconnect_max_interval: Annotated[float, Field(gt=0)] = Field(
        default=300.0,
        description=(
            "Upper bound (seconds) for the exponential reconnect "
            "backoff.  The delay doubles after each failure but "
            "never exceeds this value."
        ),
    )
    topic_prefix: str = Field(
        default="",
        description=(
            "Root prefix for all MQTT topics. "
            "When empty, falls back to App(name=...). "
            "Set via MQTT__TOPIC_PREFIX to override."
        ),
    )


class LoggingSettings(BaseModel):
    """Logging configuration.

    When ``file`` is set, logs are also written to a rotating file
    (size-based rotation, ``backup_count`` generations kept).  When
    ``None``, logs go to stderr only.

    The ``format`` field selects the output format:

    - ``"json"`` (default) — structured JSON lines for container
      log aggregators (Loki, Elasticsearch, CloudWatch).  Each
      line is a complete JSON object with correlation metadata.
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
            "'json' emits structured JSON lines for "
            "container environments; "
            "'text' emits human-readable timestamped "
            "lines for development."
        ),
    )
    file: str | None = Field(
        default=None,
        description="Optional log file path. ``None`` means stderr only.",
    )
    max_file_size_mb: Annotated[int, Field(ge=1)] = Field(
        default=10,
        description=(
            "Maximum log file size in megabytes before rotation. "
            "Only applies when ``file`` is set."
        ),
    )
    backup_count: Annotated[int, Field(ge=0)] = Field(
        default=3,
        description="Number of rotated log files to keep.",
    )


# -------------------------------------------------------------------
# Root settings
# -------------------------------------------------------------------


class Settings(BaseSettings):
    """Root framework settings for cosalette applications.

    Loaded from environment variables with the nested delimiter
    ``__`` and an optional ``.env`` file in the working directory.

    **No ``env_prefix``** is set at the framework level — each
    application subclasses ``Settings`` and adds its own prefix
    (e.g. ``env_prefix="MYAPP_"``).

    Example ``.env``::

        MQTT__HOST=broker.local
        MQTT__PORT=1883
        MQTT__USERNAME=user
        MQTT__PASSWORD=secret
        LOGGING__LEVEL=DEBUG
        LOGGING__FORMAT=text

    Example with an application prefix (subclass)::

        class MyAppSettings(Settings):
            model_config = SettingsConfigDict(
                env_prefix="MYAPP_",
                env_nested_delimiter="__",
                env_file=".env",
                env_file_encoding="utf-8",
            )
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    """Settings uses ``extra="ignore"`` because the base class sets
    **no** ``env_prefix``.  Without a prefix, pydantic-settings reads
    every environment variable; the ``BaseSettings`` default of
    ``extra="forbid"`` would then reject unrelated variables
    (``GH_TOKEN``, ``PATH``, etc.) as validation errors.

    Subclasses that set ``env_prefix`` only see prefixed variables
    and may safely tighten this to ``extra="forbid"`` for strict
    validation.
    """

    mqtt: MqttSettings = Field(
        default_factory=MqttSettings,
        description="MQTT broker connection settings.",
    )
    logging: LoggingSettings = Field(
        default_factory=LoggingSettings,
        description="Logging configuration.",
    )
