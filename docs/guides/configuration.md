---
icon: material/cog
---

# Configure Your Application

cosalette uses [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
for configuration — environment variables, `.env` files, and CLI flag overrides work
out of the box. This guide shows you how to extend the base `Settings` class for your
app-specific needs.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## The Base Settings Class

The framework provides a `Settings` class with two built-in sub-models:

```python title="cosalette framework (built-in)"
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    mqtt: MqttSettings = Field(default_factory=MqttSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
```

These cover MQTT broker connection and logging configuration. **Every cosalette app
gets these for free** — you only need to subclass `Settings` when you have
app-specific fields.

## Subclassing Settings

Create your own settings class with an `env_prefix` to namespace your environment
variables:

```python title="settings.py"
from pydantic import Field
from pydantic_settings import SettingsConfigDict

import cosalette


class Gas2MqttSettings(cosalette.Settings):  # (1)!
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",  # (2)!
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    serial_port: str = Field(
        default="/dev/ttyUSB0",
        description="Serial port for the gas meter sensor.",
    )
    poll_interval: int = Field(
        default=60,
        ge=1,
        description="Polling interval in seconds.",
    )
```

1. Inherit from `cosalette.Settings` to get `mqtt` and `logging` sub-models.
2. `env_prefix="GAS2MQTT_"` means all environment variables start with
   `GAS2MQTT_`. For example: `GAS2MQTT_SERIAL_PORT=/dev/ttyACM0`.

Then pass the class to `App`:

```python title="app.py"
app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
)
```

## Environment Variables and Nesting

The `env_nested_delimiter="__"` setting controls how nested models map to environment
variables. With `env_prefix="GAS2MQTT_"`:

| Environment Variable              | Settings Field          | Default       |
| --------------------------------- | ----------------------- | ------------- |
| `GAS2MQTT_SERIAL_PORT`            | `serial_port`           | `/dev/ttyUSB0`|
| `GAS2MQTT_POLL_INTERVAL`          | `poll_interval`         | `60`          |
| `GAS2MQTT_MQTT__HOST`             | `mqtt.host`             | `localhost`   |
| `GAS2MQTT_MQTT__PORT`             | `mqtt.port`             | `1883`        |
| `GAS2MQTT_MQTT__USERNAME`         | `mqtt.username`         | `None`        |
| `GAS2MQTT_MQTT__PASSWORD`         | `mqtt.password`         | `None`        |
| `GAS2MQTT_LOGGING__LEVEL`         | `logging.level`         | `INFO`        |
| `GAS2MQTT_LOGGING__FORMAT`        | `logging.format`        | `json`        |

!!! info "Double underscore for nesting"

    The `__` delimiter separates sub-model names from field names.
    `GAS2MQTT_MQTT__HOST` → `settings.mqtt.host`. This is a pydantic-settings
    convention — see their
    [nested models docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support).

## Using `.env` Files

Create a `.env` file in your project root:

```bash title=".env"
# MQTT broker
GAS2MQTT_MQTT__HOST=broker.local
GAS2MQTT_MQTT__PORT=1883
GAS2MQTT_MQTT__USERNAME=gas2mqtt
GAS2MQTT_MQTT__PASSWORD=supersecret

# Logging
GAS2MQTT_LOGGING__LEVEL=DEBUG
GAS2MQTT_LOGGING__FORMAT=text

# App-specific
GAS2MQTT_SERIAL_PORT=/dev/ttyACM0
GAS2MQTT_POLL_INTERVAL=30
```

The `env_file=".env"` in `model_config` tells pydantic-settings to load this file
automatically. Environment variables set in the shell take precedence over `.env`
values.

!!! tip "Don't commit `.env` to Git"

    Add `.env` to your `.gitignore`. Commit a `.env.example` with placeholder values
    instead, so new developers know which variables to set.

## CLI Flag Overrides

cosalette's built-in CLI (powered by Typer) provides command-line flags that override
settings:

```bash
# Override log level and format
gas2mqtt --log-level DEBUG --log-format text

# Use a different .env file
gas2mqtt --env-file /etc/gas2mqtt/.env

# Enable dry-run mode (uses mock adapters)
gas2mqtt --dry-run
```

**Available CLI flags:**

| Flag             | Settings Path    | Description                        |
| ---------------- | ---------------- | ---------------------------------- |
| `--log-level`    | `logging.level`  | Root log level                     |
| `--log-format`   | `logging.format` | `json` or `text`                   |
| `--dry-run`      | —                | Use dry-run adapter variants       |
| `--env-file`     | —                | Path to `.env` file                |
| `--version`      | —                | Print version and exit             |

**Priority order** (highest to lowest):

1. CLI flags
2. Environment variables
3. `.env` file values
4. Field defaults

## Secrets with SecretStr

For sensitive values like passwords, use pydantic's `SecretStr`:

```python title="settings.py"
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

import cosalette


class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    serial_port: str = Field(default="/dev/ttyUSB0")
    api_key: SecretStr = Field(  # (1)!
        default=SecretStr(""),
        description="API key for cloud reporting.",
    )
```

1. `SecretStr` masks the value in logs and `repr()` output. Access the actual value
   with `settings.api_key.get_secret_value()`.

The built-in `MqttSettings.password` field is already a `SecretStr` — MQTT
credentials are masked by default.

## Validators

Use pydantic's `field_validator` or `model_validator` for custom validation:

```python title="settings.py"
from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

import cosalette


class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    serial_port: str = Field(default="/dev/ttyUSB0")
    poll_interval: int = Field(default=60, ge=1)

    @field_validator("serial_port")
    @classmethod
    def serial_port_must_be_device(cls, v: str) -> str:
        """Validate that serial_port looks like a device path."""
        if not v.startswith("/dev/"):
            msg = f"serial_port must be a /dev/ path, got: {v!r}"
            raise ValueError(msg)
        return v
```

!!! info "Pydantic validation"

    Field constraints like `ge=1` (greater than or equal to 1) are checked at
    instantiation time. If `GAS2MQTT_POLL_INTERVAL=0` is set, pydantic raises a
    `ValidationError` before the app starts — failing fast is better than a
    runtime surprise.

## Accessing Settings in Devices

Settings are available via `ctx.settings` in both device and telemetry functions:

=== "Telemetry Device"

    ```python title="app.py"
    @app.telemetry("counter", interval=60)
    async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
        settings = ctx.settings  # (1)!
        assert isinstance(settings, Gas2MqttSettings)
        meter = ctx.adapter(GasMeterPort)
        return {"impulses": meter.read_impulses()}
    ```

    1. The settings instance is the same class you passed to `App(settings_class=...)`.
       Cast via `assert isinstance()` for type-safe access to custom fields.

=== "Command (`@app.command()`)"

    ```python title="app.py"
    @app.command("valve")
    async def valve(ctx: cosalette.DeviceContext, payload: str) -> dict[str, object]:
        settings = ctx.settings
        assert isinstance(settings, Gas2MqttSettings)
        meter = ctx.adapter(GasMeterPort)
        return {"valve_state": payload}
    ```

=== "Command Device (`@app.device()`)"

    ```python title="app.py"
    @app.device("valve")
    async def valve(ctx: cosalette.DeviceContext) -> None:
        settings = ctx.settings
        assert isinstance(settings, Gas2MqttSettings)

        @ctx.on_command
        async def handle(topic: str, payload: str) -> None:
            ...

        while not ctx.shutdown_requested:
            await ctx.sleep(30)
    ```

## Practical Example: gas2mqtt Settings

A complete, production-ready settings class:

```python title="settings.py"
"""Settings for gas2mqtt application."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

import cosalette


class Gas2MqttSettings(cosalette.Settings):
    """Configuration for the gas2mqtt bridge daemon."""

    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Hardware
    serial_port: str = Field(
        default="/dev/ttyUSB0",
        description="Serial port for the gas meter sensor.",
    )
    baud_rate: int = Field(
        default=9600,
        description="Serial baud rate.",
    )

    # Polling
    poll_interval: int = Field(
        default=60,
        ge=1,
        description="Telemetry polling interval in seconds.",
    )

    # Optional cloud reporting
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key for cloud reporting (optional).",
    )

    @field_validator("serial_port")
    @classmethod
    def serial_port_must_be_device(cls, v: str) -> str:
        if not v.startswith("/dev/"):
            msg = f"serial_port must be a /dev/ path, got: {v!r}"
            raise ValueError(msg)
        return v
```

```bash title=".env"
# gas2mqtt configuration
GAS2MQTT_SERIAL_PORT=/dev/ttyACM0
GAS2MQTT_BAUD_RATE=115200
GAS2MQTT_POLL_INTERVAL=30

# MQTT broker
GAS2MQTT_MQTT__HOST=broker.local
GAS2MQTT_MQTT__USERNAME=gas2mqtt
GAS2MQTT_MQTT__PASSWORD=s3cret

# Logging
GAS2MQTT_LOGGING__LEVEL=INFO
GAS2MQTT_LOGGING__FORMAT=json
```

---

## See Also

- [Configuration](../concepts/configuration.md) — conceptual overview of the
  configuration system
- [Logging](../concepts/logging.md) — logging configuration and formatting
- [ADR-003](../adr/ADR-003-configuration-system.md) — configuration system decisions
- [ADR-004](../adr/ADR-004-logging-strategy.md) — logging strategy decisions
