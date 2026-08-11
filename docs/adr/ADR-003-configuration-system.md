---
status: Accepted
date: 2026-02-14
impact: moderate
tags: [configuration]
---

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

## Amendment (2026-08-11) — Moderate

!!! note "Editorial note (2026-08-11)"
    This is an amendment, not a reversal. ADR-003's Decision — pydantic-settings
    with `BaseSettings`, `env_nested_delimiter="__"`, `SecretStr` for credentials —
    is untouched. What changes is Option 2's blanket rejection of structured file
    sources.

    Structured config files are admitted as a **subordinate** settings source under
    a fixed precedence chain: `env > dotenv (.env) > config_file > field defaults`.
    The feature is opt-in via `config_file="path.toml"` in `SettingsConfigDict`
    (default `None`) or `--config-file <path>` on the CLI. All existing apps are
    unaffected.

    Option 2's four objections, each addressed in turn:

    1. **12-factor violation.** Under `env > file > defaults` the file is a
       *default provider*, not a configuration authority. A container that mounts
       no file behaves exactly as today; 12-factor requires only that config be
       environment-overridable and not baked into the image, which a mounted,
       env-overridable inventory file satisfies. The same convention already
       accepts `.env`, which is equally a mounted file.
    2. **Requires file mounting in containers.** Only for applications that opt in.
       `config_file` defaults to `None`; every existing app is unaffected.
    3. **Two sources of truth.** Two sources become two *truths* only when
       resolution order is undefined. The strict `env > file > defaults` chain
       makes exactly one source authoritative — the environment, always — with the
       file supplying values the environment did not set. This is the same
       relationship `.env` already has; ADR-003 shipped a two-source system on
       day one.
    4. **Does not integrate with pydantic's validation.** Obsolete. pydantic-
       settings now ships `TomlConfigSettingsSource`, `YamlConfigSettingsSource`,
       and `JsonConfigSettingsSource` as first-class sources whose values traverse
       the identical validation pipeline as environment variables.

    Formats are dispatched by file suffix: `.toml` (stdlib `tomllib`, no extra
    dependency), `.yaml`/`.yml` (PyYAML — `cosalette[config-yaml]` extra), `.json`
    (stdlib `json`). Secrets remain environment-only.

    A non-`None` `config_file` pointing at a missing file raises
    `cosalette.SettingsLoadError` and exits with code 1 — it is never silently
    skipped. A malformed file produces an honest error message rather than an
    import failure.
