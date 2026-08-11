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

With `env_prefix="GAS2MQTT_"`, the environment variables for your application are:

| Environment Variable              | Settings Field          | Default       |
| --------------------------------- | ----------------------- | ------------- |
| `GAS2MQTT_SERIAL_PORT`            | `serial_port`           | `/dev/ttyUSB0`|
| `GAS2MQTT_POLL_INTERVAL`          | `poll_interval`         | `60`          |
| `GAS2MQTT_MQTT__HOST`             | `mqtt.host`             | `localhost`   |
| `GAS2MQTT_MQTT__PORT`             | `mqtt.port`             | `1883`        |
| `GAS2MQTT_MQTT__USERNAME`         | `mqtt.username`         | `None`        |
| `GAS2MQTT_MQTT__PASSWORD`         | `mqtt.password`         | `None`        |
| `GAS2MQTT_MQTT__TLS`              | `mqtt.tls`              | `false`       |
| `GAS2MQTT_MQTT__TLS_CA_FILE`      | `mqtt.tls_ca_file`      | `None`        |
| `GAS2MQTT_MQTT__TLS_CERT_FILE`    | `mqtt.tls_cert_file`    | `None`        |
| `GAS2MQTT_MQTT__TLS_KEY_FILE`     | `mqtt.tls_key_file`     | `None`        |
| `GAS2MQTT_LOGGING__LEVEL`         | `logging.level`         | `INFO`        |
| `GAS2MQTT_LOGGING__FORMAT`        | `logging.format`        | `json`        |

