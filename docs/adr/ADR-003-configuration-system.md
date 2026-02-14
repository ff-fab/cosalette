# ADR-003: Configuration System

## Status

Accepted **Date:** 2026-02-14

## Context

All 8 IoT-to-MQTT bridge projects share common configuration concerns: MQTT broker
connection (host, port, credentials), logging settings, and project-specific parameters
(GPIO pins, I²C addresses, polling intervals). The velux2mqtt reference implementation
already uses pydantic-settings with `BaseSettings`, environment variable loading, `.env`
file support, and `SecretStr` for credentials — a pattern that has proven effective.

Key requirements:

- All projects share the same MQTT broker but are deployed across different hosts, requiring per-deployment configuration
- Environment variable-based configuration fits both bare-metal and Docker deployments
- Credentials (MQTT password) must not leak into logs or error messages
- Nested configuration (e.g., `MQTT__HOST`) must be supported for clean grouping
- The `env_prefix` must be configurable per project (e.g., `VELUX2MQTT_` or empty string
  for clean Docker env files)

## Decision

Use **pydantic-settings with `BaseSettings`**, `env_nested_delimiter="__"`, `.env` file
support, and `SecretStr` for credentials because it provides type-safe, validated
configuration with zero custom code, aligning with the framework's type-hint-driven
philosophy.

The framework provides a base `cosalette.Settings` class with `MqttSettings` and
`LoggingSettings` pre-configured. Projects extend this with their own fields:

```python
class Settings(BaseSettings):
    """Base settings — all cosalette apps inherit these."""
    mqtt: MqttSettings = MqttSettings()
    logging: LoggingSettings = LoggingSettings()

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
```

Projects extend with `env_prefix` configurable per project:

```python
class VeluxSettings(cosalette.Settings):
    gpio_mode: Literal["BCM", "BOARD"] = "BCM"
    actuators: list[ActuatorConfig] = []

    class Config:
        env_prefix = "VELUX2MQTT_"
```

## Decision Drivers

- Type-safe, validated configuration with automatic coercion
- Environment variable loading (12-factor app compliance)
- `.env` file support for local development and Docker deployments
- Credential masking via `SecretStr` (MQTT password must not leak)
- Nested model support (`MQTT__HOST`, `LOGGING__LEVEL`)
- Configurable `env_prefix` per project to avoid collisions or allow clean env files
- Proven pattern from 8 months of velux2mqtt production use

## Considered Options

### Option 1: Plain environment variables with `os.getenv()`

Read environment variables directly with manual type conversion.

- *Advantages:* Zero dependencies, maximum simplicity.
- *Disadvantages:* No validation, no type coercion, no nesting, no credential masking.
  Every project reimplements parsing and defaults. Error messages on misconfiguration
  are poor.

### Option 2: YAML/TOML configuration files

Use structured file-based configuration (e.g., `config.yaml`).

- *Advantages:* Rich structure, comments in config, good for complex hierarchies.
- *Disadvantages:* Does not fit the Docker/container convention of env-based config
  (12-factor app violation). Requires file mounting in containers. Two sources of truth
  if env vars are also supported. Does not integrate with pydantic's validation.

### Option 3: Dataclasses with custom loading

Use `@dataclass` classes with a custom `from_env()` classmethod.

- *Advantages:* No pydantic dependency, standard library types.
- *Disadvantages:* Requires reimplementing validation, coercion, nesting, `.env` file
  parsing, and credential masking — all of which pydantic-settings provides for free.
  Violates DRY when pydantic is already a dependency.

### Option 4: pydantic-settings with BaseSettings (chosen)

Use pydantic-settings for type-safe, validated, env-based configuration with nesting
and `.env` file support.

- *Advantages:* Type-safe validation with clear error messages. Automatic environment
  variable loading with configurable prefix and nesting delimiter. `.env` file support.
  `SecretStr` for credential masking. Pydantic validators for complex constraints (pin
  uniqueness, name uniqueness). Already proven in velux2mqtt production.
- *Disadvantages:* Adds pydantic-settings as a dependency (pydantic is already required).
  The `env_nested_delimiter="__"` convention must be documented clearly.

## Decision Matrix

| Criterion          | Plain `os.getenv` | YAML/TOML Files | Dataclasses + Custom | pydantic-settings |
| ------------------ | ----------------- | --------------- | -------------------- | ----------------- |
| Type safety        | 1                 | 3               | 3                    | 5                 |
| Env var support    | 5                 | 2               | 3                    | 5                 |
| Credential masking | 1                 | 1               | 2                    | 5                 |
| Nesting support    | 1                 | 5               | 3                    | 5                 |
| Maintenance burden | 5                 | 3               | 2                    | 4                 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Configuration validation happens at startup with clear error messages — no silent
  misconfigurations at runtime
- `SecretStr` prevents MQTT passwords from appearing in logs, `repr()`, or error payloads
- Nested models cleanly separate MQTT, logging, and project-specific settings
- `.env` files work seamlessly for both local development and Docker deployments
- Projects inherit MQTT and logging settings from the framework base class —
  only project-specific fields need to be defined
- Pydantic validators enable complex cross-field validation (e.g., unique actuator names,
  globally unique GPIO pins)

### Negative

- `env_nested_delimiter="__"` is a convention that must be learned (e.g.,
  `MQTT__HOST` instead of `MQTT_HOST`)
- Complex nested configurations (like actuator lists) require JSON encoding in env vars,
  which is less readable
- pydantic-settings is an additional dependency, though pydantic itself is already
  required for the framework

_2026-02-14_
