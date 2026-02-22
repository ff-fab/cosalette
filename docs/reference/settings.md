# Settings Reference

Configuration reference for cosalette applications. Settings are managed by
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
and can be set via constructor arguments, environment variables, or `.env` files.

!!! tip "Getting started with configuration"

    See the [Configuration guide](../guides/configuration.md) for practical
    examples and the [Configuration concept](../concepts/configuration.md) for
    architectural context.

## Root Settings

::: cosalette.Settings

## MQTT Settings

::: cosalette.MqttSettings

## Logging Settings

::: cosalette.LoggingSettings

## Environment Variables

All settings can be overridden via environment variables using the nested
`__` separator convention from pydantic-settings.

### MQTT

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MQTT__HOST` | `str` | `"localhost"` | MQTT broker hostname or IP address |
| `MQTT__PORT` | `int` | `1883` | MQTT broker port (1–65535) |
| `MQTT__USERNAME` | `str  | None` | `None` | MQTT authentication username |
| `MQTT__PASSWORD` | `SecretStr | None` | `None` | MQTT authentication password (masked in logs) |
| `MQTT__CLIENT_ID` | `str` | `""` | MQTT client identifier. Empty = auto-generated as `{name}-{hex8}` at startup |
| `MQTT__RECONNECT_INTERVAL` | `float` | `5.0` | Initial seconds before reconnecting (doubles with jitter on each failure, up to max) |
| `MQTT__RECONNECT_MAX_INTERVAL` | `float` | `300.0` | Upper bound (seconds) for exponential reconnect backoff |
| `MQTT__TOPIC_PREFIX` | `str` | `""` | Root prefix for all MQTT topics. Empty = uses `App(name=...)`. Set to override (e.g. staging) |

### Logging

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOGGING__LEVEL` | `str` | `"INFO"` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `LOGGING__FORMAT` | `str` | `"json"` | Log output format (`json` or `text`) |
| `LOGGING__FILE` | `str | None` | `None` | Optional log file path — `None` means stderr only |
| `LOGGING__MAX_FILE_SIZE_MB` | `int` | `10` | Maximum log file size in megabytes before rotation. Only has effect when `file` is set |
| `LOGGING__BACKUP_COUNT` | `int` | `3` | Number of rotated log files to keep |

!!! note "Application prefix"

    The base `Settings` class has **no** `env_prefix`. When you subclass
    `Settings` for your project, you can add one
    (e.g. `env_prefix="MYAPP_"`) — all variables above would then require
    that prefix: `MYAPP_MQTT__HOST`, `MYAPP_LOGGING__LEVEL`, etc.

## Settings Injection

Settings are automatically injected into device handlers and adapter factory
callables that declare a parameter annotated with `Settings` (or a subclass).

| Context                 | How to access                                      |
|-------------------------|----------------------------------------------------|
| Device handlers         | Declare a `Settings`-typed parameter               |
| Adapter factory callables | Declare a `Settings`-typed parameter             |
| Lifespan hook           | `ctx.settings`                                     |

See the [Adapters guide](../guides/adapters.md#factory-settings-injection) for
examples of settings injection in factory callables.