The `__` delimiter separates nested sub-model names from field names
(`GAS2MQTT_MQTT__HOST` → `settings.mqtt.host`). For the full base variable
table and `.env` file loading rules, see the
[Settings reference](../reference/settings.md#environment-variables).

## Using `.env` Files

Create a `.env` file in your project root:

```bash title=".env"
# MQTT broker
GAS2MQTT_MQTT__HOST=broker.local
GAS2MQTT_MQTT__PORT=1883
GAS2MQTT_MQTT__USERNAME=gas2mqtt
GAS2MQTT_MQTT__PASSWORD=supersecret
# For broker TLS on port 8883, also set:
# GAS2MQTT_MQTT__TLS=true
# GAS2MQTT_MQTT__TLS_CA_FILE=/etc/ssl/mqtt-ca.pem

# Logging
GAS2MQTT_LOGGING__LEVEL=DEBUG
GAS2MQTT_LOGGING__FORMAT=text

# App-specific
GAS2MQTT_SERIAL_PORT=/dev/ttyACM0
GAS2MQTT_POLL_INTERVAL=30
```

!!! tip "Don't commit `.env` to Git"

    Add `.env` to your `.gitignore`. Commit a `.env.example` with placeholder values
    instead, so new developers know which variables to set.

## Config Files (TOML / YAML / JSON)

Environment variables work well for scalars but become unwieldy for **inventories** —
lists of homogeneous entities whose cardinality varies per deployment. Packing a list
into an env var requires a single-line JSON blob with no comments and no per-entity
diff granularity:

```dotenv
# Before: entire inventory in one unreadable line
MYAPP_SENSORS='[{"name":"office","pin":17},{"name":"outdoor","pin":27},{"name":"garage","pin":22}]'
```

A config file expresses the same inventory readably:

```toml title="myapp.toml"
[[sensors]]
name = "office"
pin = 17

[[sensors]]
name = "outdoor"
pin = 27

[[sensors]]
name = "garage"
pin = 22
```

### Enabling a Config File

Set `config_file=` in your settings class `model_config`:

```python title="settings.py"
from pydantic_settings import SettingsConfigDict
import cosalette


class MyAppSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="MYAPP_",
        env_nested_delimiter="__",
        env_file=".env",
        config_file="myapp.toml",  # (1)!
    )
```

1. Path is resolved relative to the working directory. Default is `None` (disabled).

Alternatively, pass `--config-file` on the CLI to override at runtime without changing
the class:

```bash
myapp --config-file /etc/myapp/myapp.toml
```

### Precedence and Nested Merge

Config file values sit below environment variables and `.env` in the loading order:

```
env > .env > config file > defaults
```

Nested models merge **per field** across sources. A file can supply structure while an
env var overrides a single leaf:

```toml title="myapp.toml"
[mqtt]
host = "broker.local"
port = 1883
```

```bash
# MYAPP_MQTT__PORT=8883 overrides only port; host from the file survives
MYAPP_MQTT__PORT=8883 myapp  # mqtt.host=broker.local, mqtt.port=8883
```

### Supported Formats

| Format | Extension       | Extra dependency                        |
| ------ | --------------- | --------------------------------------- |
| TOML   | `.toml`         | None (stdlib `tomllib`)                 |
| JSON   | `.json`         | None (stdlib `json`)                    |
| YAML   | `.yaml`, `.yml` | `pip install cosalette[config-yaml]`    |

Format is dispatched automatically from the file's suffix.

!!! warning "Keep secrets in the environment"

    Config files are mounted, templated, and code-reviewed. Place `SecretStr`
    fields (MQTT password, API keys) in environment variables or `.env`, not in
    the config file. The precedence chain enforces this: any env var overrides the
    corresponding file value.

!!! tip "Gitignore the real file; commit an example"

    Add `myapp.toml` to `.gitignore` and commit `myapp.toml.example` with
    representative, non-secret values so contributors know the expected structure.

### Fail-Loud Behaviour

A non-`None` `config_file` pointing at a missing file raises
`cosalette.SettingsLoadError` and exits with code 1. It is **never** silently skipped
(unlike a defaulted path that may have no file yet). A malformed file produces
`could not load configuration file '<path>': <detail>`, not a raw decode traceback.

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

| Flag             | Settings Path    | Description                           |
| ---------------- | ---------------- | ------------------------------------- |
| `--log-level`    | `logging.level`  | Root log level                        |
| `--log-format`   | `logging.format` | `json` or `text`                      |
| `--dry-run`      | —                | Use dry-run adapter variants          |
| `--env-file`     | —                | Path to `.env` file                   |
| `--config-file`  | —                | Path to a TOML/YAML/JSON config file  |
| `--version`      | —                | Print version and exit                |

**Priority order** (highest to lowest):

1. CLI flags
2. Environment variables
3. `.env` file values
4. Config file values
5. Field defaults

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

For broker TLS, set `MQTT__TLS=true` and point `MQTT__TLS_CA_FILE` at the CA
bundle that validates your broker certificate. Mutual TLS additionally requires
both `MQTT__TLS_CERT_FILE` and `MQTT__TLS_KEY_FILE`; cosalette rejects one without
the other so partial certificate configuration fails before connecting.

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

## Using Settings in Decorator Arguments

`App.__init__` eagerly instantiates the `settings_class`, making `app.settings`
available at **decoration time** — before the app is started. This lets you use
configuration values directly in decorator arguments like `interval=`:

```python title="app.py"
import cosalette
from pydantic import Field
from pydantic_settings import SettingsConfigDict


class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    poll_interval: int = Field(default=60, ge=1)


app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
)


@app.telemetry("counter", interval=app.settings.poll_interval)  # (1)!
async def counter() -> dict[str, object]:
    return {"impulses": 42}
```

1. `app.settings.poll_interval` is evaluated when the module loads. Environment
   variables and `.env` files have already been read by this point.
   Set `GAS2MQTT_POLL_INTERVAL=30` to override the default of 60.

!!! info "How it works"

    When `App(settings_class=Gas2MqttSettings)` is called, the constructor runs
    `Gas2MqttSettings()` immediately. Since pydantic-settings reads environment
    variables and `.env` files at instantiation time, `app.settings` already
    reflects the runtime configuration when Python evaluates the decorator.

    The CLI entrypoint (`app.run()`) may re-instantiate settings with `--env-file`
    support, but the decorator arguments are fixed at import time.

!!! warning "`--help` safety"

    Because `app.settings` is evaluated eagerly at import time, running
    `myapp --help` will crash if required environment variables are missing.
    For **dynamic registration** driven by settings (e.g. registering devices
    from a config file), use [`@app.on_configure`](multi-device.md) instead —
    it runs after CLI parsing and is safe even without environment variables set.

!!! tip "Conditional device registration"

    The simplest approach is the `enabled=` parameter, available on all
    device decorators:

    ```python
    # Modern approach — enabled= parameter
    @app.telemetry("debug", interval=10, enabled=app.settings.enable_debug_device)
    async def debug_sensor() -> dict[str, object]:
        return {"debug": True}
    ```

    When `enabled=False`, the decorator silently skips registration — no
    entry in the device registry and no name slot reserved.

    The classic `if`-guard also works, since `app.settings` is a plain Python
    object:

    ```python
    # Classic approach — if-guard
    if app.settings.enable_debug_device:
        @app.telemetry("debug", interval=10)
        async def debug_sensor() -> dict[str, object]:
            return {"debug": True}
    ```

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
    async def valve(ctx: cosalette.DeviceContext):
        settings = ctx.settings
        assert isinstance(settings, Gas2MqttSettings)

        @ctx.on_command
        async def handle(topic: str, payload: str) -> None:
            ...

        while not ctx.shutdown_requested:
            await ctx.sleep(30)
            yield  # reaction boundary
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

- [Configuration Model](../concepts/configuration.md) — hierarchy, precedence, and why the extension pattern exists
- [Settings reference](../reference/settings.md) — full env-var table, `.env` loading, and mkdocstrings API
- [Logging](../concepts/logging.md) — logging configuration and formatting
- [ADR-003](../adr/ADR-003-configuration-system.md) — configuration system decisions
- [ADR-004](../adr/ADR-004-logging-strategy.md) — logging strategy decisions
