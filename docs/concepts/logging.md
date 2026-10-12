---
icon: material/text-box-outline
---

# Logging

Cosalette produces **structured JSON logs** for production and **human-readable
text logs** for development, with UTC timestamps, correlation metadata, and
zero external dependencies.

## Two Formats

=== "JSON (production)"

    ```json
    {"timestamp": "2026-02-14T12:34:56+00:00", "level": "INFO", "logger": "cosalette._app", "message": "MQTT connected to broker.local:1883", "service": "velux2mqtt", "version": "0.3.0"}
    ```

=== "Text (development)"

    ```
    2026-02-14 12:34:56,123 [INFO] cosalette._app: MQTT connected to broker.local:1883
    ```

The format is selected via `logging.format` in settings or `--log-format`
on the CLI:

```bash
myapp --log-format text   # development
myapp --log-format json   # production (default)
```

## NDJSON Format

JSON logs follow the **NDJSON** (Newline Delimited JSON) convention: one
complete JSON object per line, no embedded newlines. This is critical for
container log drivers (Docker, Kubernetes) that split on `\n`.

```python
def format(self, record: logging.LogRecord) -> str:
    entry = {
        "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
        "service": self._service,
    }
    if self._version:
        entry["version"] = self._version
    if record.exc_info and record.exc_info[0] is not None:
        entry["exception"] = self.formatException(record.exc_info)
    if record.stack_info:
        entry["stack_info"] = self.formatStack(record.stack_info)
    return json.dumps(entry, default=str)  # (1)!
```

1. `json.dumps` with `default=str` ensures even unexpected types (e.g.
   `pathlib.Path`) serialise without raising. Tracebacks are escaped
   by `json.dumps`, so multi-line exceptions become single-line JSON.

## UTC Timestamps

All timestamps are **UTC** in RFC 3339 / ISO 8601 format:

```
2026-02-14T12:34:56.789012+00:00
```

```python
datetime.fromtimestamp(record.created, tz=UTC).isoformat()
```

## JSON Fields

| Field        | Type   | Always present | Description                              |
|--------------|--------|----------------|------------------------------------------|
| `timestamp`  | string | Yes            | ISO 8601 UTC timestamp                   |
| `level`      | string | Yes            | Python log level (`INFO`, `WARNING`, etc.) |
| `logger`     | string | Yes            | Dotted logger name (`cosalette._app`)    |
| `message`    | string | Yes            | Formatted log message                    |
| `service`    | string | Yes            | Application name (for log correlation)   |
| `version`    | string | When non-empty | Application version                      |
| `exception`  | string | When present   | Formatted traceback                      |
| `stack_info` | string | When present   | Stack trace (if `stack_info=True`)       |

### Correlation Metadata

Every log line includes `service` and `version`, enabling log aggregators to
filter and group entries without extra parser configuration:

```bash
# Loki query: all errors from velux2mqtt
{service="velux2mqtt"} |= "ERROR"

# Elasticsearch query: specific version
{"query": {"match": {"version": "0.3.0"}}}
```

## Custom Formatter Over python-json-logger

Cosalette implements its own `JsonFormatter` (~70 lines) rather than depending
on `python-json-logger`:

| Consideration          | Custom formatter          | python-json-logger        |
|------------------------|---------------------------|---------------------------|
| **Dependencies**       | Zero (stdlib only)        | One additional package    |
| **Field control**      | Full — matches project schema | Library defaults, overridable |
| **Container image**    | Smaller                   | Extra install step         |
| **Maintenance**        | Owned by project          | Third-party release cycle  |

## configure_logging()

The `configure_logging()` function is called once during Phase 1 (Bootstrap):

```python
configure_logging(
    settings.logging,  # LoggingSettings
    service="velux2mqtt",
    version="0.3.0",
)
```

It performs these steps:

1. **Clear existing handlers** on the root logger (prevents duplicate output)
2. **Build formatter** — `JsonFormatter` for `"json"`, `logging.Formatter` for `"text"`
3. **StreamHandler** → `stderr` (always installed)
4. **RotatingFileHandler** → optional, when `settings.logging.file` is set
5. **Redaction filter** → on each of those handlers, when `redact=` is set

### Redaction

`App(redact=...)` is passed to `configure_logging(..., redact=...)`, which
installs a filter on each handler it creates. The filter redacts a **copy** of
every record — the formatted message, the formatted traceback and the stack
info — so any other handler (a test's capture handler, say) still sees the
original. The filter sits on the handlers rather than on the `cosalette`
logger because a logger filter does not reach child loggers, while app and
library loggers all write through these handlers. `JsonFormatter` uses the
redacted traceback (`record.exc_text`) when one is present. The accepted
forms are described in
[Error Handling](error-handling.md#redacting-disclosed-messages); see
[ADR-085](../adr/ADR-085-optional-redaction-hook-for-logs-and-disclosed-error-messages.md).

### Text Format String

```python
_TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

Produces output like:

```
2026-02-14 12:34:56,123 [INFO] cosalette._app: Shutdown complete
```

### RotatingFileHandler

When `logging.file` is configured, logs are also written to a rotating file:

| Parameter      | Value                         |
|----------------|-------------------------------|
| `maxBytes`     | `settings.logging.max_file_size_mb` (default: 10 MB) |
| `backupCount`  | `settings.logging.backup_count` (default: 3) |
| `encoding`     | UTF-8                         |

The file handler uses the same formatter as the stream handler (JSON or text),
so file logs and stderr logs are always in the same format.

## Log Output Examples

=== "JSON — normal"

    ```json
    {"timestamp": "2026-02-14T12:34:56.789012+00:00", "level": "INFO", "logger": "cosalette._app", "message": "MQTT connected to broker.local:1883", "service": "velux2mqtt", "version": "0.3.0"}
    ```

=== "JSON — with exception"

    ```json
    {"timestamp": "2026-02-14T12:34:57.123456+00:00", "level": "CRITICAL", "logger": "cosalette._supervisor", "message": "Task 'device:blind' died (entities: blind): Connection refused", "service": "velux2mqtt", "version": "0.3.0", "exception": "Traceback (most recent call last):\n  File \"_app.py\", line 268\n    ...\nConnectionRefusedError: Connection refused"}
    ```

=== "Text — normal"

    ```
    2026-02-14 12:34:56,789 [INFO] cosalette._app: MQTT connected to broker.local:1883
    ```

=== "Text — with exception"

    ```
    2026-02-14 12:34:57,123 [CRITICAL] cosalette._supervisor: Task 'device:blind' died (entities: blind): Connection refused
    Traceback (most recent call last):
      File "_app.py", line 268
        ...
    ConnectionRefusedError: Connection refused
    ```

## Configuration Reference

```bash title=".env"
LOGGING__LEVEL=DEBUG       # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOGGING__FORMAT=json       # json or text
LOGGING__FILE=/var/log/myapp/myapp.log  # optional
LOGGING__BACKUP_COUNT=5    # rotated file generations
LOGGING__LEVELS='{"asyncssh":"WARNING"}'  # per-logger overrides (JSON)
```

Or via CLI override:

```bash
myapp --log-level DEBUG --log-format text
```

CLI flags override environment variables and `.env` settings, following the
[configuration hierarchy](configuration.md). `--log-level` sets the root level
only; per-logger overrides from `logging.levels` still apply.

## Per-logger Levels

`logging.levels` maps logger names to levels. Use it to quiet a chatty library
(asyncssh, bleak, pymodbus, aiohttp, zeroconf) or to turn on `DEBUG` for one
module while the root stays at `INFO`:

```bash title=".env"
MYAPP_LOGGING__LEVELS='{"asyncssh": "WARNING", "myapp.adapters": "DEBUG"}'
```

```toml title="app.toml (config file)"
[logging.levels]
asyncssh = "WARNING"
```

- Level names are case-insensitive (`"warning"` works) and must be one of
  `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Numeric levels are rejected.
- Logger names are case-sensitive and kept exactly as written.
- An override applies to the named logger and its children (`asyncssh` also
  covers `asyncssh.connection`). A logger set to `DEBUG` emits debug records even
  when the root is `INFO`, because cosalette's handlers have no level of their own.
- Each `configure_logging()` call first resets the loggers the previous call
  overrode, so a removed override does not linger.

!!! warning "Prefer the JSON env form"

    pydantic-settings also accepts the nested form
    `MYAPP_LOGGING__LEVELS__asyncssh=WARNING`, but it **lowercases the key**.
    `MYAPP_LOGGING__LEVELS__Foo=DEBUG` configures the logger `foo`, not `Foo`.
    Use the JSON form whenever a logger name contains uppercase letters.

### App Defaults

To ship default overrides with your app, subclass `LoggingSettings` with a
`levels` default and annotate your settings' `logging` field with it:

```python
from typing import Literal

from pydantic import Field
from pydantic_settings import SettingsConfigDict

import cosalette

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class MyLoggingSettings(cosalette.LoggingSettings):
    levels: dict[str, Level] = Field(default={"asyncssh": "WARNING"})


class MySettings(cosalette.Settings):
    model_config = SettingsConfigDict(env_prefix="MYAPP_", env_nested_delimiter="__")

    logging: MyLoggingSettings = Field(default_factory=MyLoggingSettings)
```

pydantic copies the default for each instance, so the shared dict is safe.
`MYAPP_LOGGING__LEVELS` replaces the default mapping as a whole; other
`MYAPP_LOGGING__*` variables leave it in place.

!!! danger "Do not override the field's default instance"

    `logging: LoggingSettings = LoggingSettings(levels={...})` looks equivalent
    but is not: as soon as any `MYAPP_LOGGING__*` variable is set,
    pydantic-settings builds `logging` from the environment alone and the
    default instance, with its `levels`, is silently dropped. The subclass keeps
    the default because it is part of the model, not of one instance.

---

## See Also

- [Configuration](configuration.md) — LoggingSettings and the configuration hierarchy
- [Error Handling](error-handling.md) — errors are logged at WARNING + published to MQTT
- [Lifecycle](lifecycle.md) — logging is configured in Phase 1 (Bootstrap)
- [ADR-004 — Logging Strategy](../adr/ADR-004-logging-strategy.md)
